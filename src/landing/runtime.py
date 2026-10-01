"""One Bub SDK execution path for local calls, CI, and the HTTP worker."""

import asyncio
import contextlib
import fcntl
import json
from collections.abc import AsyncIterator, Iterable, Mapping
from pathlib import Path
from typing import cast

from bub import BubFramework, hookimpl
from bub.builtin import Agent
from bub.builtin.hook_impl import BuiltinImpl
from bub.builtin.shell_manager import shell_manager
from bub.builtin.tools import bash, bash_output, fs_edit, fs_read, fs_write, kill_bash, web_fetch
from bub.tools import Tool, ToolContext
from bub.turn import TurnState

from landing.models import Action, ActionRequest, Decision
from landing.prompts import COMMON
from landing.prompts import MODES as PROMPTS
from landing.store import SQLiteTapeStore
from landing.tasks import Tasks


def decide(decision: Decision, *, context: ToolContext) -> str:
    """Record whether the candidate can proceed: allow, block, or inconclusive."""
    context.state["landing_decision"] = decision
    return decision


DECIDE = Tool.from_callable(decide, context=True)
READ_TOOLS = (fs_read, web_fetch)
WRITE_TOOLS = (fs_write, fs_edit, bash, bash_output, kill_bash)


def checks_failed(checks: list[dict]) -> bool:
    return any(item["exit_code"] != 0 or item["timed_out"] for item in checks)


class SDKHooks(BuiltinImpl):
    """Reuse builtin hooks, replacing channel prompts and file-backed sidecars."""

    def __init__(self, framework: BubFramework, tasks: Tasks, store: SQLiteTapeStore) -> None:
        super().__init__(framework)
        self.tasks, self.store = tasks, store

    @hookimpl
    def system_prompt(self, prompt, state) -> str:
        return COMMON + PROMPTS[state["landing_mode"]] + "\n" + self._read_agents_file(state)

    @hookimpl
    def provide_tape_store(self) -> SQLiteTapeStore:
        return self.store

    @hookimpl
    def provide_tape_sidecar(self) -> Tasks:
        return self.tasks


class Runtime:
    def __init__(self, path: Path, *, workspaces: Mapping[str, Path] | None = None, tools: Iterable[Tool] = ()) -> None:
        self.tasks = Tasks(path)
        self.store = SQLiteTapeStore(self.tasks.path)
        self.workspaces = workspaces
        self.framework = BubFramework()
        self.framework.plugin_manager.register(SDKHooks(self.framework, self.tasks, self.store), name="landing")
        self.extra_tools = tuple(tools)
        self.agent = Agent(
            self.framework,
            tools=[*READ_TOOLS, *WRITE_TOOLS, DECIDE, *self.extra_tools],
            tape_store=self.store,
            skill_dirs=(),
        )
        self.active: dict[str, asyncio.Task[Action]] = {}
        self.execution = asyncio.Lock()

    def workspace(self, request: ActionRequest) -> Path:
        if self.workspaces is None:
            path = Path(request.workspace or ".").expanduser().resolve()
        else:
            name = request.workspace or "default"
            if name not in self.workspaces:
                message = f"Workspace {name!r} is not registered."
                raise ValueError(message)
            path = self.workspaces[name].resolve()
        if not path.is_dir():
            message = f"Workspace does not exist: {path}"
            raise ValueError(message)
        return path

    @contextlib.asynccontextmanager
    async def running(self) -> AsyncIterator["Runtime"]:
        # A process-wide worker owns this database. Read-only CLI queries need no lock.
        with self.tasks.path.open("rb") as owner:
            try:
                fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                message = "Another worker owns this database. Use --server or a different --db."
                self.store.close()
                self.tasks.close()
                raise ValueError(message) from exc
            try:
                self.tasks.recover()
                async with self.framework.running():
                    self.control = self.agent.tape.scoped("landing")
                    monitor = asyncio.create_task(self.watch_cancellations())
                    try:
                        yield self
                    finally:
                        monitor.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await monitor
                        await self.stop()
            finally:
                await self.stop()
                self.store.close()
                self.tasks.close()
                fcntl.flock(owner, fcntl.LOCK_UN)

    async def run(self, request: ActionRequest, *, retry_of: str | None = None) -> Action:
        self.workspace(request)
        action, _ = self.tasks.create(request, retry_of=retry_of)
        task = asyncio.create_task(self.execute(action.id))
        self.active[action.id] = task
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            self.cancel(action.id)
            await asyncio.gather(task, return_exceptions=True)
            raise
        finally:
            self.active.pop(action.id, None)

    async def watch_cancellations(self) -> None:
        while True:
            for action_id, task in tuple(self.active.items()):
                if not task.done() and not task.cancelling() and self.tasks.get(action_id).cancel_requested_at:
                    task.cancel()
            await asyncio.sleep(0.1)

    async def checks(self, action_id: str, request: ActionRequest, workspace: Path) -> list[dict]:
        results = []
        for command in request.checks:
            shell = await shell_manager.start(cmd=command, cwd=str(workspace), session_id=action_id)
            timed_out = False
            try:
                async with asyncio.timeout(300):
                    await shell_manager.wait_closed(shell.shell_id)
            except TimeoutError:
                timed_out = True
                await shell_manager.terminate(shell.shell_id)
            item = {"command": command, "exit_code": shell.returncode, "output": shell.output, "timed_out": timed_out}
            self.tasks.event(action_id, "validation", item)
            results.append(item)
        return results

    async def consume(
        self, action_id: str, request: ActionRequest, workspace: Path, checks: list[dict]
    ) -> tuple[str, Decision | None]:
        state: TurnState = {"landing_mode": request.mode, "_runtime_workspace": str(workspace), "code_mode": False}
        prompt = request.instruction or PROMPTS[request.mode]
        for item in request.input:
            prompt += "\n\n" + (item.text if item.type == "text" else f"{item.name}:\n{item.content}")
        if checks:
            prompt += "\n\nValidation results:\n" + json.dumps(checks)
        allowed = [*READ_TOOLS, *self.extra_tools]
        if request.mode == "fixer":
            allowed.extend(WRITE_TOOLS)
        if request.mode == "gatekeeper":
            allowed.append(DECIDE)
        # Content parts bypass Bub's direct-command prefix path.
        stream = await self.agent.run_stream(
            session_id=action_id,
            prompt=[{"type": "text", "text": prompt}],
            state=state,
            allowed_tools=[tool.name for tool in allowed],
        )
        output = ""
        async with contextlib.aclosing(stream):
            async for event in stream:
                if event.kind == "final":
                    output = str(event.data.get("text", output))
                    self.tasks.output(action_id, output)
                elif event.kind == "error":
                    message = str(event.data.get("message", "Agent execution failed."))
                    raise RuntimeError(message)
                elif event.kind in {"tool_call", "tool_result"}:
                    self.tasks.event(action_id, "agent." + event.kind, {"session_id": action_id})
        if stream.error is not None:
            raise RuntimeError(stream.error.message)
        decision = self._decision(request, state, checks)
        self._require_output(action_id, output, decision)
        return output, decision

    @staticmethod
    def _decision(request: ActionRequest, state: TurnState, checks: list[dict]) -> Decision | None:
        if request.mode != "gatekeeper":
            return None
        if checks_failed(checks):
            return "block"
        return cast("Decision", state.get("landing_decision", "inconclusive"))

    def _require_output(self, action_id: str, output: str, decision: Decision | None) -> None:
        """Fail empty/whitespace-only model output instead of reporting it as completed work."""
        if not output.strip():
            # Record any decision reached before the failure so gatekeeper verdicts are not lost.
            self.tasks.output(action_id, output, decision)
            message = "The model returned empty output."
            raise RuntimeError(message)

    async def perform(self, action_id: str) -> tuple[str, Decision | None]:
        request = self.tasks.request(action_id)
        workspace = self.workspace(request)
        checks = await self.checks(action_id, request, workspace) if request.mode == "gatekeeper" else []
        # Finish model-owned background processes before validating its changes.
        async with shell_manager.lifespan():
            output, decision = await self.consume(action_id, request, workspace, checks)
        if request.mode == "gatekeeper" and checks_failed(checks):
            decision = "block"
            output += "\nRequired validation failed; the change cannot proceed."
        self.tasks.output(action_id, output, decision)
        if request.mode == "fixer" and checks_failed(await self.checks(action_id, request, workspace)):
            message = "Required validation failed. Inspect the recorded checks and partial changes."
            raise RuntimeError(message)
        return output, decision

    async def execute(self, action_id: str) -> Action:
        async with self.execution:
            return await self.execute_claimed(action_id)

    async def execute_claimed(self, action_id: str) -> Action:
        if not self.tasks.claim(action_id):
            return self.tasks.get(action_id)
        try:
            async with shell_manager.lifespan():
                output, decision = await self.perform(action_id)
        except asyncio.CancelledError:
            status = "cancelled" if self.tasks.get(action_id).cancel_requested_at else "interrupted"
            self.tasks.finish(action_id, status)
            raise
        except Exception as exc:
            return self.tasks.finish(
                action_id, "failed", error={"code": type(exc).__name__, "message": str(exc) or type(exc).__name__}
            )
        else:
            return self.tasks.finish(action_id, "completed", result=output, decision=decision)

    def cancel(self, action_id: str) -> Action:
        action = self.tasks.cancel(action_id)
        if (task := self.active.get(action_id)) and not task.done() and not task.cancelling():
            task.cancel()
        return action

    async def worker(self) -> None:
        try:
            while True:
                action_id = self.tasks.next()
                if action_id is None:
                    await asyncio.sleep(0.1)
                    continue
                task = asyncio.create_task(self.execute(action_id))
                self.active[action_id] = task
                try:
                    await task
                except asyncio.CancelledError:
                    if (parent := asyncio.current_task()) and parent.cancelling():
                        raise
                finally:
                    self.active.pop(action_id, None)
        finally:
            await self.stop()

    async def stop(self) -> None:
        for task in self.active.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.active.values(), return_exceptions=True)

"""One Bub SDK execution path for local calls, CI, and the HTTP worker."""

import asyncio
import contextlib
import fcntl
import json
import shlex
from collections.abc import AsyncIterator, Callable, Iterable, Mapping
from pathlib import Path
from typing import cast

import bub.builtin.tools  # noqa: F401 -- initialize the native tool registry.
from bub import BubFramework, ensure_config
from bub.builtin.settings import load_session_settings
from bub.builtin.shell_manager import shell_manager
from bub.builtin.tools import resolve_tool_names
from bub.errors import BubError, ErrorKind
from bub.streaming import AsyncStreamEvents, StreamEvent, StreamState
from bub.tools import REGISTRY, Tool, ToolContext
from bub.turn import TurnState

from landing.agent import Agent
from landing.commands import COMMANDS, TOOLS
from landing.hooks import LandingHooks, SDKDefaults
from landing.models import Action, ActionRequest, Decision
from landing.prompts import MODES as PROMPTS
from landing.repository import templates
from landing.settings import ConfigurationFile, ModeSettings, Settings
from landing.store import SQLiteTapeStore
from landing.tasks import Tasks


def decide(decision: Decision, *, context: ToolContext) -> str:
    """Record whether the candidate can proceed: allow, block, or inconclusive."""
    context.state["landing_decision"] = decision
    return decision


DECIDE = Tool.from_callable(decide, context=True)


def checks_failed(checks: list[dict]) -> bool:
    return any(item["exit_code"] != 0 or item["timed_out"] for item in checks)


def task_prompt(request: ActionRequest, workspace: Path, checks: list[dict]) -> list[dict]:
    prompt = request.instruction or PROMPTS[request.mode]
    for item in request.input:
        prompt += "\n\n" + (item.text if item.type == "text" else f"{item.name}:\n{item.content}")
    if checks:
        prompt += "\n\nValidation results:\n" + json.dumps(checks)
    return [{"type": "text", "text": prompt + templates(workspace, request.mode)}]


class Runtime:
    def __init__(
        self,
        path: Path,
        *,
        workspaces: Mapping[str, Path] | None = None,
        tools: Iterable[Tool] = (),
        skill_dirs: Iterable[Path] = (),
        framework: BubFramework | None = None,
        verify: Callable[[Action], None] | None = None,
    ) -> None:
        self.tasks = Tasks(path)
        self.store = SQLiteTapeStore(self.tasks.path)
        self.workspaces = workspaces
        self.verify = verify
        self.framework = framework or BubFramework(config_file=ConfigurationFile().config_file.expanduser())
        self.settings = ensure_config(Settings)
        if framework is None:
            self.framework.plugin_manager.register(SDKDefaults(self.framework), name="builtin")
        self.hooks = LandingHooks(self)
        self.framework.plugin_manager.register(self.hooks, name="landing")
        self.extra_tools = tuple(tools)
        self.skill_dirs = tuple(Path(root).expanduser().resolve() for root in (*skill_dirs, *self.settings.skill_dirs))
        self.agent = Agent(
            self,
            tools=[*REGISTRY.values(), *TOOLS, DECIDE, *self.extra_tools],
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

    async def command(
        self, name: str, request: ActionRequest, *, session_id: str = "cli", scope: str = "cli", key: str | None = None
    ) -> Action:
        """Delegate a user action through its native command tool."""
        if name not in COMMANDS:
            message = f"Unknown command {name!r}. Choose triage, fix, review, or explain."
            raise ValueError(message)
        workspace = self.workspace(request)
        state = await self.framework.build_state({"_runtime_agent": self.agent.bub}, session_id)
        state.update(
            _runtime_workspace=str(workspace),
            landing_request=request.model_dump(),
            landing_scope=scope,
            landing_delivery_key=key,
        )
        prompt = shlex.join([self.agent.bub.command_prefix + name, request.instruction or ""])
        stream = await self.agent.run_stream(session_id=session_id, prompt=prompt, state=state)
        async with contextlib.aclosing(stream):
            async for _ in stream:
                pass
        return self.tasks.get(state["landing_action_id"])

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
        self,
        action_id: str,
        request: ActionRequest,
        workspace: Path,
        checks: list[dict],
        *,
        state: TurnState | None = None,
        events: asyncio.Queue | None = None,
        stream_state: StreamState | None = None,
    ) -> tuple[str, Decision | None]:
        row = self.tasks.connection.execute(
            "SELECT data FROM action_events WHERE action_id=? AND type='sdk.invocation' LIMIT 1", (action_id,)
        ).fetchone()
        invocation = json.loads(row[0]) if row else {}
        session_id = invocation.pop("session_id", action_id)
        supplied_prompt = invocation.pop("prompt", None)
        self.capabilities(request.mode, invocation)
        if state is None:
            state = await load_session_settings(self.agent.tape.session_tape(session_id, workspace))
        state.update(landing_mode=request.mode, _runtime_workspace=str(workspace))
        state.pop("landing_decision", None)
        state.pop("allowed_skills", None)
        # Actions are serialized; discovery and the native skill tool share these per-turn SDK roots.
        self.agent.bub.skill_dirs = (workspace / ".agents/skills", *self.skill_dirs, Path.home() / ".agents/skills")
        # Content parts keep task evidence outside native command dispatch.
        stream = await self.agent.bub.run_stream(
            session_id=session_id,
            prompt=supplied_prompt if supplied_prompt is not None else task_prompt(request, workspace, checks),
            state=state,
            **invocation,
        )
        output = ""
        try:
            async with contextlib.aclosing(stream):
                async for event in stream:
                    if events is not None:
                        events.put_nowait(event)
                    if event.kind == "final":
                        output = str(event.data.get("text", output))
                        self.tasks.output(action_id, output)
                    elif event.kind == "error":
                        message = str(event.data.get("message", "Agent execution failed."))
                        raise RuntimeError(message)
                    elif event.kind in {"tool_call", "tool_result"}:
                        self.tasks.event(action_id, "agent." + event.kind, {"session_id": action_id})
        finally:
            if stream_state is not None:
                stream_state.error, stream_state.usage = stream.error, stream.usage
        if stream.error is not None:
            raise RuntimeError(stream.error.message)
        decision = self._decision(request, state, checks)
        self._require_output(action_id, output, decision)
        return output, decision

    def capabilities(self, mode, invocation) -> None:
        """Apply independent mode limits using the SDK's native tool resolution."""
        limits = self.settings.modes.get(mode, ModeSettings())
        if limits.allowed_tools is not None:
            available = self.agent.bub.tools
            configured = resolve_tool_names(limits.allowed_tools, all_names=available)
            requested = resolve_tool_names(invocation.get("allowed_tools"), all_names=available)
            invocation["allowed_tools"] = sorted(configured & requested)
        if limits.allowed_skills is not None:
            configured_skills = {name.casefold() for name in limits.allowed_skills}
            requested_skills = invocation.get("allowed_skills")
            invocation["allowed_skills"] = sorted(
                configured_skills
                if requested_skills is None
                else configured_skills & {name.casefold() for name in requested_skills}
            )

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

    async def perform(self, action_id: str, **kwargs) -> tuple[str, Decision | None]:
        request = self.tasks.request(action_id)
        workspace = self.workspace(request)
        checks = await self.checks(action_id, request, workspace) if request.mode == "gatekeeper" else []
        # Finish model-owned background processes before validating its changes.
        async with shell_manager.lifespan():
            output, decision = await self.consume(action_id, request, workspace, checks, **kwargs)
        if request.mode == "gatekeeper" and checks_failed(checks):
            decision = "block"
            output += "\nRequired validation failed; the change cannot proceed."
        self.tasks.output(action_id, output, decision)
        if request.mode == "fixer" and checks_failed(await self.checks(action_id, request, workspace)):
            message = "Required validation failed. Inspect the recorded checks and partial changes."
            raise RuntimeError(message)
        return output, decision

    async def execute(self, action_id: str, **kwargs) -> Action:
        async with self.execution:
            return await self.execute_claimed(action_id, **kwargs)

    async def execute_claimed(self, action_id: str, **kwargs) -> Action:
        if not self.tasks.claim(action_id):
            return self.tasks.get(action_id)
        try:
            async with shell_manager.lifespan():
                output, decision = await self.perform(action_id, **kwargs)
            if self.verify is not None:
                self.verify(self.tasks.get(action_id))
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

    def stream(self, action_id: str, *, state: TurnState) -> AsyncStreamEvents:
        """Expose the shared executor's native events, with durable cancellation."""
        events: asyncio.Queue[StreamEvent | None] = asyncio.Queue()
        stream_state = StreamState()
        task: asyncio.Task | None = None

        async def drive():
            try:
                action = await self.execute(action_id, state=state, events=events, stream_state=stream_state)
                if action.error and stream_state.error is None:
                    stream_state.error = BubError(ErrorKind.UNKNOWN, action.error["message"])
                    events.put_nowait(StreamEvent("error", stream_state.error.as_dict()))
            finally:
                events.put_nowait(None)

        async def iterate():
            nonlocal task
            task = asyncio.create_task(drive())
            self.active[action_id] = task
            while (event := await events.get()) is not None:
                yield event
            await task

        async def close():
            if task is None or not task.done():
                self.cancel(action_id)
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
            self.active.pop(action_id, None)

        return AsyncStreamEvents(iterate(), state=stream_state, on_close=close)

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

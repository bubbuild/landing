"""One Bub SDK execution path for local calls, CI, and the HTTP worker."""

import asyncio
import contextlib
import fcntl
import json
from collections.abc import AsyncIterator, Callable, Iterable, Mapping
from pathlib import Path
from typing import cast

import bub.builtin.tools  # noqa: F401 -- initialize the native tool registry.
from bub import BubFramework, ensure_config
from bub.builtin.shell_manager import shell_manager
from bub.builtin.tools import resolve_tool_names
from bub.errors import BubError, ErrorKind
from bub.skills import discover_skills
from bub.streaming import AsyncStreamEvents, StreamEvent, StreamState
from bub.tools import REGISTRY, Tool, ToolContext, tool
from bub.turn import TurnState

from landing.agent import Agent
from landing.commands import COMMANDS
from landing.hooks import install_hooks
from landing.mcp import MCPChannel, connected_tools
from landing.models import Action, ActionRequest, Decision
from landing.prompts import render
from landing.settings import ConfigurationFile, ModeSettings, Settings
from landing.store import SQLiteTapeStore
from landing.tasks import Tasks


@tool(context=True)
def decide(decision: Decision, *, context: ToolContext) -> str:
    """Record whether the candidate can proceed: allow, block, or inconclusive."""
    context.state["landing_decision"] = decision
    return decision


@tool(context=True)
def no_update(reason: str, *, context: ToolContext) -> str:
    """Finish an unchanged issuer follow-up without a public update and record the reason."""
    if context.state.get("landing_mode") != "issuer" or not reason.strip():
        message = "Only issuer can record no update, with a reason."
        raise ValueError(message)
    context.state["landing_no_update"] = reason
    return reason


def checks_failed(checks: list[dict]) -> bool:
    return any(item["exit_code"] != 0 or item["timed_out"] for item in checks)


def task_prompt(request: ActionRequest, checks: list[dict]) -> list[dict]:
    evidence = "\n\n".join(
        item.text if item.type == "text" else render("$name:\n$content", name=item.name, content=item.content)
        for item in request.input
    )
    prompt = render(
        "$instruction\n\n$evidence\n\n$checks",
        instruction=request.instruction or "Carry out the delegated work using the supplied evidence.",
        evidence=evidence,
        checks=render("Validation results:\n$results", results=json.dumps(checks)) if checks else "",
    )
    return [{"type": "text", "text": prompt}]


class Runtime:
    tasks: Tasks
    store: SQLiteTapeStore

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
        self.path = path.expanduser().resolve()
        self.workspaces = workspaces
        self.verify = verify
        self.framework = framework or BubFramework(config_file=ConfigurationFile().config_file.expanduser())
        self.settings = ensure_config(Settings)
        self.hooks = install_hooks(self)
        self.skill_dirs = tuple(Path(root).expanduser().resolve() for root in (*skill_dirs, *self.settings.skill_dirs))
        self.agent = Agent(
            self,
            tools=[*REGISTRY.values(), *tools],
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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+b") as owner:
            try:
                fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                message = "Another worker owns this database. Use --server or a different --db."
                raise ValueError(message) from exc
            try:
                async with self.framework.running():
                    self.tasks.recover()
                    monitor = asyncio.create_task(self.watch_cancellations())
                    try:
                        yield self
                    finally:
                        monitor.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await monitor
                        await self.stop()
            finally:
                fcntl.flock(owner, fcntl.LOCK_UN)

    async def run(self, request: ActionRequest, *, retry_of: str | None = None) -> Action:
        self.workspace(request)
        action, _ = self.tasks.create(request, retry_of=retry_of)
        return await self._wait(action.id)

    async def _wait(self, action_id: str, *, cancel_on_interrupt: bool = True, **kwargs) -> Action:
        task = asyncio.create_task(self.execute(action_id, **kwargs))
        self.active[action_id] = task
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if cancel_on_interrupt:
                self.cancel(action_id)
            else:
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise
        finally:
            self.active.pop(action_id, None)

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
            landing_invocation={"session_id": session_id},
        )
        async with self.execution:
            tape = self.agent.tape.session_tape(session_id, workspace)
            action = await self.agent.bub.tools[name].run(
                instruction=request.instruction or "", context=ToolContext(tape=tape, state=state)
            )
        state.pop("landing_pending_action")
        return await self._wait(action.id, state=state)

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
        mcp: MCPChannel,
        state: TurnState | None = None,
        events: asyncio.Queue | None = None,
        stream_state: StreamState | None = None,
    ) -> tuple[str, Decision | None]:
        invocation = self.tasks.event_data(action_id, "sdk.invocation") or {}
        session_id = invocation.pop("session_id", action_id)
        supplied_prompt = invocation.pop("prompt", None)
        if state is None:
            state = await self.framework.build_state({"_runtime_agent": self.agent.bub}, session_id)
        state.update(landing_action_id=action_id, landing_mode=request.mode, _runtime_workspace=str(workspace))
        state["mcp"] = mcp
        state.pop("landing_decision", None)
        state.pop("landing_llm_call", None)
        state.pop("landing_no_update", None)
        state.pop("landing_tool_failed", None)
        state.pop("allowed_skills", None)
        # Actions are serialized; discovery and the native skill tool share these per-turn SDK roots.
        self.agent.bub.skill_dirs = (
            workspace / ".agents/skills",
            *self.skill_dirs,
            Path.home() / ".agents/skills",
            Path(__file__).with_name("skills"),
        )
        self.capabilities(request.mode, invocation, workspace)
        # Content parts keep task evidence outside native command dispatch.
        stream = await self.agent.bub.run_stream(
            session_id=session_id,
            prompt=supplied_prompt if supplied_prompt is not None else task_prompt(request, checks),
            state=state,
            **invocation,
        )
        output = ""
        try:
            async with contextlib.aclosing(stream):
                async for event in stream:
                    if events is not None:
                        events.put_nowait(event)
                    if event.kind == "final" and "text" in event.data:
                        output = str(event.data["text"])
                        self.tasks.output(action_id, output)
        except Exception as exc:
            self.hooks.record_failure(action_id, state, exc)
            raise
        finally:
            if stream_state is not None:
                stream_state.error, stream_state.usage = stream.error, stream.usage
        if stream.error is not None:
            self.hooks.record_failure(action_id, state, stream.error)
            raise stream.error
        state.pop("landing_llm_call", None)
        decision: Decision | None = None
        if request.mode == "gatekeeper":
            decision = (
                "block" if checks_failed(checks) else cast("Decision", state.get("landing_decision", "inconclusive"))
            )
        if not output.strip():
            self.tasks.output(action_id, output, decision)
            message = "The model returned empty output."
            raise RuntimeError(message)
        self.hooks.record_completion(state)
        return output, decision

    def capabilities(self, mode, invocation, workspace: Path) -> None:
        """Intersect mode and call selections, then exclude unavailable capabilities."""
        limits = self.settings.modes.get(mode, ModeSettings())
        if limits.allowed_tools is not None or limits.excluded_tools:
            available = self.agent.bub.tools
            configured = resolve_tool_names(limits.allowed_tools, exclude=limits.excluded_tools, all_names=available)
            requested = resolve_tool_names(invocation.get("allowed_tools"), all_names=available)
            invocation["allowed_tools"] = sorted(configured & requested)
        if limits.allowed_skills is not None or limits.excluded_skills:
            skills = {item.name.casefold() for item in discover_skills(workspace, skill_dirs=self.agent.bub.skill_dirs)}
            for allowed in (limits.allowed_skills, invocation.get("allowed_skills")):
                if allowed is not None:
                    skills &= {name.casefold() for name in allowed}
            invocation["allowed_skills"] = sorted(skills - {name.casefold() for name in limits.excluded_skills})

    async def perform(self, action_id: str, **kwargs) -> tuple[str, Decision | None]:
        request = self.tasks.request(action_id)
        workspace = self.workspace(request)
        checks = await self.checks(action_id, request, workspace) if request.mode == "gatekeeper" else []
        # Finish model-owned background processes before validating its changes.
        async with (
            shell_manager.lifespan(),
            connected_tools(self.agent.bub, workspace) as channel,
        ):
            output, decision = await self.consume(action_id, request, workspace, checks, mcp=channel, **kwargs)
        if request.mode == "gatekeeper" and checks_failed(checks):
            output += "\nRequired validation failed; the change cannot proceed."
        self.tasks.output(action_id, output, decision)
        if request.mode == "fixer" and checks_failed(await self.checks(action_id, request, workspace)):
            message = "Required validation failed. Inspect the recorded checks and partial changes."
            raise RuntimeError(message)
        return output, decision

    async def execute(self, action_id: str, **kwargs) -> Action:
        async with self.execution:
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
                    action_id,
                    "failed",
                    error={
                        "code": exc.kind.value if isinstance(exc, BubError) else ErrorKind.UNKNOWN.value,
                        "message": exc.message if isinstance(exc, BubError) else str(exc) or type(exc).__name__,
                    },
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
                action = await self._wait(action_id, state=state, events=events, stream_state=stream_state)
                if action.error and stream_state.error is None:
                    stream_state.error = BubError(ErrorKind.UNKNOWN, action.error["message"])
                    events.put_nowait(StreamEvent("error", stream_state.error.as_dict()))
            finally:
                events.put_nowait(None)

        async def iterate():
            nonlocal task
            task = asyncio.create_task(drive())
            while (event := await events.get()) is not None:
                yield event
            await task

        async def close():
            if task is None or not task.done():
                self.cancel(action_id)
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)

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
                try:
                    await self._wait(action_id, cancel_on_interrupt=False)
                except asyncio.CancelledError:
                    if (parent := asyncio.current_task()) and parent.cancelling():
                        raise
        finally:
            await self.stop()

    async def stop(self) -> None:
        for task in self.active.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.active.values(), return_exceptions=True)

"""One Bub SDK execution path for local calls, CI, and the HTTP worker."""

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Callable, Collection, Iterable, Mapping
from pathlib import Path
from typing import cast

from bub import BubFramework, ensure_config
from bub.builtin import Agent as BubAgent
from bub.builtin.environment import environment_from_state
from bub.builtin.shell_manager import shell_manager
from bub.builtin.tools import resolve_tool_names
from bub.environment import Environment
from bub.errors import BubError, ErrorKind
from bub.skills import discover_skills
from bub.streaming import AsyncStreamEvents, StreamEvent, StreamState
from bub.tools import REGISTRY, Tool, ToolContext, tool
from bub.turn import TurnState
from pydantic import ValidationError

from landing.database import database_engine
from landing.hooks import LandingHooks
from landing.mcp import connected_tools
from landing.models import Action, ActionRequest, Decision, Mode
from landing.settings import ConfigurationFile, ModeSettings, Settings
from landing.store import SQLiteTapeStore
from landing.tasks import Tasks


@tool(context=True)
def decide(decision: Decision, *, context: ToolContext) -> str:
    """Record a review recommendation: allow, block, or inconclusive."""
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
    parts = [request.instruction or "Carry out the delegated work using the supplied evidence."]
    parts.extend(item.text if item.type == "text" else f"{item.name}:\n{item.content}" for item in request.input)
    if checks:
        parts.append("Validation results:\n" + json.dumps(checks))
    return [{"type": "text", "text": "\n\n".join(parts)}]


class Runtime:
    settings: Settings
    execution: asyncio.Lock
    pending: asyncio.Event

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
        settings = ensure_config(Settings)
        if workspaces is not None and "default" in workspaces:
            self.framework.workspace = workspaces["default"].expanduser().resolve()
        manager = self.framework.plugin_manager
        hooks = manager.get_plugin("landing")
        if hooks is None:
            hooks = LandingHooks(self.framework)
            manager.register(hooks, name="landing")
        self.engine = database_engine(self.path)
        self.tasks = Tasks(self.engine)
        self.settings = settings
        self.tools = {**REGISTRY, **{item.name: item for item in tools}}
        self.tape_store = SQLiteTapeStore(self.engine)
        self.skill_roots = tuple(Path(root).expanduser().resolve() for root in (*skill_dirs, *settings.skill_dirs))
        hooks.runtime = self
        self.active: dict[str, asyncio.Task[Action]] = {}
        self.worker_task: asyncio.Task[None] | None = None

    def create_agent(self, workspace: Path) -> BubAgent:
        agent = BubAgent(
            self.framework,
            tools=list(self.tools.values()),
            tape_store=self.tape_store,
            skill_dirs=(
                workspace / ".agents/skills",
                *self.skill_roots,
                Path.home() / ".agents/skills",
                Path(__file__).with_name("skills"),
            ),
            command_prefix=self.settings.command_prefix,
        )
        # Keep the Runtime configuration when another host loads process-wide settings.
        agent.settings = agent.model_runner.settings = self.settings
        return agent

    async def run_stream(
        self,
        *,
        session_id: str,
        prompt: str | list[dict],
        mode: Mode = "explainer",
        state: TurnState | None = None,
        model: str | None = None,
        allowed_skills: "Collection[str] | None" = None,
        allowed_tools: "Collection[str] | None" = None,
        reasoning_effort: str | None = None,
    ) -> AsyncStreamEvents:
        workspace = (
            self.workspace_name(state["_runtime_workspace"]) if state and "_runtime_workspace" in state else None
        )
        self.workspace(workspace)
        request = ActionRequest(
            mode=mode,
            instruction=prompt
            if isinstance(prompt, str)
            else "Carry out the delegated work using the supplied evidence.",
            workspace=workspace,
        )
        action, _ = self.tasks.create(
            request,
            event=(
                "sdk.invocation",
                {
                    "session_id": session_id,
                    "prompt": [{"type": "text", "text": prompt}] if isinstance(prompt, str) else prompt,
                    "model": model,
                    "allowed_skills": list(allowed_skills) if allowed_skills is not None else None,
                    "allowed_tools": list(allowed_tools) if allowed_tools is not None else None,
                    "reasoning_effort": reasoning_effort,
                },
            ),
        )
        return self.stream(action.id, state=state)

    def workspace(self, selected: str | None = None) -> Path:
        if self.workspaces is None:
            path = Path(selected).expanduser().resolve() if selected else self.framework.workspace
        else:
            name = selected or "default"
            if name not in self.workspaces:
                message = f"Workspace {name!r} is not registered."
                raise ValueError(message)
            path = self.workspaces[name].resolve()
        if not path.is_dir():
            message = f"Workspace does not exist: {path}"
            raise ValueError(message)
        return path

    def workspace_name(self, path: str | Path) -> str:
        resolved = Path(path).expanduser().resolve()
        if self.workspaces is None:
            return str(resolved)
        for name, registered in self.workspaces.items():
            if registered.resolve() == resolved:
                return name
        message = f"Workspace {str(resolved)!r} is not registered."
        raise ValueError(message)

    @contextlib.asynccontextmanager
    async def running(self, *, background: bool = False) -> AsyncIterator["Runtime"]:
        """Run work within Bub's resources, stopping execution before they close."""
        async with self.framework.running():
            self.worker_task = asyncio.create_task(self.worker()) if background else None
            try:
                yield self
            finally:
                await self.stop()

    @contextlib.asynccontextmanager
    async def lifespan(self, app: object) -> AsyncIterator[None]:
        """ASGI lifespan callback that owns background execution."""
        async with self.running(background=True):
            yield

    def submit(
        self, request: ActionRequest, *, scope: str = "sdk", key: str | None = None, retry_of: str | None = None
    ) -> tuple[Action, bool]:
        """Persist work for a running background host and return its receipt."""
        if self.worker_task is None or self.worker_task.done() or self.worker_task.cancelling():
            message = "The worker is unavailable."
            raise RuntimeError(message)
        self.workspace(request.workspace)
        action, created = self.tasks.create(request, scope=scope, key=key, retry_of=retry_of)
        self.pending.set()
        return action, created

    async def run(
        self,
        request: ActionRequest,
        *,
        retry_of: str | None = None,
        session_id: str | None = None,
        scope: str = "sdk",
        key: str | None = None,
        verify: Callable[[Action], None] | None = None,
    ) -> Action:
        self.workspace(request.workspace)
        action, _ = self.tasks.create(
            request,
            scope=scope,
            key=key,
            retry_of=retry_of,
            event=("sdk.invocation", {"session_id": session_id}) if session_id is not None else None,
        )
        return await self._wait(action.id, verify=verify)

    async def _wait(self, action_id: str, **kwargs) -> Action:
        async with contextlib.aclosing(self.stream(action_id, **kwargs)) as stream:
            async for _ in stream:
                pass
        return self.tasks.get(action_id)

    async def checks(self, action_id: str, request: ActionRequest, environment: Environment) -> list[dict]:
        results = []
        for command in request.checks:
            shell = await shell_manager.start(cmd=command, cwd=None, session_id=action_id, environment=environment)
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

    def capabilities(self, mode, invocation, workspace: Path, agent: BubAgent) -> None:
        """Intersect mode and call selections, then exclude unavailable capabilities."""
        limits = self.settings.modes.get(mode, ModeSettings())
        if limits.allowed_tools is not None or limits.excluded_tools:
            available = agent.tools
            configured = resolve_tool_names(limits.allowed_tools, exclude=limits.excluded_tools, all_names=available)
            requested = resolve_tool_names(invocation.get("allowed_tools"), all_names=available)
            invocation["allowed_tools"] = sorted(configured & requested)
        if limits.allowed_skills is not None or limits.excluded_skills:
            skills = {item.name.casefold() for item in discover_skills(workspace, skill_dirs=agent.skill_dirs)}
            for allowed in (limits.allowed_skills, invocation.get("allowed_skills")):
                if allowed is not None:
                    skills &= {name.casefold() for name in allowed}
            invocation["allowed_skills"] = sorted(skills - {name.casefold() for name in limits.excluded_skills})

    async def execute(  # noqa: C901 -- modes share one execution transaction and shutdown order.
        self,
        action_id: str,
        *,
        state: TurnState | None = None,
        verify: Callable[[Action], None] | None = None,
        events: asyncio.Queue,
        stream_state: StreamState,
    ) -> Action:
        async with self.execution:
            if not self.tasks.claim(action_id):
                return self.tasks.get(action_id)
            try:
                request = self.tasks.request(action_id)
                workspace = self.workspace(request.workspace)
                invocation = self.tasks.event_data(action_id, "sdk.invocation") or {}
                session_id = invocation.pop("session_id", action_id)
                prompt = invocation.pop("prompt", None)
                agent = self.create_agent(workspace)
                if state is None:
                    state = await self.framework.build_state(
                        {"_runtime_agent": agent, "_runtime_workspace": str(workspace)}, session_id
                    )
                state.update(landing_action_id=action_id, landing_mode=request.mode, _runtime_workspace=str(workspace))
                for key in (
                    "landing_decision",
                    "landing_llm_call",
                    "landing_no_update",
                    "landing_tool_failed",
                    "allowed_skills",
                ):
                    state.pop(key, None)
                environment = environment_from_state(state)
                async with shell_manager.lifespan():
                    checks = await self.checks(action_id, request, environment) if request.mode == "gatekeeper" else []
                    # Close model-owned processes and MCP connections before post-fix validation.
                    async with shell_manager.lifespan(), connected_tools(agent, workspace) as channel:
                        state["mcp"] = channel
                        self.capabilities(request.mode, invocation, workspace, agent)
                        stream = await agent.run_stream(
                            session_id=session_id,
                            prompt=prompt if prompt is not None else task_prompt(request, checks),
                            state=state,
                            **invocation,
                        )
                        output = ""
                        try:
                            async with contextlib.aclosing(stream):
                                async for event in stream:
                                    events.put_nowait(event)
                                    if event.kind == "final" and "text" in event.data:
                                        output = str(event.data["text"])
                                        self.tasks.output(action_id, output)
                        finally:
                            stream_state.error, stream_state.usage = stream.error, stream.usage
                        if stream.error is not None:
                            raise stream.error  # noqa: TRY301 -- persist the failed task at this boundary.
                    state.pop("landing_llm_call", None)
                    decision = None
                    if request.mode == "gatekeeper":
                        decision = (
                            "block"
                            if checks_failed(checks)
                            else cast("Decision", state.get("landing_decision", "inconclusive"))
                        )
                    if not output.strip():
                        self.tasks.output(action_id, output, decision)
                        message = "The model returned empty output."
                        raise RuntimeError(message)  # noqa: TRY301 -- persist the failed task at this boundary.
                    if not state.get("landing_tool_failed") and (reason := state.get("landing_no_update")):
                        self.tasks.event(action_id, "issue.unchanged", {"reason": reason})
                    if request.mode == "gatekeeper" and checks_failed(checks):
                        output += "\nRequired validation failed; the change cannot proceed."
                    self.tasks.output(action_id, output, decision)
                    if request.mode == "fixer" and checks_failed(await self.checks(action_id, request, environment)):
                        message = "Required validation failed. Inspect the recorded checks and partial changes."
                        raise RuntimeError(message)  # noqa: TRY301 -- persist the failed task at this boundary.
                for verifier in (self.verify, verify):
                    if verifier is not None:
                        verifier(self.tasks.get(action_id))
            except asyncio.CancelledError:
                status = "cancelled" if self.tasks.get(action_id).cancel_requested_at else "interrupted"
                self.tasks.finish(action_id, status)
                raise
            except Exception as exc:
                if state is not None and (last_call := state.pop("landing_llm_call", None)):
                    diagnostic = {"last_llm_call": last_call}
                    if isinstance(exc.__cause__, ValidationError):
                        diagnostic["validation_errors"] = [
                            item["type"] for item in exc.__cause__.errors(include_input=False)
                        ]
                    self.tasks.event(action_id, "model.failure", diagnostic)
                error = (
                    exc if isinstance(exc, BubError) else BubError(ErrorKind.UNKNOWN, str(exc) or type(exc).__name__)
                )
                if stream_state.error is None:
                    stream_state.error = error
                    events.put_nowait(StreamEvent("error", error.as_dict()))
                return self.tasks.finish(
                    action_id, "failed", error={"code": error.kind.value, "message": error.message}
                )
            else:
                return self.tasks.finish(action_id, "completed", result=output, decision=decision)

    def stream(self, action_id: str, *, cancel_on_close: bool = True, **kwargs) -> AsyncStreamEvents:
        """Expose the shared executor's native events, with durable cancellation."""
        events: asyncio.Queue[StreamEvent | None] = asyncio.Queue()
        stream_state = StreamState()
        task: asyncio.Task | None = None

        async def iterate():
            nonlocal task
            task = asyncio.create_task(self.execute(action_id, events=events, stream_state=stream_state, **kwargs))
            self.active[action_id] = task
            task.add_done_callback(lambda _: self.active.pop(action_id, None))
            task.add_done_callback(lambda _: events.put_nowait(None))
            while (event := await events.get()) is not None:
                yield event
            await task

        async def close():
            if task is None or not task.done():
                if cancel_on_close:
                    self.cancel(action_id)
                elif task is not None:
                    task.cancel()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)

        return AsyncStreamEvents(iterate(), state=stream_state, on_close=close)

    def cancel(self, action_id: str) -> Action:
        action = self.tasks.cancel(action_id)
        if (task := self.active.get(action_id)) and not task.done() and not task.cancelling():
            task.cancel()
        return action

    async def worker(self) -> None:
        while True:
            self.pending.clear()
            while (action_id := self.tasks.next()) is not None:
                try:
                    await self._wait(action_id, cancel_on_close=False)
                except asyncio.CancelledError:
                    if (parent := asyncio.current_task()) and parent.cancelling():
                        raise
            await self.pending.wait()

    async def stop(self) -> None:
        tasks = [*self.active.values(), *([self.worker_task] if self.worker_task is not None else [])]
        for task in tasks:
            if not task.done() and not task.cancelling():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

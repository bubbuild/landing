"""One Bub SDK execution path for local calls, CI, and the HTTP worker."""

import asyncio
import contextlib
import json
from collections.abc import AsyncGenerator, Callable, Iterable, Mapping
from pathlib import Path
from typing import cast

from bub import BubFramework, ensure_config, tool
from bub.builtin import tools as builtin_tools
from bub.builtin.agent import BubAgent
from bub.builtin.environment import environment_from_state
from bub.builtin.shell_manager import shell_manager
from bub.builtin.spill import SpillSettings, SpillStore, spill_read
from bub.builtin.tools import resolve_tool_names
from bub.core import Agent, PromptContext
from bub.core.tape import session_tape_name
from bub.core.tools import ToolContext
from bub.environment import Environment
from bub.errors import BubError, ErrorKind
from bub.skills import SkillMetadata, discover_skills, iter_skill_roots
from bub.turn import TurnState
from pydantic import ValidationError

from landing.database import database_engine
from landing.hooks import LandingHooks, skill
from landing.mcp import connected_tools
from landing.models import Action, ActionRequest, Decision
from landing.prompts import system_prompt
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


def task_prompt(request: ActionRequest, checks: list[dict]) -> str:
    parts = [request.instruction or "Carry out the delegated work using the supplied evidence."]
    parts.extend(item.text if item.type == "text" else f"{item.name}:\n{item.content}" for item in request.input)
    if request.mode == "bumper" and request.checks:
        parts.append("Required final check commands:\n" + json.dumps(request.checks))
    if checks:
        parts.append("Validation results:\n" + json.dumps(checks))
    return "\n\n".join(parts)


class Runtime:
    settings: Settings
    execution: asyncio.Lock
    pending: asyncio.Event

    def __init__(
        self,
        path: Path,
        *,
        workspaces: Mapping[str, Path] | None = None,
        skill_dirs: Iterable[Path] = (),
        plugins: Iterable[object] = (),
        verify: Callable[[Action], None] | None = None,
    ) -> None:
        self.path = path.expanduser().resolve()
        self.workspaces = workspaces
        self.verify = verify
        self.framework = BubFramework(config_file=ConfigurationFile().config_file.expanduser())
        settings = ensure_config(Settings)
        if workspaces is not None and "default" in workspaces:
            self.framework.workspace = workspaces["default"].expanduser().resolve()
        self.engine = database_engine(self.path)
        self.tasks = Tasks(self.engine)
        self.settings = settings
        self.tools = [
            builtin_tools.bash,
            builtin_tools.bash_output,
            builtin_tools.kill_bash,
            builtin_tools.fs_read,
            builtin_tools.fs_write,
            builtin_tools.fs_edit,
            builtin_tools.tape_info,
            builtin_tools.tape_search,
            builtin_tools.tape_reset,
            builtin_tools.tape_handoff,
            builtin_tools.tape_anchors,
            builtin_tools.web_fetch,
            spill_read,
            skill,
            decide,
            no_update,
        ]
        self.tape_store = SQLiteTapeStore(self.engine)
        self.spill_store = SpillStore(ensure_config(SpillSettings))
        self.skill_roots = tuple(Path(root).expanduser().resolve() for root in (*skill_dirs, *settings.skill_dirs))
        for plugin in [LandingHooks(self), *plugins]:
            self.framework.plugin_manager.register(plugin)
        self.active: dict[str, asyncio.Task[Action]] = {}
        self.worker_task: asyncio.Task[None] | None = None

    def create_agent(self) -> Agent:
        agent = cast("Agent", BubAgent(self.framework, tools=self.tools, skill_dirs=()))
        agent.name = "landing"
        agent.max_steps = self.settings.max_steps
        # Delegated text remains model input, even when it starts with Bub's command prefix.
        agent.command_prefix = None
        agent.system_prompt = system_prompt(self.settings)

        @agent.system_prompt.add
        def plugin_prompt(context: PromptContext) -> str:
            return self.framework.get_system_prompt(context.prompt, context.state)

        return agent

    def skills(self, workspace: Path) -> list[SkillMetadata]:
        roots = [root for root, _ in iter_skill_roots(workspace)]
        return discover_skills(workspace, skill_dirs=[roots[0], *self.skill_roots, *roots[1:]])

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
    async def running(self, *, background: bool = False) -> AsyncGenerator["Runtime"]:
        """Run work within Bub's resources, stopping execution before they close."""
        async with self.framework.running():
            self.worker_task = asyncio.create_task(self.worker()) if background else None
            try:
                yield self
            finally:
                await self.stop()

    @contextlib.asynccontextmanager
    async def lifespan(self, app: object) -> AsyncGenerator[None]:
        """ASGI lifespan callback that owns background execution."""
        async with self.running(background=True):
            yield

    def submit(
        self, request: ActionRequest, *, scope: str = "local", key: str | None = None, retry_of: str | None = None
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
        scope: str = "local",
        key: str | None = None,
        verify: Callable[[Action], None] | None = None,
    ) -> Action:
        self.workspace(request.workspace)
        action, _ = self.tasks.create(
            request,
            scope=scope,
            key=key,
            retry_of=retry_of,
            event=("execution", {"session_id": session_id}) if session_id is not None else None,
        )
        return await self._wait(action.id, verify=verify)

    async def _wait(self, action_id: str, *, cancel_on_interrupt: bool = True, **kwargs) -> Action:
        task = self.active.get(action_id)
        if task is None:
            task = asyncio.create_task(self.execute(action_id, **kwargs))
            self.active[action_id] = task
            task.add_done_callback(lambda _: self.active.pop(action_id, None))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if cancel_on_interrupt:
                self.cancel(action_id)
            else:
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise

    async def checks(self, action_id: str, request: ActionRequest, environment: Environment) -> list[dict]:
        results = []
        # Own check processes so cancellation cannot leave them running until the host exits.
        async with shell_manager.lifespan():
            for command in request.checks:
                shell = await shell_manager.start(cmd=command, cwd=None, session_id=action_id, environment=environment)
                timed_out = False
                try:
                    async with asyncio.timeout(300):
                        await shell_manager.wait_closed(shell.shell_id)
                except TimeoutError:
                    timed_out = True
                    await shell_manager.terminate(shell.shell_id)
                item = {
                    "command": command,
                    "exit_code": shell.returncode,
                    "output": shell.output,
                    "timed_out": timed_out,
                }
                self.tasks.event(action_id, "validation", item)
                results.append(item)
        return results

    def capabilities(self, mode, workspace: Path, agent: Agent, state: TurnState) -> None:
        """Select the mode's tools and skills after runtime discovery."""
        limits = self.settings.modes.get(mode, ModeSettings())
        available = agent.tools
        configured = resolve_tool_names(limits.allowed_tools, exclude=limits.excluded_tools, all_names=available)
        agent.tools = {name: item for name, item in available.items() if name in configured}
        skills = {item.name.casefold(): item for item in self.skills(workspace)}
        if limits.allowed_skills is not None:
            names = {name.casefold() for name in limits.allowed_skills}
            skills = {name: item for name, item in skills.items() if name in names}
        excluded = {name.casefold() for name in limits.excluded_skills}
        state["landing_skills"] = {name: item for name, item in skills.items() if name not in excluded}

    async def execute(  # noqa: C901 -- modes share one execution transaction and shutdown order.
        self,
        action_id: str,
        *,
        verify: Callable[[Action], None] | None = None,
    ) -> Action:
        state: TurnState | None = None
        async with self.execution:
            if not self.tasks.claim(action_id):
                return self.tasks.get(action_id)
            default_workspace = self.framework.workspace
            try:
                request = self.tasks.request(action_id)
                workspace = self.workspace(request.workspace)
                self.framework.workspace = workspace
                invocation = self.tasks.event_data(action_id, "execution") or {}
                session_id = invocation.pop("session_id", action_id)
                agent = self.create_agent()
                state = await self.framework.build_state(
                    {"_runtime_workspace": str(workspace), "_runtime_agent": agent}, session_id
                )
                state.update(landing_action_id=action_id, landing_mode=request.mode, _runtime_workspace=str(workspace))
                for key in (
                    "landing_decision",
                    "landing_llm_call",
                    "landing_no_update",
                    "landing_tool_failed",
                ):
                    state.pop(key, None)
                environment = environment_from_state(state)
                checks = await self.checks(action_id, request, environment) if request.mode == "gatekeeper" else []
                # Close model-owned processes and MCP connections before post-edit validation.
                async with shell_manager.lifespan(), connected_tools(agent, workspace) as channel:
                    state["mcp"] = channel
                    self.capabilities(request.mode, workspace, agent, state)
                    output = ""
                    async with agent.run(
                        task_prompt(request, checks),
                        tape=session_tape_name(session_id, workspace),
                        model=self.settings.chat_model(
                            state.get("model"), reasoning_effort=state.get("reasoning_effort")
                        ),
                        state=state,
                    ) as stream:
                        async for event in stream:
                            if event.kind == "final" and "text" in event.data:
                                output = str(event.data["text"])
                                self.tasks.output(action_id, output)
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
                if request.mode in {"fixer", "bumper"} and checks_failed(
                    await self.checks(action_id, request, environment)
                ):
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
                return self.tasks.finish(
                    action_id, "failed", error={"code": error.kind.value, "message": error.message}
                )
            else:
                return self.tasks.finish(action_id, "completed", result=output, decision=decision)
            finally:
                self.framework.workspace = default_workspace

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
                    await self._wait(action_id, cancel_on_interrupt=False)
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

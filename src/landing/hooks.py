"""Bub hooks adapt Landing's business state and execution to the message pipeline."""

import asyncio
from importlib.metadata import distribution
from pathlib import Path
from typing import TYPE_CHECKING

from bub import hookimpl
from bub.builtin.hook_impl import BuiltinImpl
from bub.builtin.settings import load_session_settings
from bub.envelope import field_of
from bub.hooks.interception import LlmCallDecision
from bub.skills import discover_skills
from bub.utils import workspace_from_state

from landing.database import open_database, own_database
from landing.prompts import COMMON, SYSTEM, render
from landing.settings import ModeSettings

if TYPE_CHECKING:
    from landing.runtime import Runtime


class LandingHooks(BuiltinImpl):
    """Native defaults and business hooks shared by hook and SDK calls."""

    runtime: "Runtime"

    def __init__(self, framework) -> None:
        super().__init__(framework)
        for entry in distribution("landing").entry_points.select(group="landing.adapters"):
            framework.plugin_manager.register(entry.load(), name=entry.name)

    @hookimpl
    def register_cli_commands(self, app):
        from landing import cli

        for name, help_text in {
            "triage": "Identify a problem and its acceptance criteria.",
            "fix": "Repair the delegated problem and validate changes.",
            "review": "Evaluate a candidate against independent evidence.",
            "explain": "Explain the supplied question or evidence.",
        }.items():
            app.command(name=name, cls=cli.Command, help=help_text)(cli.delegate)
        app.add_typer(cli.actions, name="action")
        app.command(cls=cli.Command)(cli.serve)

    @hookimpl(trylast=True)
    def provide_environment(self, session_id, workspace):
        # Native tools resolve the state workspace unless a host supplies a session environment.
        return None

    @hookimpl
    async def load_state(self, message, session_id):
        if lifespan := field_of(message, "lifespan"):
            await lifespan.__aenter__()
        agent = field_of(message, "_runtime_agent") or self.runtime
        workspace = Path(field_of(message, "_runtime_workspace", self.framework.workspace)).expanduser().resolve()
        tape = agent.tape.session_tape(session_id, workspace)
        state = {
            "session_id": session_id,
            "_runtime_agent": agent,
            "_runtime_workspace": str(workspace),
            "landing_mode": "explainer",
            **await load_session_settings(tape),
        }
        for entry in await tape.store.fetch_all(tape.query().kinds("event")):
            if entry.payload.get("name") == "landing_mode_switch":
                state["landing_mode"] = entry.payload["data"]["landing_mode"]
        if context := field_of(message, "context_str"):
            state["context"] = context
        context = field_of(message, "context", {})
        if model := context.get("model"):
            state["model"] = model
        if thread := context.get("thread_id"):
            state["_runtime_thread_id"] = thread
        return state

    @hookimpl
    def system_prompt(self, prompt, state) -> str:
        workspace = workspace_from_state(state)
        selected = state.get("landing_mode", "explainer")
        allowed = state.get("allowed_skills")
        skill = next(
            (
                item
                for item in discover_skills(workspace, skill_dirs=self.runtime.skill_dirs)
                if item.name == f"landing-{selected}" and (allowed is None or item.name in allowed)
            ),
            None,
        )
        return render(
            SYSTEM,
            common=COMMON,
            selected=selected,
            mode=skill.body() if skill else "",
            instructions=self.runtime.settings.modes.get(selected, ModeSettings()).instructions,
            workspace=workspace,
            repository=self._read_agents_file(state),
        )

    @hookimpl
    def before_llm_call(self, request, state):
        if state.get("landing_tool_failed"):
            return None
        if reason := state.get("landing_no_update"):
            return LlmCallDecision.finish(reason)

    @hookimpl(specname="after_tool_call")
    def observe_tool_result(self, call, result, state) -> None:
        if result.error is not None:
            state["landing_tool_failed"] = True

    @hookimpl
    def after_llm_call(self, request, result, state) -> None:
        # The SDK calls this before parsing tool arguments. Retain metadata, never their contents.
        state["landing_llm_call"] = {
            "run_id": result.run_id,
            "tools": [
                name if (name := call.get("function", {}).get("name")) in request.tool_names else "unknown"
                for call in result.tool_calls
            ],
        }

    @hookimpl(specname="provide_lifespan")
    async def storage_lifespan(self):
        with own_database(self.runtime.path), open_database(self.runtime.engine):
            self.runtime.execution = asyncio.Lock()
            self.runtime.pending = asyncio.Event()
            self.runtime.tasks.recover()
            yield

    @hookimpl
    def provide_tape_store(self):
        return self.runtime.tape_store

    @hookimpl(specname="provide_tape_sidecar")
    def task_sidecar(self):
        return self.runtime.tasks

"""Bub hooks supply Landing task guidance, state, and storage."""

import asyncio
import re
from importlib.metadata import distribution
from pathlib import Path
from typing import TYPE_CHECKING

from bub import hookimpl
from bub.builtin.hook_impl import BuiltinImpl
from bub.builtin.settings import load_session_settings
from bub.builtin.spill import spill_read
from bub.builtin.tools import TOOLS
from bub.core.events import LLMCallDecision
from bub.core.tape import session_tape_name
from bub.core.tools import ToolContext, tool
from bub.envelope import field_of
from bub.prompt import to_content
from bub.skills import render_skills_prompt
from bub.utils import workspace_from_state

from landing.database import open_database, own_database
from landing.prompts import COMMON, SYSTEM, render
from landing.settings import ModeSettings

if TYPE_CHECKING:
    from landing.runtime import Runtime


@tool(context=True, name="skill")
def skill(name: str | None = None, *, context: ToolContext) -> dict:
    """List the task's selected skills, or read one skill's instructions."""
    skills = context.state.get("landing_skills", {})
    if name is None:
        return {"skills": list(skills)}
    if selected := skills.get(name.casefold()):
        return {"name": selected.name, "location": str(selected.location), "content": selected.body()}
    return {"error": f"skill '{name}' is unavailable in this task"}


class LandingHooks:
    """Compose selected native hooks with Landing business hooks."""

    runtime: "Runtime"

    def __init__(self, framework) -> None:
        self.framework = framework
        defaults = BuiltinImpl(framework)
        # Reuse native tape context and tool interception without a message host.
        self.provide_tape_sidecar = defaults.provide_tape_sidecar
        self.build_tape_context = defaults.build_tape_context
        self.before_tool_call = defaults.before_tool_call
        self.after_tool_call = defaults.after_tool_call
        for entry in distribution("landing").entry_points.select(group="landing.adapters"):
            framework.plugin_manager.register(entry.load(), name=entry.name)

    @hookimpl
    def provide_tools(self):
        # App commands, subagents and deferred loading require BubAgent.
        tools = [
            item
            for item in TOOLS
            if item.exposure in {"auto", "direct"} and item.name not in {"skill", "subagent", "tool.describe"}
        ]
        return [*tools, spill_read, skill, *self.runtime.tools.values()]

    @hookimpl
    def build_prompt(self, message):
        return to_content(field_of(message, "content", ""))

    @hookimpl
    async def load_state(self, message, session_id):
        workspace = Path(field_of(message, "_runtime_workspace", self.framework.workspace)).expanduser().resolve()
        agent = field_of(message, "_runtime_agent")
        tape = agent.tape(session_tape_name(session_id, workspace))
        return {
            "session_id": session_id,
            "_runtime_agent": agent,
            "_runtime_workspace": str(workspace),
            **await load_session_settings(tape),
        }

    @hookimpl
    def system_prompt(self, prompt, state) -> str:
        workspace = workspace_from_state(state)
        selected = state.get("landing_mode", "explainer")
        skills = state.get("landing_skills", {})
        mode_skill = skills.get(f"landing-{selected}")
        repository = workspace / "AGENTS.md"
        try:
            instructions = repository.read_text(encoding="utf-8").strip()
        except OSError:
            instructions = ""
        guidance = render(
            SYSTEM,
            common=COMMON,
            selected=selected,
            mode=mode_skill.body() if mode_skill else "",
            instructions=self.runtime.settings.modes.get(selected, ModeSettings()).instructions,
            workspace=workspace,
            repository=instructions,
        )
        expanded = set(re.findall(r"\$([A-Za-z0-9_.-]+)", str(prompt))) & skills.keys()
        return "\n\n".join(
            filter(None, (guidance, render_skills_prompt(list(skills.values()), expanded_skills=expanded)))
        )

    @hookimpl
    def before_llm_call(self, request, state):
        if not state.get("landing_tool_failed") and (reason := state.get("landing_no_update")):
            return LLMCallDecision.finish(reason)

    @hookimpl(specname="after_tool_call")
    def observe_tool_result(self, call, result, state) -> None:
        if result.error is not None:
            state["landing_tool_failed"] = True

    @hookimpl
    def after_llm_call(self, request, result, state) -> None:
        # Failure diagnostics retain call metadata without tool arguments.
        state["landing_llm_call"] = {
            "run_id": result.run_id,
            "tools": [
                name if (name := call.get("name")) in request.tool_names else "unknown" for call in result.tool_calls
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

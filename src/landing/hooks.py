"""Bub hooks supply Landing task guidance, state, and storage."""

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from bub import hookimpl, tool
from bub.builtin.settings import load_session_settings
from bub.builtin.spill import SPILL_READ_MODEL_NAME, SPILL_SIDECAR_NAME, SpillStore
from bub.core.context import default_tape_context
from bub.core.events import LLMCallDecision, ToolCall, ToolCallResult
from bub.core.tape import session_tape_name
from bub.core.tools import ToolContext, content_result, result_content
from bub.envelope import field_of
from bub.errors import BubError
from bub.turn import TurnState

from landing.database import open_database, own_database

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
    """Supply Landing policy and resources through native agent hooks."""

    def __init__(self, runtime: "Runtime") -> None:
        self.runtime = runtime

    @hookimpl
    def build_tape_context(self):
        return default_tape_context()

    @hookimpl
    async def load_state(self, message, session_id):
        workspace = (
            Path(field_of(message, "_runtime_workspace", self.runtime.framework.workspace)).expanduser().resolve()
        )
        agent = field_of(message, "_runtime_agent")
        tape = agent.tape(session_tape_name(session_id, workspace))
        return {
            "session_id": session_id,
            "_runtime_agent": agent,
            "_runtime_workspace": str(workspace),
            **await load_session_settings(tape),
        }

    @hookimpl
    def before_llm_call(self, request, state):
        if not state.get("landing_tool_failed") and (reason := state.get("landing_no_update")):
            return LLMCallDecision.finish(reason)

    @hookimpl(trylast=True)
    async def after_tool_call(self, call: ToolCall, result: ToolCallResult, state: TurnState) -> None:
        if result.error is not None:
            state["landing_tool_failed"] = True
        context = call.context
        if context is None or context.code_mode or context.command:
            return
        # Only spill when the model can read the stored output back.
        if SPILL_READ_MODEL_NAME not in state.get("_runtime_tool_names", ()):
            return
        if result.error is None:
            value = result.result
        elif isinstance(result.error, BubError):
            value = result.error.as_dict() if result.result is None else result.result
        else:
            return

        spill = context.tape.get_sidecar(SPILL_SIDECAR_NAME)
        if not isinstance(spill, SpillStore):
            return
        content = result_content(value)
        text = "".join(part for part in content if isinstance(part, str))
        bounded = await spill.spill_tool_result(context.tape, text, tool=call.tool, run_id=call.run_id)
        if bounded != text:
            media = [part for part in content if not isinstance(part, str)]
            result.result = content_result([bounded, *media]) if media else bounded

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
    def spill_sidecar(self):
        return self.runtime.spill_store

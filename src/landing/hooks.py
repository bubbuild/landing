"""Bub hooks adapt Landing's business state and execution to the message pipeline."""

import hashlib
from typing import TYPE_CHECKING

from bub import hookimpl
from bub.builtin.hook_impl import BuiltinImpl
from bub.hooks.interception import LlmCallDecision
from bub.utils import workspace_from_state
from pydantic import ValidationError

from landing.prompts import COMMON, MODES

if TYPE_CHECKING:
    from landing.runtime import Runtime


class SDKDefaults(BuiltinImpl):
    """Keep native repository guidance without packaged channel instructions."""

    @hookimpl
    def system_prompt(self, prompt, state) -> str:
        return self._read_agents_file(state)

    @hookimpl
    def provide_environment(self, session_id, workspace):
        # Native tools derive their local environment from the selected task workspace.
        return None


class LandingHooks:
    """Use the same modes, task sidecar, and executor in hook and SDK integrations."""

    def __init__(self, runtime: "Runtime") -> None:
        self.runtime = runtime

    @hookimpl
    async def load_state(self, message, session_id):
        tape = self.runtime.agent.tape.session_tape(session_id, self.runtime.framework.workspace)
        selected = "explainer"
        for entry in await tape.store.fetch_all(tape.query().kinds("event")):
            if entry.payload.get("name") == "landing_mode_switch":
                selected = entry.payload["data"]["landing_mode"]
        return {"landing_mode": selected}

    @hookimpl
    def system_prompt(self, prompt, state) -> str:
        workspace = workspace_from_state(state)
        return COMMON + MODES[state.get("landing_mode", "explainer")] + f"\nTask workspace: {workspace}\n"

    @hookimpl
    def before_llm_call(self, request, state):
        if state.get("landing_tool_failed"):
            return None
        if reason := state.get("landing_no_update"):
            return LlmCallDecision.finish(reason)

    @hookimpl
    def after_tool_call(self, call, result, state) -> None:
        if result.error is not None:
            state["landing_tool_failed"] = True

    @hookimpl
    def after_llm_call(self, request, result, state) -> None:
        # The SDK calls this before parsing tool arguments. Retain metadata, never their contents.
        calls = []
        for call in result.tool_calls:
            function = call.get("function", {})
            arguments = function.get("arguments", "").encode()
            name = function.get("name")
            calls.append({
                "type": call.get("type"),
                "tool": name if name in request.tool_names else "unknown",
                "argument_bytes": len(arguments),
                "argument_sha256": hashlib.sha256(arguments).hexdigest(),
            })
        state["landing_llm_call"] = {
            "run_id": result.run_id,
            "completion_failed": result.error is not None,
            "tool_calls": calls,
        }

    def record_failure(self, action_id, state, error) -> None:
        if last_call := state.pop("landing_llm_call", None):
            diagnostic = {"last_llm_call": last_call}
            if isinstance(error.__cause__, ValidationError):
                diagnostic["validation_errors"] = [item["type"] for item in error.__cause__.errors(include_input=False)]
            self.runtime.tasks.event(action_id, "model.failure", diagnostic)

    def record_completion(self, state) -> None:
        if state.get("landing_tool_failed"):
            return
        if reason := state.get("landing_no_update"):
            self.runtime.tasks.event(state["landing_action_id"], "issue.unchanged", {"reason": reason})

    @hookimpl
    def provide_tape_store(self):
        return self.runtime.store

    @hookimpl
    def provide_tape_sidecar(self):
        return self.runtime.tasks

    @hookimpl
    async def run_model_stream(self, prompt, session_id, state):
        return await self.runtime.agent.run_stream(prompt=prompt, session_id=session_id, state=state)

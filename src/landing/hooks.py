"""Bub hooks adapt Landing's business state and execution to the message pipeline."""

from typing import TYPE_CHECKING

from bub import hookimpl
from bub.builtin.hook_impl import BuiltinImpl
from bub.utils import workspace_from_state

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
    def provide_tape_store(self):
        return self.runtime.store

    @hookimpl
    def provide_tape_sidecar(self):
        return self.runtime.tasks

    @hookimpl
    async def run_model_stream(self, prompt, session_id, state):
        return await self.runtime.agent.run_stream(prompt=prompt, session_id=session_id, state=state)

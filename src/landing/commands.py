"""User actions are native command tools; modes describe their delegated work."""

from typing import cast

from bub.tools import Tool, ToolContext, tool

from landing.models import Action, ActionRequest, Mode
from landing.tasks import Tasks

COMMANDS: dict[str, Mode] = {
    "triage": "issuer",
    "fix": "fixer",
    "review": "gatekeeper",
    "explain": "explainer",
}


@tool(context=True, agent_use=False)
async def mode(value: Mode | None = None, *, context: ToolContext) -> Mode:
    """Read or select this session's mode. Selection applies to subsequent tasks."""
    if value is not None:
        context.state["landing_mode"] = value
        await context.tape.append_event("landing_mode_switch", {"landing_mode": value})
    return cast("Mode", context.state.get("landing_mode", "explainer"))


def command_tool(name: str, selected: Mode) -> Tool:
    @tool(name=name, context=True, agent_use=False)
    async def delegate(instruction: str = "", *, context: ToolContext) -> Action:
        """Delegate work in the prepared workspace and return its task receipt."""
        request = ActionRequest.model_validate({
            **context.state.get("landing_request", {}),
            "mode": selected,
            "instruction": instruction or None,
        })
        tasks = cast("Tasks", context.tape.get_sidecar("tasks"))
        action, _ = tasks.create(
            request,
            scope=context.state.get("landing_scope", "sdk"),
            key=context.state.get("landing_delivery_key"),
            event=("sdk.invocation", context.state["landing_invocation"]),
        )
        await mode.run(selected, context=context)
        context.state["landing_pending_action"] = action.id
        return action

    return delegate


for name, selected in COMMANDS.items():
    command_tool(name, selected)

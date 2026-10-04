"""A Bub-compatible stream facade sharing Landing's task executor."""

from typing import TYPE_CHECKING

from bub.builtin import Agent as BubAgent
from bub.builtin.commands import strip_command_prefix
from bub.streaming import AsyncStreamEvents
from bub.tools import ToolContext
from bub.turn import TurnState

from landing.commands import admit
from landing.models import ActionRequest

if TYPE_CHECKING:
    from collections.abc import Collection

    from landing.runtime import Runtime


class Agent:
    """Native tools, options, and events with durable delegated task execution."""

    def __init__(self, runtime: "Runtime", **kwargs) -> None:
        self.runtime = runtime
        self.bub = BubAgent(runtime.framework, **kwargs)

    @property
    def tape(self):
        return self.bub.tape

    async def run_stream(
        self,
        *,
        session_id: str,
        prompt: str | list[dict],
        state: TurnState | None = None,
        model: str | None = None,
        allowed_skills: "Collection[str] | None" = None,
        allowed_tools: "Collection[str] | None" = None,
        reasoning_effort: str | None = None,
    ) -> AsyncStreamEvents:
        if not prompt:
            return await self.bub.run_stream(session_id=session_id, prompt=prompt, state=state)
        if state is None:
            state = await self.runtime.framework.build_state({"_runtime_agent": self.bub}, session_id)
        invocation = {
            "session_id": session_id,
            "model": model,
            "allowed_skills": list(allowed_skills) if allowed_skills is not None else None,
            "allowed_tools": list(allowed_tools) if allowed_tools is not None else None,
            "reasoning_effort": reasoning_effort,
        }
        state["landing_invocation"] = invocation
        workspace = None if self.runtime.workspaces is not None else state.get("_runtime_workspace")
        state.setdefault("landing_request", {"workspace": workspace})
        if isinstance(prompt, str) and strip_command_prefix(prompt, self.bub.command_prefix) is not None:
            async with self.runtime.execution:
                stream = await self.bub.run_stream(prompt=prompt, state=state, **invocation)
                pending = state.pop("landing_pending_action", None)
                if pending is None:
                    return stream
                async for _ in stream:
                    pass
            return self.runtime.stream(pending, state=state)

        request = ActionRequest(
            mode=state.get("landing_mode", "explainer"),
            instruction=prompt if isinstance(prompt, str) else "Continue the delegated task.",
            workspace=workspace,
        )
        state["landing_invocation"] = {**invocation, "prompt": prompt}
        action = admit(request, context=ToolContext(tape=self.tape, state=state))
        return self.runtime.stream(action.id, state=state)

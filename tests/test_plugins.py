"""Native Bub plugins drive Landing's tools, environment and continuations."""

import asyncio

import pytest
from bub import hookimpl
from bub.builtin.steering import InMemorySteeringInbox
from bub.core.tools import ToolContext, tool
from bub.environment import LocalEnvironment

from landing.models import ActionRequest
from landing.runtime import Runtime
from tests.conftest import completion
from tests.test_repository import report_reference


@pytest.mark.parametrize("mode", ["fixer", "gatekeeper"])
def test_delegated_work_and_checks_use_the_host_environment(tmp_path, model, mode):
    workspace, default = tmp_path / "workspace", tmp_path / "default"
    prepared = workspace / "prepared"
    workspace.mkdir()
    prepared.mkdir()
    (default / "prepared").mkdir(parents=True)
    (workspace / "deployment.txt").write_text("reference=wrong")
    if mode != "fixer":
        (prepared / "deployment.txt").write_text("reference=approved")

    class Host:
        @hookimpl
        def provide_environment(self, session_id, workspace):
            return LocalEnvironment(workspace / "prepared")

    responses, _ = model
    if mode == "fixer":
        responses.append(
            completion(tool="fs_write", arguments={"path": "deployment.txt", "content": "reference=approved"})
        )
    responses.extend([completion(tool="fs_read", arguments={"path": "deployment.txt"}), report_reference])

    async def run():
        async with Runtime(
            tmp_path / "landing.sqlite3", workspaces={"default": default, "selected": workspace}, plugins=[Host()]
        ).running() as landing:
            action = await landing.run(
                ActionRequest(
                    mode=mode,
                    instruction="Inspect the deployment reference.",
                    workspace="selected",
                    checks=['test "$(cat deployment.txt)" = reference=approved'],
                )
            )
            assert action.status == "completed"
            assert action.result == "Deployment reference: approved"
            assert (workspace / "deployment.txt").read_text() == "reference=wrong"
            assert (prepared / "deployment.txt").read_text() == "reference=approved"
            assert not (default / "prepared/deployment.txt").exists()

    asyncio.run(run())


def test_native_steering_delivers_input_to_the_running_task(tmp_path, model):
    inbox = InMemorySteeringInbox()

    class Steering:
        @hookimpl
        def provide_steering_inbox(self):
            return inbox

    responses, _ = model

    async def receive(**kwargs):
        await inbox.enqueue_message({"content": "reference=updated"}, {"session_id": "thread"})
        return completion("Inspected the original evidence.")

    responses.extend([receive, report_reference])

    async def run():
        async with Runtime(tmp_path / "landing.sqlite3", plugins=[Steering()]).running() as landing:
            action = await landing.run(
                ActionRequest(mode="explainer", instruction="Identify deployment."), session_id="thread"
            )
            assert action.status == "completed"
            assert action.result == "Deployment reference: updated"

    asyncio.run(run())


def test_native_plugin_tools_and_continuation_share_task_state(tmp_path, model):
    @tool(context=True)
    def identify(*, context: ToolContext) -> str:
        return "reference=" + context.state["reference"]

    class Context:
        @hookimpl
        def load_state(self):
            return {"reference": "approved"}

        @hookimpl
        def continue_prompt(self):
            return "Report reference=continued."

    responses, _ = model

    async def report(**kwargs):
        evidence = str(kwargs["messages"])
        reference = "reference=approved" in evidence
        continued = "reference=continued" in evidence
        return completion("Continued with approved evidence." if reference and continued else "Missing evidence.")

    responses.extend([completion(tool="identify"), report])

    async def run():
        landing = Runtime(tmp_path / "landing.sqlite3", plugins=[Context()])
        landing.tools.append(identify)
        async with landing.running():
            action = await landing.run(ActionRequest(mode="explainer", instruction="Identify deployment."))
            assert action.status == "completed"
            assert action.result == "Continued with approved evidence."

    asyncio.run(run())

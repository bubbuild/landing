import asyncio
from contextlib import closing

import pytest

from landing.models import ActionRequest
from landing.runtime import Runtime
from landing.tasks import Tasks
from tests.conftest import completion


def test_agent_can_finish_after_more_than_thirty_tool_steps(tmp_path, model, monkeypatch):
    monkeypatch.delenv("BUB_MAX_STEPS", raising=False)
    (tmp_path / "evidence.txt").write_text("Observed failure.\n")
    responses, requests = model
    responses.extend(completion(tool="fs_read", arguments={"path": "evidence.txt"}) for _ in range(31))
    responses.append(completion("Finished investigating the evidence."))

    async def run():
        async with Runtime(tmp_path / "landing.sqlite3").running() as runtime:
            action = await runtime.run(
                ActionRequest(mode="explainer", instruction="Investigate the failure.", workspace=str(tmp_path))
            )
            assert action.status == "completed"
            assert action.result == "Finished investigating the evidence."
            assert action.error is None

    asyncio.run(run())
    assert len(requests) == 32


def test_fixer_uses_sdk_tools_and_checks_before_completion(tmp_path, model):
    responses, requests = model
    responses.extend([
        completion(tool="fs_write", arguments={"path": "answer.txt", "content": "42\n"}),
        completion("Wrote and verified the answer."),
    ])
    path = tmp_path / "landing.sqlite3"

    async def run():
        runtime = Runtime(path)
        async with runtime.running():
            assert runtime.control.get_sidecar("tasks") is runtime.tasks
            assert len(runtime.framework.plugin_manager.get_plugins()) == 1
            assert runtime.agent.skill_dirs == ()
            action = await runtime.run(
                ActionRequest(
                    mode="fixer",
                    instruction="Write the answer.",
                    workspace=str(tmp_path),
                    checks=['test "$(cat answer.txt)" = 42'],
                )
            )
            assert action.status == "completed"
            assert action.result == "Wrote and verified the answer."
            assert action.exit_code() == 0
            events = runtime.tasks.events(action.id)
            assert [event.type for event in events][-2:] == ["validation", "action.completed"]
            assert events[-2].data["exit_code"] == 0
            tape = runtime.agent.tape.session_tape(action.id, tmp_path)
            assert runtime.store.read(tape.name)
            return action

    action = asyncio.run(run())
    assert (tmp_path / "answer.txt").read_text() == "42\n"
    assert len(requests) == 2  # A tool step's final event must not end the action.
    with closing(Tasks(path)) as tasks:
        assert tasks.get(action.id) == action


@pytest.mark.parametrize(("check", "expected"), [("true", "allow"), ("false", "block")])
def test_gatekeeper_decision_and_required_validation(tmp_path, model, check, expected):
    responses, requests = model
    responses.extend([
        completion(tool="decide", arguments={"decision": "allow"}),
        completion("The evidence supports proceeding."),
    ])

    async def run():
        async with Runtime(tmp_path / "landing.sqlite3").running() as runtime:
            action = await runtime.run(
                ActionRequest(
                    mode="gatekeeper", instruction="Evaluate the candidate.", workspace=str(tmp_path), checks=[check]
                )
            )
            assert action.status == "completed"
            assert action.decision == expected
            assert action.exit_code() == (expected != "allow")
            tools = {tool.name for tool in requests[0]["tools"]}
            assert "decide" in tools
            assert not tools.intersection({"fs.write", "fs.edit", "bash"})
            assert "Validation results:" in str(requests[0]["messages"])

    asyncio.run(run())


def test_missing_decision_is_inconclusive(tmp_path, model):
    responses, _ = model
    responses.append(completion("There is not enough evidence."))

    async def run():
        async with Runtime(tmp_path / "landing.sqlite3").running() as runtime:
            action = await runtime.run(ActionRequest(mode="gatekeeper", instruction="Evaluate the candidate."))
            assert action.decision == "inconclusive"
            assert action.exit_code() == 1

    asyncio.run(run())


def test_failed_fixer_keeps_output_and_changes(tmp_path, model):
    responses, _ = model
    responses.extend([
        completion(tool="fs_write", arguments={"path": "answer.txt", "content": "42"}),
        completion("Updated the answer."),
    ])

    async def run():
        async with Runtime(tmp_path / "landing.sqlite3").running() as runtime:
            action = await runtime.run(
                ActionRequest(mode="fixer", instruction="Write the answer.", workspace=str(tmp_path), checks=["false"])
            )
            assert action.status == "failed"
            assert action.result == "Updated the answer."
            assert action.error is not None
            assert "validation failed" in action.error["message"]
            assert action.exit_code() == 1

    asyncio.run(run())
    assert (tmp_path / "answer.txt").read_text() == "42"


def test_external_cancellation_stops_active_sdk_turn(tmp_path, model):
    responses, _ = model
    path = tmp_path / "landing.sqlite3"

    async def run():
        started = asyncio.Event()

        async def blocked(**kwargs):
            started.set()
            await asyncio.Event().wait()

        responses.append(blocked)
        async with Runtime(path).running() as runtime:
            task = asyncio.create_task(runtime.run(ActionRequest(mode="explainer", instruction="Explain the failure.")))
            await asyncio.wait_for(started.wait(), 5)
            action_id = runtime.tasks.list()[0].id
            with closing(Tasks(path)) as other_process:
                other_process.cancel(action_id)
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
            assert runtime.tasks.get(action_id).status == "cancelled"

    asyncio.run(run())


def test_model_failure_is_durable(tmp_path, model):
    responses, _ = model
    responses.append(RuntimeError("The model is unavailable."))

    async def run():
        async with Runtime(tmp_path / "landing.sqlite3").running() as runtime:
            action = await runtime.run(ActionRequest(mode="issuer", instruction="Identify the problem."))
            assert action.status == "failed"
            assert action.error is not None
            assert "model is unavailable" in action.error["message"]
            assert runtime.tasks.events(action.id)[-1].type == "action.failed"

    asyncio.run(run())


def test_second_worker_cannot_recover_live_work(tmp_path):
    path = tmp_path / "landing.sqlite3"

    async def run():
        async with Runtime(path).running() as owner:
            action, _ = owner.tasks.create(ActionRequest(mode="explainer", instruction="Explain the failure."))
            owner.tasks.claim(action.id)
            with pytest.raises(ValueError, match="Another worker"):
                async with Runtime(path).running():
                    pass
            assert owner.tasks.get(action.id).status == "running"
        async with Runtime(path).running() as restarted:
            assert restarted.tasks.get(action.id).status == "interrupted"
            assert restarted.tasks.next() is None

    asyncio.run(run())


@pytest.mark.parametrize("mode", ["issuer", "gatekeeper", "explainer"])
def test_read_modes_cannot_invoke_a_hallucinated_write_tool(tmp_path, model, mode):
    responses, _ = model
    responses.extend([
        completion(tool="fs_write", arguments={"path": "unexpected.txt", "content": "changed"}),
        completion("The write tool is unavailable."),
    ])

    async def run():
        async with Runtime(tmp_path / "landing.sqlite3").running() as runtime:
            await runtime.run(ActionRequest(mode=mode, instruction="Inspect the candidate.", workspace=str(tmp_path)))

    asyncio.run(run())
    assert not (tmp_path / "unexpected.txt").exists()


def test_background_processes_finish_before_post_fix_validation(tmp_path, model):
    responses, _ = model

    async def ready(**kwargs):
        async with asyncio.timeout(5):
            while not (tmp_path / "worker.pid").exists():
                await asyncio.sleep(0.01)
        return completion("Finished the requested change.")

    responses.extend([
        completion(tool="bash", arguments={"command": "echo $$ > worker.pid; sleep 60 & wait", "background": True}),
        ready,
    ])

    async def run():
        async with Runtime(tmp_path / "landing.sqlite3").running() as runtime:
            action = await runtime.run(
                ActionRequest(
                    mode="fixer",
                    instruction="Make and verify the change.",
                    workspace=str(tmp_path),
                    checks=['! kill -0 "$(cat worker.pid)" 2>/dev/null'],
                )
            )
            assert action.status == "completed"
            assert runtime.tasks.events(action.id)[-2].data["exit_code"] == 0

    asyncio.run(run())

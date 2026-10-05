import asyncio

import pytest

from landing.database import open_database
from landing.models import ActionRequest
from landing.runtime import Runtime
from landing.tasks import Tasks
from tests.conftest import completion


def test_missing_decision_is_inconclusive(tmp_path, model):
    responses, _ = model
    responses.append(completion("There is not enough evidence."))

    async def run():
        async with Runtime(tmp_path / "landing.sqlite3").running() as runtime:
            action = await runtime.run(ActionRequest(mode="gatekeeper", instruction="Evaluate the candidate."))
            assert action.decision == "inconclusive"
            assert action.exit_code() == 1

    asyncio.run(run())


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
            with open_database(path) as engine:
                other_process = Tasks(engine)
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

    asyncio.run(run())


def test_blank_completion_keeps_partial_changes_and_failed_history(tmp_path, model):
    responses, _ = model
    responses.extend([
        completion(tool="fs_write", arguments={"path": "answer.txt", "content": "42\n"}),
        completion("   \t\n"),
    ])
    path = tmp_path / "landing.sqlite3"

    async def run():
        async with Runtime(path).running() as runtime:
            action = await runtime.run(
                ActionRequest(mode="fixer", instruction="Write the answer.", workspace=str(tmp_path))
            )
            assert action.status == "failed"
            assert action.error is not None
            assert "empty" in action.error["message"].lower()
            assert action.exit_code() == 1
            return action

    action = asyncio.run(run())
    assert (tmp_path / "answer.txt").read_text() == "42\n"
    with open_database(path) as engine:
        tasks = Tasks(engine)
        assert tasks.get(action.id) == action


def test_second_worker_cannot_interrupt_live_work(tmp_path, model):
    responses, _ = model
    path = tmp_path / "landing.sqlite3"

    async def run():
        started = asyncio.Event()

        async def blocked(**kwargs):
            started.set()
            await asyncio.Event().wait()

        responses.append(blocked)
        async with Runtime(path).running() as owner:
            task = asyncio.create_task(owner.run(ActionRequest(mode="explainer", instruction="Explain the failure.")))
            await asyncio.wait_for(started.wait(), 5)
            action = owner.tasks.list()[0]
            with pytest.raises(ValueError, match="Another worker"):
                async with Runtime(path).running():
                    pass
            assert owner.tasks.get(action.id).status == "running"
            await owner.stop()
            with pytest.raises(asyncio.CancelledError):
                await task
        async with Runtime(path).running() as restarted:
            assert restarted.tasks.get(action.id).status == "interrupted"

    asyncio.run(run())


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

    asyncio.run(run())

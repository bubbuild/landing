import asyncio
import os

import pytest
from bub import BubFramework

from landing.cli import main
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


def test_host_cancellation_stops_active_sdk_turn(tmp_path, model, capsys):
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
            assert await asyncio.to_thread(main, ["--db", str(path), "action", "cancel", action_id]) == 2
            assert "HTTP API" in capsys.readouterr().err
            assert runtime.tasks.get(action_id).status == "running"
            runtime.cancel(action_id)
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
            assert runtime.tasks.get(action_id).status == "cancelled"

    asyncio.run(run())


def test_background_host_returns_receipts_and_preserves_pending_work(tmp_path, model):
    responses, _ = model
    path = tmp_path / "landing.sqlite3"

    async def run():
        started = asyncio.Event()

        async def blocked(**kwargs):
            started.set()
            await asyncio.Event().wait()

        runtime = Runtime(path)
        responses.extend([blocked, completion("Explained queued work.")])
        async with runtime.running(background=True):
            request = ActionRequest(mode="explainer", instruction="Explain the failure.")
            first, created = runtime.submit(request, key="delivery-1")
            assert created and first.status == "queued"
            await asyncio.wait_for(started.wait(), 5)
            repeated, created = runtime.submit(request, key="delivery-1")
            assert not created and repeated.id == first.id
            pending, _ = runtime.submit(request)
        with open_database(path) as engine:
            records = Tasks(engine)
            assert records.get(first.id).status == "interrupted"
            assert records.get(pending.id).status == "queued"
        async with Runtime(path).running(background=True) as restarted:
            async with asyncio.timeout(5):
                while restarted.tasks.get(pending.id).status not in {"completed", "failed"}:
                    await asyncio.sleep(0.01)
            assert restarted.tasks.get(pending.id).result == "Explained queued work."
        async with Runtime(path).running() as direct:
            with pytest.raises(RuntimeError, match="worker is unavailable"):
                direct.submit(request)

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


@pytest.mark.parametrize("native_host", [False, True])
def test_restarted_host_completes_concurrent_delegations(tmp_path, model, native_host):
    responses, _ = model

    async def explain(**kwargs):
        await asyncio.sleep(0)
        return completion("Explained the failure.")

    responses.extend([explain] * 4)
    runtime = Runtime(tmp_path / "landing.sqlite3", workspaces={"default": tmp_path})

    async def run():
        async with runtime.framework.running() if native_host else runtime.running():
            request = ActionRequest(mode="explainer", instruction="Explain the failure.")
            actions = await asyncio.gather(runtime.run(request), runtime.run(request))
            assert all(action.status == "completed" and action.result == "Explained the failure." for action in actions)

    for _ in range(2):
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


@pytest.mark.parametrize("native_host", [False, True])
def test_second_worker_cannot_interrupt_live_work(tmp_path, model, native_host):
    responses, _ = model
    path = tmp_path / "landing.sqlite3"

    async def run():
        started = asyncio.Event()

        async def blocked(**kwargs):
            started.set()
            await asyncio.Event().wait()

        responses.append(blocked)
        owner = Runtime(path, framework=BubFramework())
        async with owner.framework.running() if native_host else owner.running():
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


def test_cancellation_terminates_running_checks(tmp_path, model):
    pid_file = tmp_path / "check.pid"

    async def run():
        async with Runtime(tmp_path / "landing.sqlite3").running() as runtime:
            request = ActionRequest(
                mode="gatekeeper",
                instruction="Review the change.",
                workspace=str(tmp_path),
                checks=["echo $$ > check.pid; exec sleep 60"],
            )
            task = asyncio.create_task(runtime.run(request))
            async with asyncio.timeout(5):
                while not pid_file.exists() or not pid_file.read_text().strip():
                    await asyncio.sleep(0.01)
            runtime.cancel(runtime.tasks.list()[0].id)
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 10)
            # The host keeps running; the cancelled action must not leave its check behind.
            pid = int(pid_file.read_text())
            async with asyncio.timeout(5):
                while True:
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        return
                    await asyncio.sleep(0.05)

    asyncio.run(run())

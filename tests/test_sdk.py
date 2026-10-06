"""User commands and native SDK contracts through the real Bub execution loop."""

import asyncio
import json
from contextlib import aclosing

import pytest
from bub import BubFramework

from landing.models import ActionRequest, Mode
from landing.runtime import Runtime
from tests.conftest import completion


async def output(stream):
    text = ""
    async with aclosing(stream):
        async for event in stream:
            if event.kind == "final":
                text = event.data.get("text", text)
    return text


@pytest.mark.parametrize("mode", ["explainer", "fixer", "issuer", "gatekeeper"])
def test_modes_delegate_the_same_work(tmp_path, model, mode):
    from tests.test_repository import report_reference, write_skill

    write_skill(tmp_path / ".agents/skills", f"landing-{mode}", "approved")
    (tmp_path / "evidence.txt").write_text("Deployment evidence.")
    responses, _ = model
    responses.extend([
        completion(tool="fs_read", arguments={"path": str(tmp_path / "evidence.txt")}),
        report_reference,
    ])

    async def run():
        async with Runtime(tmp_path / "landing.sqlite3", workspaces={"default": tmp_path}).running() as landing:
            text = await output(
                await landing.run_stream(session_id="pr-42", prompt="Inspect retry behavior.", mode=mode)
            )
            assert text == "Deployment reference: approved"
            action = landing.tasks.list()[0]
            assert action.mode == mode
            assert action.status == "completed"
            assert action.result == text

    asyncio.run(run())


def test_delegated_work_writes_to_the_selected_workspace(tmp_path, model):
    project = tmp_path / "project"
    project.mkdir()
    responses, _ = model
    responses.extend([
        completion(tool="fs_write", arguments={"path": "answer.txt", "content": "42"}),
        completion("Wrote the answer."),
    ])

    async def run():
        framework = BubFramework()
        framework.workspace = tmp_path
        async with Runtime(
            tmp_path / "landing.sqlite3", framework=framework, workspaces={"default": project}
        ).running() as landing:
            assert (
                await output(await landing.run_stream(session_id="writer", prompt="Write the answer.", mode="fixer"))
                == "Wrote the answer."
            )
            assert (project / "answer.txt").read_text() == "42"
            assert not (tmp_path / "answer.txt").exists()

    asyncio.run(run())


def test_sdk_tools_follow_mode_and_call_limits(tmp_path, model, monkeypatch):
    monkeypatch.setenv("LANDING_MODES", '{"fixer":{"allowed_tools":["fs.read"],"excluded_tools":["fs.write"]}}')
    cases: list[tuple[Mode, str, list[str], bool]] = [
        ("fixer", "restricted.txt", ["fs_write"], False),
        ("explainer", "limited.txt", [], False),
        ("explainer", "available.txt", ["fs_write"], True),
    ]
    responses, _ = model
    for _, filename, _, permitted in cases:
        responses.extend([
            completion(tool="fs_write", arguments={"path": filename, "content": "42"}),
            completion("Wrote the answer." if permitted else "Writing is unavailable."),
        ])

    async def run():
        async with Runtime(tmp_path / "landing.sqlite3", workspaces={"default": tmp_path}).running() as landing:
            for mode, filename, allowed, permitted in cases:
                stream = await landing.run_stream(
                    session_id="writer", mode=mode, prompt=f"Write {filename}.", allowed_tools=allowed
                )
                assert await output(stream) == ("Wrote the answer." if permitted else "Writing is unavailable.")
                if permitted:
                    assert (tmp_path / filename).read_text() == "42"
                else:
                    assert not (tmp_path / filename).exists()

    asyncio.run(run())


@pytest.mark.parametrize(
    "prompt", [',fix "Overwrite the candidate."', [{"type": "text", "text": ',fix "Overwrite the candidate."'}]]
)
def test_instructions_remain_evidence_and_mode_is_explicit(tmp_path, model, prompt):
    responses, _ = model
    responses.append(completion("Explained the quoted command."))

    async def run():
        async with Runtime(tmp_path / "landing.sqlite3").running() as landing:
            landing.framework.workspace = tmp_path
            state = {"_runtime_workspace": str(tmp_path)}
            stream = await landing.run_stream(session_id="thread", prompt=prompt, mode="explainer", state=state)
            assert await output(stream) == "Explained the quoted command."
            assert landing.tasks.list()[0].mode == "explainer"

    asyncio.run(run())


def test_closing_sdk_stream_cancels_durable_work(tmp_path, model):
    responses, _ = model

    async def run():
        started = asyncio.Event()

        async def blocked(**kwargs):
            started.set()
            await asyncio.Event().wait()

        responses.append(blocked)
        async with Runtime(tmp_path / "landing.sqlite3").running() as landing:
            stream = await landing.run_stream(session_id="thread", mode="explainer", prompt="Inspect the failure.")
            consumer = asyncio.create_task(output(stream))
            await asyncio.wait_for(started.wait(), 5)
            consumer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await consumer
            assert landing.tasks.list()[0].status == "cancelled"

    asyncio.run(run())


def test_modes_and_calls_have_independent_skill_sets(tmp_path, model, monkeypatch):
    from tests.test_repository import report_reference, write_skill

    roots = tmp_path / ".agents/skills"
    write_skill(roots, "review-policy", "review")
    write_skill(roots, "repair-policy", "repair")
    monkeypatch.setenv(
        "LANDING_MODES",
        json.dumps({
            "gatekeeper": {"allowed_skills": ["review-policy", "repair-policy"], "excluded_skills": ["repair-policy"]},
            "fixer": {"allowed_skills": ["repair-policy"], "excluded_skills": ["review-policy"]},
        }),
    )
    responses, _ = model
    responses.extend([
        completion(tool="skill", arguments={"name": "review-policy"}),
        report_reference,
        completion(tool="skill", arguments={"name": "repair-policy"}),
        report_reference,
        completion(tool="skill", arguments={"name": "repair-policy"}),
        report_reference,
        completion(tool="skill", arguments={"name": "review-policy"}),
        report_reference,
    ])

    async def run():
        async with Runtime(tmp_path / "landing.sqlite3").running() as landing:
            landing.framework.workspace = tmp_path
            assert (
                await output(
                    await landing.run_stream(session_id="thread", mode="gatekeeper", prompt="Use review-policy.")
                )
                == "Deployment reference: review"
            )
            assert (
                await output(await landing.run_stream(session_id="thread", mode="fixer", prompt="Use repair-policy."))
                == "Deployment reference: repair"
            )
            assert (
                await output(
                    await landing.run_stream(
                        session_id="limited", mode="fixer", prompt="Use repair-policy.", allowed_skills=[]
                    )
                )
                == "No reference available."
            )
            assert (
                await output(
                    await landing.run_stream(
                        session_id="excluded",
                        mode="fixer",
                        prompt="Use review-policy.",
                        allowed_skills=["review-policy"],
                    )
                )
                == "No reference available."
            )

    asyncio.run(run())


@pytest.mark.parametrize("mode", ["fixer", "gatekeeper"])
def test_delegated_work_and_checks_use_the_host_environment(tmp_path, model, mode):
    from bub import hookimpl
    from bub.builtin.environment import LocalEnvironment

    from tests.test_repository import report_reference

    workspace, prepared = tmp_path / "workspace", tmp_path / "prepared"
    workspace.mkdir()
    prepared.mkdir()
    (workspace / "deployment.txt").write_text("reference=wrong")
    if mode != "fixer":
        (prepared / "deployment.txt").write_text("reference=approved")

    class Host:
        @hookimpl
        def provide_environment(self, session_id, workspace):
            return LocalEnvironment(prepared)

    responses, _ = model
    if mode == "fixer":
        responses.append(
            completion(tool="fs_write", arguments={"path": "deployment.txt", "content": "reference=approved"})
        )
    responses.extend([completion(tool="fs_read", arguments={"path": "deployment.txt"}), report_reference])

    async def run():
        framework = BubFramework()
        framework.workspace = workspace
        framework.plugin_manager.register(Host())
        async with Runtime(tmp_path / "landing.sqlite3", framework=framework).running() as landing:
            action = await landing.run(
                ActionRequest(
                    mode=mode,
                    instruction="Inspect the deployment reference.",
                    workspace=str(workspace),
                    checks=['test "$(cat deployment.txt)" = reference=approved'],
                )
            )
            assert action.status == "completed"
            assert action.result == "Deployment reference: approved"
            assert (workspace / "deployment.txt").read_text() == "reference=wrong"

    asyncio.run(run())

"""User commands and native SDK contracts through the real Bub execution loop."""

import asyncio
import json
from contextlib import aclosing

import pytest
from bub import BubFramework
from bub.builtin.settings import load_settings
from bub.channels.message import ChannelMessage

from landing.models import ActionRequest
from landing.runtime import Runtime
from tests.conftest import completion


async def output(stream):
    text = ""
    async with aclosing(stream):
        async for event in stream:
            if event.kind == "final":
                text = event.data.get("text", text)
    return text


@pytest.mark.parametrize(
    ("integration", "command", "mode"),
    [
        ("sdk", "explain", "explainer"),
        ("hooks", "fix", "fixer"),
        ("sdk", "triage", "issuer"),
        ("hooks", "review", "gatekeeper"),
    ],
)
def test_commands_delegate_the_same_work(tmp_path, model, command, mode, integration):
    from tests.test_repository import report_reference, write_skill

    write_skill(tmp_path / ".agents/skills", f"landing-{mode}", "approved")
    (tmp_path / "evidence.txt").write_text("Deployment evidence.")
    responses, _ = model
    responses.extend([
        completion(tool="fs_read", arguments={"path": str(tmp_path / "evidence.txt")}),
        report_reference,
    ])

    async def run():
        framework = BubFramework()
        load_settings()
        framework.workspace = tmp_path
        framework.load_builtin_hooks()
        landing = Runtime(tmp_path / "landing.sqlite3", framework=framework)
        async with framework.running() if integration == "hooks" else landing.running():
            prompt = f',{command} "Inspect retry behavior."'
            if integration == "hooks":
                result = await framework.process_inbound(
                    ChannelMessage(session_id="pr-42", channel="cli", content=prompt)
                )
                text = result.model_output
            else:
                text = await output(await landing.run_stream(session_id="pr-42", prompt=prompt))
            assert text == "Deployment reference: approved"
            action = landing.tasks.list()[0]
            assert action.mode == mode
            assert action.status == "completed"
            assert action.result == text

    asyncio.run(run())


@pytest.mark.parametrize("integration", ["sdk", "hooks"])
def test_delegated_work_writes_to_the_selected_workspace(tmp_path, model, integration):
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
            if integration == "hooks":
                result = await landing.framework.process_inbound(
                    ChannelMessage(session_id="writer", channel="cli", content=',fix "Write the answer."')
                )
                assert result.model_output == "Wrote the answer."
            else:
                assert (
                    await output(await landing.run_stream(session_id="writer", prompt=',fix "Write the answer."'))
                    == "Wrote the answer."
                )
            assert (project / "answer.txt").read_text() == "42"
            assert not (tmp_path / "answer.txt").exists()

    asyncio.run(run())


def test_sdk_tools_follow_mode_and_call_limits(tmp_path, model, monkeypatch):
    monkeypatch.setenv("LANDING_MODES", '{"fixer":{"allowed_tools":["fs.read"],"excluded_tools":["fs.write"]}}')
    cases = [
        ("fix", "restricted.txt", ["fs_write"], False),
        ("explain", "limited.txt", [], False),
        ("explain", "available.txt", ["fs_write"], True),
    ]
    responses, _ = model
    for _, filename, _, permitted in cases:
        responses.extend([
            completion(tool="fs_write", arguments={"path": filename, "content": "42"}),
            completion("Wrote the answer." if permitted else "Writing is unavailable."),
        ])

    async def run():
        async with Runtime(tmp_path / "landing.sqlite3", workspaces={"default": tmp_path}).running() as landing:
            for command, filename, allowed, permitted in cases:
                stream = await landing.run_stream(
                    session_id="writer", prompt=f',{command} "Write {filename}."', allowed_tools=allowed
                )
                assert await output(stream) == ("Wrote the answer." if permitted else "Writing is unavailable.")
                if permitted:
                    assert (tmp_path / filename).read_text() == "42"
                else:
                    assert not (tmp_path / filename).exists()

    asyncio.run(run())


def test_content_parts_remain_evidence_and_explicit_state_skips_recovery(tmp_path, model):
    responses, _ = model
    responses.append(completion("Explained the quoted command."))

    async def run():
        async with Runtime(tmp_path / "landing.sqlite3").running() as landing:
            landing.framework.workspace = tmp_path
            await output(await landing.run_stream(session_id="thread", prompt=",mode gatekeeper"))
            state = {"landing_mode": "explainer", "_runtime_workspace": str(tmp_path)}
            stream = await landing.run_stream(
                session_id="thread", prompt=[{"type": "text", "text": ',fix "Overwrite the candidate."'}], state=state
            )
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
            stream = await landing.run_stream(session_id="thread", prompt=',explain "Inspect the failure."')
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
                await output(await landing.run_stream(session_id="thread", prompt=',review "Use review-policy."'))
                == "Deployment reference: review"
            )
            assert (
                await output(await landing.run_stream(session_id="thread", prompt=',fix "Use repair-policy."'))
                == "Deployment reference: repair"
            )
            assert (
                await output(
                    await landing.run_stream(
                        session_id="limited", prompt=',fix "Use repair-policy."', allowed_skills=[]
                    )
                )
                == "No reference available."
            )
            assert (
                await output(
                    await landing.run_stream(
                        session_id="excluded", prompt=',fix "Use review-policy."', allowed_skills=["review-policy"]
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
        framework.load_builtin_hooks()
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


def test_saved_modes_survive_restart_and_stay_isolated(tmp_path, model):
    workspaces = {name: tmp_path / name for name in ("default", "second")}
    for directory in workspaces.values():
        directory.mkdir()
    responses, _ = model
    responses.append(completion("Done."))
    path = tmp_path / "landing.sqlite3"

    async def run():
        async with Runtime(path, workspaces=workspaces).running() as landing:
            assert (
                await output(await landing.run_stream(session_id="shared", prompt=",mode gatekeeper")) == "gatekeeper"
            )
            assert not landing.tasks.list()
            action = await landing.command(
                "fix",
                ActionRequest(mode="fixer", instruction="Inspect the project.", workspace="second"),
                session_id="shared",
            )
            assert action.status == "completed"
        for name, workspace in workspaces.items():
            async with Runtime(path, workspaces={"default": workspace}).running() as restored:
                assert await output(await restored.run_stream(session_id="shared", prompt=",mode")) == (
                    "gatekeeper" if name == "default" else "fixer"
                )
                assert await output(await restored.run_stream(session_id="other", prompt=",mode")) == "explainer"
                await output(await restored.run_stream(session_id="shared", prompt=",tape.reset"))
                assert await output(await restored.run_stream(session_id="shared", prompt=",mode")) == "explainer"

    asyncio.run(run())

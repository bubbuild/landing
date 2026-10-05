"""User commands and native SDK contracts through the real Bub execution loop."""

import asyncio
import json
from contextlib import aclosing

import pytest
from bub import BubFramework
from bub.channels.message import ChannelMessage

from landing.models import ActionRequest
from landing.runtime import Runtime
from tests.conftest import completion
from tests.provider import provider


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
        framework.workspace = tmp_path
        framework.load_builtin_hooks()
        async with Runtime(tmp_path / "landing.sqlite3", framework=framework).running() as landing:
            prompt = f',{command} "Inspect retry behavior."'
            if integration == "hooks":
                result = await framework.process_inbound(
                    ChannelMessage(session_id="pr-42", channel="cli", content=prompt)
                )
                text = result.model_output
            else:
                text = await output(await landing.agent.run_stream(session_id="pr-42", prompt=prompt))
            assert text == "Deployment reference: approved"
            action = landing.tasks.list()[0]
            assert action.mode == mode
            assert action.status == "completed"
            assert action.result == text

    asyncio.run(run())


@pytest.mark.parametrize("integration", ["local", "embedded", "sdk", "hooks"])
def test_delegated_work_writes_to_the_selected_workspace(tmp_path, model, integration):
    project = tmp_path / "project"
    project.mkdir()
    responses, _ = model
    responses.extend([
        completion(tool="fs_write", arguments={"path": "answer.txt", "content": "42"}),
        completion("Wrote the answer."),
    ])

    async def run():
        framework = None
        if integration != "local":
            framework = BubFramework()
            framework.workspace = tmp_path
            framework.load_builtin_hooks()
        async with Runtime(
            tmp_path / "landing.sqlite3", framework=framework, workspaces={"default": project}
        ).running() as landing:
            if integration == "hooks":
                result = await landing.framework.process_inbound(
                    ChannelMessage(session_id="writer", channel="cli", content=',fix "Write the answer."')
                )
                assert result.model_output == "Wrote the answer."
            elif integration == "sdk":
                assert (
                    await output(await landing.agent.run_stream(session_id="writer", prompt=',fix "Write the answer."'))
                    == "Wrote the answer."
                )
            else:
                action = await landing.run(ActionRequest(mode="fixer", instruction="Write the answer."))
                assert action.status == "completed"
            assert (project / "answer.txt").read_text() == "42"
            assert not (tmp_path / "answer.txt").exists()

    asyncio.run(run())


def test_mode_survives_restart_without_leaking_between_sessions(tmp_path, model, monkeypatch):
    settings = tmp_path / "settings.yml"
    settings.write_text(json.dumps({"db": [], "server": []}))
    monkeypatch.setenv("LANDING_CONFIG", str(settings))
    monkeypatch.setenv("BUB_MCP_INIT_TIMEOUT_SECONDS", "invalid")
    _, requests = model
    path = tmp_path / "landing.sqlite3"

    async def run():
        for restarted in (False, True):
            async with Runtime(path).running() as landing:
                landing.framework.workspace = tmp_path
                if not restarted:
                    assert (
                        await output(await landing.agent.run_stream(session_id="reviewer", prompt=",mode gatekeeper"))
                        == "gatekeeper"
                    )
                assert (
                    await output(await landing.agent.run_stream(session_id="reviewer", prompt=",mode")) == "gatekeeper"
                )
                assert await output(await landing.agent.run_stream(session_id="other", prompt=",mode")) == "explainer"
                assert not landing.tasks.list()
        assert not requests

    asyncio.run(run())


def test_sdk_capability_selection_and_per_turn_model_are_preserved(tmp_path, monkeypatch):
    responses = [
        completion(tool="fs_write", arguments={"path": "answer.txt", "content": "42"}),
        completion("The write capability is unavailable."),
        completion(tool="fs_write", arguments={"path": "answer.txt", "content": "42"}),
        completion("Wrote the answer."),
    ]

    async def run():
        async with Runtime(tmp_path / "landing.sqlite3").running() as landing:
            landing.framework.workspace = tmp_path
            stream = await landing.agent.run_stream(
                session_id="limited", prompt="Write the answer.", allowed_tools=[], model="openai:chosen-model"
            )
            assert await output(stream) == "The write capability is unavailable."
            assert not (tmp_path / "answer.txt").exists()
            stream = await landing.agent.run_stream(
                session_id="writer", prompt=',fix "Write the answer."', allowed_tools=["fs_write"]
            )
            assert await output(stream) == "Wrote the answer."
            assert (tmp_path / "answer.txt").read_text() == "42"

    with provider(responses) as (api_base, requests):
        monkeypatch.setenv("LANDING_MODEL", "openai:default-model")
        monkeypatch.setenv("LANDING_API_KEY", "test-key")
        monkeypatch.setenv("LANDING_API_BASE", api_base)
        monkeypatch.setenv("LANDING_CLIENT_ARGS", '{"max_retries": 0}')
        asyncio.run(run())
        assert requests[0]["model"] == "chosen-model"
        assert requests[-1]["model"] == "default-model"


def test_content_parts_remain_evidence_and_explicit_state_skips_recovery(tmp_path, model):
    responses, _ = model
    responses.append(completion("Explained the quoted command."))

    async def run():
        async with Runtime(tmp_path / "landing.sqlite3").running() as landing:
            landing.framework.workspace = tmp_path
            await output(await landing.agent.run_stream(session_id="thread", prompt=",mode gatekeeper"))
            state = {"landing_mode": "explainer", "_runtime_workspace": str(tmp_path)}
            stream = await landing.agent.run_stream(
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
            stream = await landing.agent.run_stream(session_id="thread", prompt=',explain "Inspect the failure."')
            consumer = asyncio.create_task(output(stream))
            await asyncio.wait_for(started.wait(), 5)
            consumer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await consumer
            assert landing.tasks.list()[0].status == "cancelled"

    asyncio.run(run())


@pytest.mark.parametrize(
    "limits",
    [
        {"allowed_tools": []},
        {"excluded_tools": ["fs.write"]},
        {"allowed_tools": ["fs.write"], "excluded_tools": ["fs_write"]},
    ],
)
def test_modes_have_independent_tool_configuration(tmp_path, model, monkeypatch, limits):
    monkeypatch.setenv("LANDING_MODES", json.dumps({"fixer": limits}))
    responses, _ = model
    responses.extend([
        completion(tool="fs_write", arguments={"path": "restricted.txt", "content": "changed"}),
        completion("The configured capability is unavailable."),
        completion(tool="fs_write", arguments={"path": "available.txt", "content": "changed"}),
        completion("The authorized capability worked."),
    ])

    async def run():
        async with Runtime(tmp_path / "landing.sqlite3").running() as landing:
            restricted = await landing.run(
                ActionRequest(mode="fixer", instruction="Write restricted.txt.", workspace=str(tmp_path))
            )
            assert restricted.status == "completed"
            assert not (tmp_path / "restricted.txt").exists()
            available = await landing.run(
                ActionRequest(mode="explainer", instruction="Write available.txt.", workspace=str(tmp_path))
            )
            assert available.status == "completed"
            assert (tmp_path / "available.txt").read_text() == "changed"

    asyncio.run(run())


@pytest.mark.parametrize(
    "limits",
    [
        {"allowed_tools": ["fs.read"]},
        {"excluded_tools": ["fs.write"]},
        {"allowed_tools": ["fs.write"], "excluded_tools": ["fs_write"]},
    ],
)
def test_sdk_call_can_narrow_but_not_expand_mode_tools(tmp_path, model, monkeypatch, limits):
    monkeypatch.setenv("LANDING_MODES", json.dumps({"fixer": limits}))
    responses, _ = model
    responses.extend([
        completion(tool="fs_write", arguments={"path": "unexpected.txt", "content": "changed"}),
        completion("The mode does not allow writing."),
    ])

    async def run():
        async with Runtime(tmp_path / "landing.sqlite3").running() as landing:
            landing.framework.workspace = tmp_path
            stream = await landing.agent.run_stream(
                session_id="writer", prompt=',fix "Write unexpected.txt."', allowed_tools=["fs_write"]
            )
            assert await output(stream) == "The mode does not allow writing."
            assert not (tmp_path / "unexpected.txt").exists()

    asyncio.run(run())


@pytest.mark.parametrize("selection", ["allow", "exclude", "overlap"])
def test_modes_and_calls_have_independent_skill_sets(tmp_path, model, monkeypatch, selection):
    from tests.test_repository import report_reference, write_skill

    roots = tmp_path / ".agents/skills"
    write_skill(roots, "review-policy", "review")
    write_skill(roots, "repair-policy", "repair")
    modes = {}
    for mode, allowed, excluded in (
        ("gatekeeper", "Review-Policy", "Repair-Policy"),
        ("fixer", "repair-policy", "review-policy"),
    ):
        limits = {}
        if selection != "exclude":
            limits["allowed_skills"] = [allowed, excluded] if selection == "overlap" else [allowed]
        if selection != "allow":
            limits["excluded_skills"] = [excluded]
        modes[mode] = limits
    monkeypatch.setenv("LANDING_MODES", json.dumps(modes))
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
                await output(await landing.agent.run_stream(session_id="thread", prompt=',review "Use review-policy."'))
                == "Deployment reference: review"
            )
            assert (
                await output(await landing.agent.run_stream(session_id="thread", prompt=',fix "Use repair-policy."'))
                == "Deployment reference: repair"
            )
            assert (
                await output(
                    await landing.agent.run_stream(
                        session_id="limited", prompt=',fix "Use repair-policy."', allowed_skills=[]
                    )
                )
                == "No reference available."
            )
            assert (
                await output(
                    await landing.agent.run_stream(
                        session_id="excluded", prompt=',fix "Use review-policy."', allowed_skills=["review-policy"]
                    )
                )
                == "No reference available."
            )

    asyncio.run(run())


def test_delegated_request_uses_the_host_provided_environment(tmp_path, model):
    from bub import hookimpl
    from bub.builtin.environment import LocalEnvironment

    from tests.test_repository import report_reference

    workspace = tmp_path / "workspace"
    prepared = tmp_path / "prepared"
    workspace.mkdir()
    prepared.mkdir()
    (prepared / "deployment.txt").write_text("reference=approved")

    class Host:
        @hookimpl
        def provide_environment(self, session_id, workspace):
            return LocalEnvironment(prepared)

    responses, _ = model
    responses.extend([completion(tool="fs_read", arguments={"path": "deployment.txt"}), report_reference])

    async def run():
        framework = BubFramework()
        framework.workspace = workspace
        framework.load_builtin_hooks()
        framework.plugin_manager.register(Host())
        async with Runtime(tmp_path / "landing.sqlite3", framework=framework).running() as landing:
            action = await landing.run(
                ActionRequest(mode="explainer", instruction="Read the deployment reference.", workspace=str(workspace))
            )
            assert action.status == "completed"
            assert action.result == "Deployment reference: approved"

    asyncio.run(run())

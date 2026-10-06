"""Prepared MCP tools through the public CLI, SDK, hooks, and task lifecycle."""

import asyncio
import json
import os
import sys

import bub
import pytest
from bub import BubFramework
from bub.channels.message import ChannelMessage

from landing.cli import main
from landing.models import ActionRequest
from landing.runtime import Runtime
from tests.conftest import completion
from tests.test_sdk import output


@pytest.fixture
def mcp_server(tmp_path, monkeypatch):
    monkeypatch.delenv("LANDING_MCP_CONFIG")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(bub, "home", home / ".bub")
    script = tmp_path / "server.py"
    script.write_text("""import asyncio
import json
import os
import sys
from pathlib import Path
from fastmcp import FastMCP

server = FastMCP("Evidence")

@server.tool
async def record(text: str, wait: bool = False) -> str:
    with Path(sys.argv[1]).open("a") as receipt:
        receipt.write(json.dumps({"text": text, "pid": os.getpid()}) + "\\n")
    if wait:
        await asyncio.Event().wait()
    return "Recorded " + text + "."

server.run(show_banner=False)
""")

    def configure(path, receipt):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"mcpServers": {"evidence": {"command": sys.executable, "args": [str(script), str(receipt)]}}})
        )

    return home, configure


@pytest.mark.parametrize("source", ["project", "user", "bub", "explicit", "bub-env", "yaml"])
def test_cli_uses_selected_mcp_configuration(tmp_path, mcp_server, model, monkeypatch, capfd, source):
    home, configure = mcp_server
    workspace = tmp_path / "project"
    workspace.mkdir()
    receipts = {name: tmp_path / f"{name}.txt" for name in ("project", "user", "bub", "explicit")}
    configure(home / ".bub/mcp.json", receipts["bub"])
    if source != "bub":
        configure(home / ".agents/mcp.json", receipts["user"])
    if source not in {"bub", "user"}:
        configure(workspace / ".agents/mcp.json", receipts["project"])
    if source in {"explicit", "bub-env", "yaml"}:
        configure(workspace / "custom.json", receipts["explicit"])
        if source == "yaml":
            settings = tmp_path / "settings.yml"
            settings.write_text("mcp_config: custom.json\n")
            monkeypatch.setenv("LANDING_CONFIG", str(settings))
        else:
            monkeypatch.setenv("LANDING_MCP_CONFIG" if source == "explicit" else "BUB_MCP_CONFIG_PATH", "custom.json")
    responses, _ = model
    responses.extend([
        completion(tool="mcp_evidence_record", arguments={"text": "candidate"}),
        completion("Recorded candidate."),
    ])
    assert (
        main([
            "--db",
            str(tmp_path / "landing.sqlite3"),
            "explain",
            "Record candidate evidence.",
            "--workspace",
            str(workspace),
            "--json",
        ])
        == 0
    )
    assert json.loads(capfd.readouterr().out)["result"] == "Recorded candidate."
    expected = "explicit" if source in {"explicit", "bub-env", "yaml"} else source
    assert json.loads(receipts[expected].read_text())["text"] == "candidate"
    assert all(not receipt.exists() for name, receipt in receipts.items() if name != expected)


@pytest.mark.parametrize(("integration", "source"), [("sdk", "framework"), ("hooks", "project")])
def test_mcp_tools_follow_mode_and_caller_permissions(tmp_path, mcp_server, model, monkeypatch, integration, source):
    _, configure = mcp_server
    receipt = tmp_path / "record.txt"
    selected = tmp_path / ".agents/mcp.json" if source == "project" else tmp_path / "custom.json"
    configure(selected, receipt)
    monkeypatch.setenv(
        "LANDING_MODES", '{"explainer":{"allowed_tools":[]},"fixer":{"allowed_tools":["mcp.evidence_record"]}}'
    )
    responses, _ = model
    for text in ("denied", "allowed", "caller-denied"):
        responses.extend([
            completion(tool="mcp_evidence_record", arguments={"text": text}),
            completion(text),
        ])

    async def run():
        settings = tmp_path / "host.yml"
        settings.write_text(json.dumps({"mcp_config": str(selected)}))
        framework = BubFramework(config_file=settings) if source == "framework" else BubFramework()
        framework.workspace = tmp_path
        framework.load_builtin_hooks()
        async with Runtime(tmp_path / "landing.sqlite3", framework=framework).running() as landing:
            for command in ("explain", "fix"):
                prompt = f',{command} "Record evidence."'
                if integration == "hooks":
                    await framework.process_inbound(ChannelMessage(session_id=command, channel="cli", content=prompt))
                else:
                    await output(await landing.run_stream(session_id=command, prompt=prompt))
            await output(
                await landing.run_stream(session_id="limited", prompt=',fix "Record evidence."', allowed_tools=[])
            )
        assert [json.loads(line)["text"] for line in receipt.read_text().splitlines()] == ["allowed"]

    asyncio.run(run())


def test_workspace_switch_does_not_reuse_previous_mcp_server(tmp_path, mcp_server, model):
    _, configure = mcp_server
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    receipt = tmp_path / "record.txt"
    configure(first / ".agents/mcp.json", receipt)
    responses, _ = model
    for text in ("first", "second"):
        responses.extend([completion(tool="mcp_evidence_record", arguments={"text": text}), completion(text)])

    async def run():
        async with Runtime(
            tmp_path / "landing.sqlite3", workspaces={"first": first, "second": second}
        ).running() as landing:
            for name in ("first", "second"):
                action = await landing.run(
                    ActionRequest(mode="explainer", workspace=name, instruction="Record evidence.")
                )
                assert action.status == "completed"
        assert [json.loads(line)["text"] for line in receipt.read_text().splitlines()] == ["first"]

    asyncio.run(run())


def test_cancellation_closes_mcp_subprocess(tmp_path, mcp_server, model):
    _, configure = mcp_server
    receipt = tmp_path / "record.txt"
    configure(tmp_path / ".agents/mcp.json", receipt)
    responses, _ = model
    responses.append(completion(tool="mcp_evidence_record", arguments={"text": "pending", "wait": True}))

    async def run():
        async with Runtime(tmp_path / "landing.sqlite3").running() as landing:
            landing.framework.workspace = tmp_path
            consumer = asyncio.create_task(
                output(await landing.run_stream(session_id="pending", prompt="Record evidence."))
            )
            async with asyncio.timeout(15):
                while not receipt.exists():
                    await asyncio.sleep(0.01)
            pid = json.loads(receipt.read_text())["pid"]
            consumer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await consumer
            assert landing.tasks.list()[0].status == "cancelled"
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)

    asyncio.run(run())


def test_explicit_missing_mcp_configuration_fails_without_model_work(tmp_path, mcp_server, model, monkeypatch):
    monkeypatch.setenv("LANDING_MCP_CONFIG", "missing.json")
    _, requests = model

    async def run():
        async with Runtime(tmp_path / "landing.sqlite3").running() as landing:
            action = await landing.run(
                ActionRequest(mode="explainer", workspace=str(tmp_path), instruction="Inspect evidence.")
            )
            assert action.status == "failed"
            assert action.error and "MCP configuration does not exist" in action.error["message"]
            assert not requests

    asyncio.run(run())

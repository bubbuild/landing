"""User-visible repository guidance and skills through the actual SDK and CLI."""

import asyncio
import json
import re
from pathlib import Path

import pytest

from landing.cli import main
from landing.models import ActionRequest
from landing.runtime import Runtime
from tests.conftest import completion


def write_skill(root, name, reference):
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Identify the deployment reference.\n---\nreference={reference}\n"
    )


async def report_reference(**kwargs):
    references = re.findall(r"reference=([a-z]+)", str(kwargs["messages"]))
    return completion("Deployment reference: " + references[-1] if references else "No reference available.")


def test_cli_uses_selected_project_instructions_and_explicit_skill_root(tmp_path, monkeypatch, model, capsys):
    project = tmp_path / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text("Report project=regional.\n")
    write_skill(tmp_path / "shared", "deploy", "approved")
    write_skill(tmp_path / "ambient" / ".agents/skills", "deploy", "ambient")
    monkeypatch.chdir(tmp_path / "ambient")
    responses, _ = model

    async def report(**kwargs):
        reference = (await report_reference(**kwargs)).choices[0].message.content
        project_name = re.findall(r"project=([a-z]+)", str(kwargs["messages"]))
        return completion(f"{project_name[-1] if project_name else 'unknown'}: {reference}")

    responses.extend([completion(tool="skill", arguments={"name": "deploy"}), report])
    assert (
        main([
            "--db",
            str(tmp_path / "landing.sqlite3"),
            "--skill-dir",
            str(tmp_path / "shared"),
            "explainer",
            "Identify the deployment reference.",
            "--workspace",
            str(project),
            "--json",
        ])
        == 0
    )
    assert json.loads(capsys.readouterr().out)["result"] == "regional: Deployment reference: approved"


def test_registered_workspaces_keep_their_own_skills_and_user_fallback(tmp_path, monkeypatch, model):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    write_skill(tmp_path / "home" / ".agents/skills", "deploy", "global")
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    write_skill(first / ".agents/skills", "deploy", "local")
    responses, _ = model
    responses.extend([
        completion(tool="skill", arguments={"name": "deploy"}),
        report_reference,
        completion(tool="skill", arguments={"name": "deploy"}),
        report_reference,
    ])

    async def run():
        async with Runtime(
            tmp_path / "landing.sqlite3", workspaces={"first": first, "second": second}
        ).running() as runtime:
            local = await runtime.run(
                ActionRequest(mode="explainer", instruction="Identify deployment.", workspace="first")
            )
            global_ = await runtime.run(
                ActionRequest(mode="explainer", instruction="Identify deployment.", workspace="second")
            )
            assert local.result == "Deployment reference: local"
            assert global_.result == "Deployment reference: global"

    asyncio.run(run())


@pytest.mark.parametrize("source", ["environment", "yaml"])
def test_cli_loads_configured_skills_and_explicit_override(tmp_path, monkeypatch, model, capsys, source):
    project = tmp_path / "project"
    project.mkdir()
    configured, explicit = tmp_path / "configured", tmp_path / "explicit"
    write_skill(configured, "deploy", "configured")
    write_skill(explicit, "deploy", "explicit")
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    config = tmp_path / "landing.yml"
    monkeypatch.setenv("LANDING_CONFIG", str(config))
    if source == "environment":
        monkeypatch.setenv("LANDING_SKILL_DIRS", json.dumps([str(configured)]))
    else:
        monkeypatch.delenv("LANDING_SKILL_DIRS", raising=False)
        config.write_text("skill_dirs: " + json.dumps([str(configured)]) + "\n")
    responses, _ = model
    responses.extend([
        report_reference,
        completion(tool="skill", arguments={"name": "deploy"}),
        report_reference,
    ])
    command = ["explainer", "Use $deploy to identify deployment.", "--workspace", str(project), "--json"]
    database = ["--db", str(tmp_path / "landing.sqlite3")]
    assert main([*database, *command]) == 0
    assert json.loads(capsys.readouterr().out)["result"] == "Deployment reference: configured"
    command[1] = "Identify deployment."
    assert main([*database, "--skill-dir", str(explicit), *command]) == 0
    assert json.loads(capsys.readouterr().out)["result"] == "Deployment reference: explicit"


def test_skill_guidance_does_not_grant_read_mode_shell_access(tmp_path, model):
    write_skill(tmp_path / ".agents/skills", "deploy", "approved")
    responses, _ = model
    responses.extend([
        completion(tool="skill", arguments={"name": "deploy"}),
        completion(tool="bash", arguments={"command": "touch unexpected.txt"}),
        completion("I can explain deployment without changing it."),
    ])

    async def run():
        async with Runtime(tmp_path / "landing.sqlite3").running() as runtime:
            action = await runtime.run(
                ActionRequest(mode="explainer", instruction="Use the deploy skill.", workspace=str(tmp_path))
            )
            assert action.status == "completed"

    asyncio.run(run())
    assert not (tmp_path / "unexpected.txt").exists()

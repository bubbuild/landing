"""Default skills through the public CLI, real SDK and an external gh boundary."""

import json
import os
import sys

import pytest

from landing.cli import main
from tests.conftest import completion
from tests.test_repository import report_reference, write_skill


@pytest.fixture
def skill_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("LANDING_CONFIG", str(tmp_path / "unused.yml"))
    monkeypatch.setenv("LANDING_SKILL_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("LANDING_DEFAULT_SKILLS", "true")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable = bin_dir / "gh"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import sys\nfrom pathlib import Path\n"
        "if sys.argv[1] == 'skill':\n"
        "    name = sys.argv[4].split('/')[-1]\n"
        "    root = Path(sys.argv[sys.argv.index('--dir') + 1]) / name\n"
        "    root.mkdir()\n"
        "    (root / 'SKILL.md').write_text(f'---\\nname: {name}\\ndescription: Identify a reference.\\n---\\nreference=remote\\n')\n"
        "else:\n"
        "    print('MIT' if '/LICENSE?' in sys.argv[2] else '---\\nname: humanizer\\ndescription: Edit prose.\\n---\\nreference=remote')\n"
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    workspace = tmp_path / "project"
    workspace.mkdir()
    return workspace, executable


@pytest.mark.parametrize("name", ["friendly-python", "humanizer"])
@pytest.mark.parametrize("invocation", ["mention", "tool"])
def test_cli_loads_default_skill_on_demand_and_reuses_it_offline(
    tmp_path, monkeypatch, model, capsys, skill_environment, name, invocation
):
    workspace, executable = skill_environment
    responses, _ = model
    instruction = f"Use ${name} to identify the reference." if invocation == "mention" else "Identify the reference."
    command = [
        "--db",
        str(tmp_path / "landing.sqlite3"),
        "explainer",
        instruction,
        "--workspace",
        str(workspace),
        "--json",
    ]
    for offline in (False, True):
        if offline:
            executable.unlink()
            monkeypatch.setenv("PATH", str(executable.parent))
        if invocation == "tool":
            responses.append(completion(tool="skill", arguments={"name": name}))
        responses.append(report_reference)
        assert main(command) == 0
        assert json.loads(capsys.readouterr().out)["result"] == "Deployment reference: remote"


def test_project_skill_overrides_default_without_gh(tmp_path, monkeypatch, model, capsys, skill_environment):
    workspace, executable = skill_environment
    write_skill(workspace / ".agents/skills", "friendly-python", "project")
    executable.unlink()
    monkeypatch.setenv("PATH", str(executable.parent))
    responses, _ = model
    responses.append(report_reference)
    assert (
        main([
            "--db",
            str(tmp_path / "landing.sqlite3"),
            "explainer",
            "Use $friendly-python to identify the reference.",
            "--workspace",
            str(workspace),
            "--json",
        ])
        == 0
    )
    assert json.loads(capsys.readouterr().out)["result"] == "Deployment reference: project"


def test_default_skill_catalog_needs_no_gh(tmp_path, monkeypatch, model, capsys, skill_environment):
    workspace, executable = skill_environment
    executable.unlink()
    monkeypatch.setenv("PATH", str(executable.parent))
    responses, _ = model

    async def report_catalog(**kwargs):
        result = next(message["content"] for message in kwargs["messages"] if message["role"] == "tool")
        return completion(result)

    responses.extend([completion(tool="skill"), report_catalog])
    assert (
        main([
            "--db",
            str(tmp_path / "landing.sqlite3"),
            "explainer",
            "List available skills.",
            "--workspace",
            str(workspace),
            "--json",
        ])
        == 0
    )
    result = json.loads(capsys.readouterr().out)["result"]
    assert all(name in result for name in ["documentation-writer", "humanizer", "piglet", "friendly-python"])


def test_skill_download_failure_reports_observed_error(tmp_path, model, capsys, skill_environment):
    workspace, executable = skill_environment
    executable.write_text(
        f"#!{sys.executable}\nimport sys\nprint('HTTP 503: service unavailable', file=sys.stderr)\nsys.exit(1)\n"
    )
    responses, _ = model

    async def report_error(**kwargs):
        result = next(message["content"] for message in kwargs["messages"] if message["role"] == "tool")
        return completion(result)

    responses.extend([completion(tool="skill", arguments={"name": "friendly-python"}), report_error])
    assert (
        main([
            "--db",
            str(tmp_path / "landing.sqlite3"),
            "explainer",
            "Load friendly-python and explain any failure.",
            "--workspace",
            str(workspace),
            "--json",
        ])
        == 0
    )
    assert "HTTP 503: service unavailable" in json.loads(capsys.readouterr().out)["result"]

"""Exercise Landing configuration through the CLI and provider protocol."""

import json
import os
import subprocess
import sys

import pytest

from landing.cli import main
from tests.conftest import completion
from tests.provider import provider


@pytest.mark.parametrize("source", ["environment", "file", "custom-file", "precedence"])
def test_cli_model_configuration(tmp_path, source):
    environment = {key: value for key, value in os.environ.items() if not key.startswith(("BUB_", "LANDING_"))}
    environment["HOME"] = str(tmp_path)
    authorization = []
    with provider([completion("The evidence explains the failed check.")], authorization=authorization) as (
        api_base,
        requests,
    ):
        settings = {
            "model": "openai:selected-model",
            "api_key": "selected-key",
            "api_base": api_base,
            "client_args": {"api_format": "chat"},
            "completion_args": {"temperature": 0.2},
            "max_tokens": 128,
        }
        if source == "environment":
            environment.update({
                "LANDING_" + key.upper(): json.dumps(value) if isinstance(value, dict) else str(value)
                for key, value in settings.items()
            })
        else:
            landing = tmp_path / ".landing"
            landing.mkdir()
            config_file = landing / ("custom.yml" if source == "custom-file" else "config.yml")
            config_file.write_text(json.dumps(settings))
            if source == "custom-file":
                environment["LANDING_CONFIG"] = str(config_file)
            if source == "precedence":
                environment["LANDING_MODEL"] = "openai:environment-model"
                settings["model"] = "openai:environment-model"
        result = subprocess.run(  # noqa: S603 -- run the public CLI against a disposable provider and workspace.
            [
                sys.executable,
                "-m",
                "landing",
                "--db",
                str(tmp_path / "landing.sqlite3"),
                "explain",
                "Explain the failure.",
                "--workspace",
                str(tmp_path),
            ],
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr + result.stdout
        assert "The evidence explains the failed check." in result.stdout
        assert requests[0]["model"] == settings["model"].split(":", 1)[1]
        assert authorization == ["Bearer selected-key"]


@pytest.mark.parametrize("source", ["environment", "yaml"])
def test_cli_database_configuration_and_explicit_override(tmp_path, monkeypatch, model, capsys, source):
    configured = tmp_path / "configured.sqlite3"
    explicit = tmp_path / "explicit.sqlite3"
    if source == "environment":
        monkeypatch.setenv("LANDING_DB", str(configured))
    else:
        config_file = tmp_path / "landing.yml"
        monkeypatch.setenv("LANDING_CONFIG", str(config_file))
        config_file.write_text(json.dumps({"db": str(configured)}))
    responses, _ = model
    responses.append(completion("Explained the configured project."))
    assert main(["explain", "Explain this project.", "--workspace", str(tmp_path), "--json"]) == 0
    action = json.loads(capsys.readouterr().out)
    assert main(["action", "view", action["id"], "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == action
    assert main(["--db", str(explicit), "action", "list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []
    assert explicit.is_file()


@pytest.mark.parametrize("source", ["environment", "yaml"])
def test_cli_uses_selected_mode_instructions(tmp_path, monkeypatch, model, capsys, source):
    from tests.test_repository import report_reference

    modes = {
        "explainer": {"instructions": "Report reference=approved. Preserve ${release_tag} literally."},
        "fixer": {"instructions": "Report reference=repair."},
    }
    if source == "environment":
        monkeypatch.setenv("LANDING_MODES", json.dumps(modes))
    else:
        config = tmp_path / "landing.yml"
        config.write_text(json.dumps({"modes": modes}))
        monkeypatch.setenv("LANDING_CONFIG", str(config))

    async def report(**kwargs):
        reply = (await report_reference(**kwargs)).choices[0].message.content
        if "${release_tag}" in str(kwargs["messages"]):
            reply += "; ${release_tag}"
        return completion(reply)

    responses, _ = model
    responses.extend([report, report])
    for command, expected in (
        ("explain", "Deployment reference: approved; ${release_tag}"),
        ("fix", "Deployment reference: repair"),
    ):
        assert (
            main([
                "--db",
                str(tmp_path / f"{command}.sqlite3"),
                command,
                "Identify deployment.",
                "--workspace",
                str(tmp_path),
                "--json",
            ])
            == 0
        )
        assert json.loads(capsys.readouterr().out)["result"] == expected

"""Exercise model configuration through the public CLI and provider protocol."""

import asyncio
import json
import os
import subprocess
import sys

import pytest

from tests.conftest import completion
from tests.test_dogfood import provider


@pytest.mark.parametrize(
    "source",
    [
        "landing-env",
        "legacy-env",
        "legacy-file",
        "landing-file",
        "precedence",
        "custom-file",
        "empty-env",
        "provider-specific",
    ],
)
def test_cli_model_configuration_and_legacy_fallback(tmp_path, source):
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
            "client_args": {"max_retries": 0},
            "completion_args": {"temperature": 0.2},
            "max_tokens": 128,
        }
        if source.endswith("env"):
            prefix = "LANDING_" if source == "landing-env" else "BUB_"
            environment.update({
                prefix + key.upper(): json.dumps(value) if isinstance(value, dict) else str(value)
                for key, value in settings.items()
            })
        else:
            legacy = tmp_path / ".bub"
            legacy.mkdir()
            legacy_settings = {**settings, "model": "openai:legacy-model"}
            if source == "provider-specific":
                del legacy_settings["api_key"], legacy_settings["api_base"]
                environment.update(BUB_OPENAI_API_KEY="selected-key", BUB_OPENAI_API_BASE=api_base)
            elif source == "legacy-file":
                environment.update(BUB_OPENAI_API_KEY="ignored-key", BUB_OPENAI_API_BASE="http://127.0.0.1:9/v1")
            (legacy / "config.yml").write_text(json.dumps(legacy_settings))
            if source in {"landing-file", "precedence", "custom-file"}:
                landing = tmp_path / ".landing"
                landing.mkdir()
                config_file = landing / ("custom.yml" if source == "custom-file" else "config.yml")
                config_file.write_text(json.dumps({"model": "openai:selected-model"}))
                if source == "custom-file":
                    environment["LANDING_CONFIG"] = str(config_file)
            if source in {"legacy-file", "empty-env", "provider-specific"}:
                settings["model"] = "openai:legacy-model"
            if source == "empty-env":
                environment.update(LANDING_MODEL="", LANDING_API_BASE="")
            if source == "precedence":
                environment.update(LANDING_MODEL="openai:environment-model", BUB_MODEL="openai:ignored-model")
                settings["model"] = "openai:environment-model"
        result = subprocess.run(  # noqa: S603 -- run the public CLI against a disposable provider and workspace.
            [
                sys.executable,
                "-m",
                "landing",
                "--db",
                str(tmp_path / "landing.sqlite3"),
                "explainer",
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
        assert requests[0]["temperature"] == 0.2
        assert requests[0].get("max_tokens", requests[0].get("max_completion_tokens")) == 128
        assert authorization == ["Bearer selected-key"]


def test_runtime_refreshes_configuration_after_existing_sdk_use(tmp_path, monkeypatch, model):
    from bub import BubFramework, ensure_config
    from bub.builtin.settings import AgentSettings

    from landing.models import ActionRequest
    from landing.runtime import Runtime

    monkeypatch.setenv("HOME", str(tmp_path))
    BubFramework(config_file=tmp_path / "unused.yml")
    ensure_config(AgentSettings)
    responses, requests = model

    async def run():
        for number in (1, 2):
            monkeypatch.setenv("LANDING_MODEL", f"openai:configured-{number}")
            responses.append(completion(f"Explanation {number}."))
            async with Runtime(tmp_path / f"action-{number}.sqlite3").running() as runtime:
                action = await runtime.run(
                    ActionRequest(mode="explainer", instruction="Explain the failure.", workspace=str(tmp_path))
                )
            assert action.result == f"Explanation {number}."
            assert requests[-1]["model"] == f"openai:configured-{number}"

    asyncio.run(run())

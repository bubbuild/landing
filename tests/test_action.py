"""Exercise the public Action entry point and provider protocol end to end."""

import json
import os
import subprocess
import sys
from contextlib import closing

import pytest

from landing.tasks import Tasks
from tests.conftest import completion
from tests.provider import provider


def delegate(tmp_path, api_base, command, checks, *, extra_env=None):
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith(("LANDING_", "BUB_", "GITHUB_", "INPUT_"))
    }
    environment.update(
        LANDING_MODEL="openai:test-model",
        LANDING_API_KEY="test-key",
        LANDING_API_BASE=api_base,
        LANDING_CLIENT_ARGS='{"max_retries":0}',
        LANDING_MODEL_TIMEOUT_SECONDS="5",
        INPUT_COMMAND=command,
        INPUT_INSTRUCTION="Carry out the requested work.",
        INPUT_CHECKS=checks,
        INPUT_DATABASE=str(tmp_path / "landing.sqlite3"),
        GITHUB_REPOSITORY="example/landing",
        GITHUB_RUN_ID="acceptance",
        GITHUB_OUTPUT=str(tmp_path / "outputs.txt"),
        GITHUB_STEP_SUMMARY=str(tmp_path / "summary.md"),
    )
    environment.update(extra_env or {})
    return subprocess.run(  # noqa: S603 -- fixed Python module entry point in a disposable workspace.
        [sys.executable, "-m", "landing.action"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.parametrize(("check", "decision"), [("true", "allow"), ("false", "block")])
def test_action_reports_advisory_gate_separately_from_native_checks(tmp_path, check, decision):
    responses = [completion(tool="decide", arguments={"decision": "allow"}), completion("Reviewed the candidate.")]
    with provider(responses) as (api_base, _):
        result = delegate(tmp_path, api_base, "review", check)
    assert result.returncode == 0, result.stderr
    action = json.loads(result.stdout)
    assert action["mode"] == "gatekeeper"
    assert action["decision"] == decision
    assert action["status"] == "completed"
    assert action["id"] in (tmp_path / "outputs.txt").read_text()
    assert "Reviewed the candidate." in (tmp_path / "summary.md").read_text()


def test_action_retains_failed_fix_and_validation(tmp_path):
    responses = [
        completion(tool="fs_write", arguments={"path": "answer with spaces.txt", "content": "42\n"}),
        completion("Wrote the candidate; independent validation follows."),
    ]
    with provider(responses) as (api_base, _):
        result = delegate(tmp_path, api_base, "fix", "false")
    assert result.returncode == 1, result.stderr
    action = json.loads(result.stdout)
    assert action["status"] == "failed"
    assert (tmp_path / "answer with spaces.txt").read_text() == "42\n"
    with closing(Tasks(tmp_path / "landing.sqlite3")) as tasks:
        saved = tasks.get(action["id"])
        assert saved.result == "Wrote the candidate; independent validation follows."
        assert saved.error is not None
        assert "validation failed" in saved.error["message"]


def test_action_does_not_attribute_unknown_checks_to_the_trigger_revision(tmp_path):
    def explain(request):
        answer = (
            "The checked revision is unknown."
            if "unrelated-main-revision" not in str(request["messages"])
            else "The checks covered unrelated-main-revision."
        )
        return completion(answer)

    with provider([explain]) as (api_base, _):
        result = delegate(
            tmp_path,
            api_base,
            "triage",
            "",
            extra_env={"INPUT_CHECKED_REVISION": "", "GITHUB_SHA": "unrelated-main-revision"},
        )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["result"] == "The checked revision is unknown."


def test_action_preserves_instruction_assignments_and_quotes(tmp_path):
    instruction = 'Native checks: quality=success. Explain "key=value" in https://example.test/?page=2.'

    def explain(request):
        answer = (
            "The quality check passed; key=value and page=2 remain task evidence."
            if instruction in str(request["messages"])
            else "The instruction is unavailable."
        )
        return completion(answer)

    with provider([explain]) as (api_base, _):
        result = delegate(tmp_path, api_base, "explain", "", extra_env={"INPUT_INSTRUCTION": instruction})
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["result"] == "The quality check passed; key=value and page=2 remain task evidence."

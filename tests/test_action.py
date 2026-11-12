"""Exercise the public Action entry point and provider protocol end to end."""

import json
import os
import signal
import subprocess
import sys
import time

import pytest

from landing.database import open_database
from landing.tasks import Tasks
from tests.conftest import completion
from tests.provider import provider


def delegate(tmp_path, api_base, command, checks, *, extra_env=None, cancel_when=None):
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith(("LANDING_", "BUB_", "GITHUB_", "INPUT_"))
    }
    environment.update(
        LANDING_MODEL="openai:test-model",
        LANDING_API_KEY="test-key",
        LANDING_API_BASE=api_base,
        LANDING_CLIENT_ARGS='{"api_format":"chat"}',
        LANDING_MODEL_TIMEOUT_SECONDS="5",
        INPUT_COMMAND=command,
        INPUT_INSTRUCTION="Carry out the requested work.",
        INPUT_CHECKS=checks,
        LANDING_DB=str(tmp_path / "landing.sqlite3"),
        GITHUB_REPOSITORY="example/landing",
        GITHUB_RUN_ID="acceptance",
        GITHUB_OUTPUT=str(tmp_path / "outputs.txt"),
        GITHUB_STEP_SUMMARY=str(tmp_path / "summary.md"),
    )
    environment.update(extra_env or {})
    args = [sys.executable, "-m", "landing", "github", "event"]
    if cancel_when is None:
        return subprocess.run(args, cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=30)  # noqa: S603 -- fixed entry point in a disposable workspace.
    with subprocess.Popen(  # noqa: S603 -- fixed public Action entry point in a disposable workspace.
        args, cwd=tmp_path, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    ) as process:
        deadline = time.monotonic() + 10
        while not cancel_when.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert cancel_when.exists(), "The delegated shell did not start."
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=10)
        return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)


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


def _outputs(path):
    """Read the GitHub output file format this Action writes."""
    values, name, delimiter = {}, None, ""
    for line in path.read_text().splitlines():
        if name is None:
            key, marker, value = line.partition("<<")
            name, delimiter = (key, value) if marker else (None, "")
            if name is not None:
                values[name] = ""
        elif line == delimiter:
            name = None
        else:
            values[name] += line + "\n"
    return {key: value.strip() for key, value in values.items()}


def test_action_reports_a_skipped_event_separately_from_a_failed_step(tmp_path):
    with provider([]) as (api_base, _):
        result = delegate(
            tmp_path,
            api_base,
            "review",
            "",
            extra_env={"LANDING_GITHUB_REPOSITORY": "other/repository"},
        )
    assert result.returncode == 2, result.stderr
    assert "workflow repository" in result.stderr
    outputs = _outputs(tmp_path / "outputs.txt")
    # A broken invocation must not look like an event this Action chose not to handle.
    assert outputs["status"] == "failed"
    assert outputs["id"] == ""
    assert "workflow repository" in outputs["result"]


def test_action_and_cli_agree_on_the_recorded_outcome_for_advisory_gates(tmp_path, capsys):
    responses = [completion(tool="decide", arguments={"decision": "block"}), completion("One retry defect.")]
    with provider(responses) as (api_base, _):
        result = delegate(tmp_path, api_base, "review", "")
    assert result.returncode == 0, result.stderr
    action = json.loads(result.stdout)
    assert (action["status"], action["decision"]) == ("completed", "block")
    outputs = _outputs(tmp_path / "outputs.txt")
    assert (outputs["id"], outputs["status"], outputs["decision"]) == (action["id"], "completed", "block")
    with open_database(tmp_path / "landing.sqlite3") as engine:
        saved = Tasks(engine).get(action["id"])
    # One record, rendered by each entry: the Action reports it without failing the advisory gate.
    assert (saved.result, saved.decision, saved.error) == ("One retry defect.", "block", None)


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
    with open_database(tmp_path / "landing.sqlite3") as engine:
        tasks = Tasks(engine)
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
            extra_env={"LANDING_GITHUB_CHECKED_REVISION": "", "GITHUB_SHA": "unrelated-main-revision"},
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
        result = delegate(
            tmp_path,
            api_base,
            "explain",
            "",
            extra_env={"INPUT_INSTRUCTION": instruction},
        )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["result"] == "The quality check passed; key=value and page=2 remain task evidence."


def test_action_rejects_cross_repository_target_before_model_call(tmp_path):
    with provider([]) as (api_base, requests):
        result = delegate(tmp_path, api_base, "review", "", extra_env={"LANDING_GITHUB_REPOSITORY": "other/repository"})
    assert result.returncode != 0
    assert "workflow repository" in result.stderr
    assert not requests
    assert not (tmp_path / "landing.sqlite3").exists()


def test_runner_termination_cancels_work_and_stops_its_shell(tmp_path):
    child = tmp_path / "child.pid"
    response = completion(
        tool="bash", arguments={"command": "sleep 60 & echo $! > child.pid; wait", "timeout_seconds": 60}
    )
    with provider([response]) as (api_base, _):
        result = delegate(tmp_path, api_base, "fix", "", cancel_when=child)
    assert result.returncode != 0
    with open_database(tmp_path / "landing.sqlite3") as engine:
        tasks = Tasks(engine)
        actions = tasks.list()
        assert len(actions) == 1
        assert actions[0].status == "cancelled"
    with pytest.raises(ProcessLookupError):
        os.kill(int(child.read_text()), 0)


@pytest.mark.parametrize("event_name", ["release", "workflow_run", "schedule"])
def test_native_source_uses_github_authorization_for_app_identity(tmp_path, event_name):
    binary = tmp_path / "bin"
    binary.mkdir()
    executable = binary / "gh"
    executable.write_text('#!/bin/sh\necho "GitHub identity lookup is unavailable" >&2\nexit 1\n')
    executable.chmod(0o755)
    event = {
        "repository": {"full_name": "example/landing", "default_branch": "main"},
        "sender": {"type": "Bot", "login": "team-app[bot]"},
    }
    if event_name == "workflow_run":
        event["workflow_run"] = {
            "id": 123,
            "name": "release-main",
            "head_repository": {"full_name": "example/landing"},
            "actor": event["sender"],
            "head_branch": "0.1.2",
            "event": "release",
        }
    source = tmp_path / "event.json"
    source.write_text(json.dumps(event))
    with provider([completion("Explained the requested release evidence.")]) as (api_base, requests):
        result = delegate(
            tmp_path,
            api_base,
            "explain",
            "",
            extra_env={
                "GITHUB_ACTIONS": "true",
                "GITHUB_EVENT_NAME": event_name,
                "GITHUB_EVENT_PATH": str(source),
                "LANDING_GITHUB_UPSTREAM_WORKFLOW": "release-main",
                "PATH": str(binary) + os.pathsep + os.environ["PATH"],
            },
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["status"] == "completed"
        assert requests


def test_native_execution_does_not_expose_dispatch_signing_key(tmp_path):
    with provider([completion("Finished maintenance.")]) as (api_base, _):
        result = delegate(
            tmp_path,
            api_base,
            "bump",
            'test -z "$LANDING_GITHUB_WEBHOOK_SECRET"',
            extra_env={"LANDING_GITHUB_WEBHOOK_SECRET": "signing-fixture"},
        )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "completed"

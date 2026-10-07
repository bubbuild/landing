import json
import os
import subprocess
import sys

import pytest
from bub import BubFramework
from typer.testing import CliRunner

from landing.cli import create_cli_app, main
from landing.runtime import Runtime
from tests.conftest import completion


@pytest.mark.parametrize(
    ("command", "mode"),
    [
        ("triage", "issuer"),
        ("fix", "fixer"),
        ("review", "gatekeeper"),
        ("explain", "explainer"),
    ],
)
def test_cli_modes_complete_and_reopen_history(tmp_path, model, capsys, command, mode):
    responses, _ = model
    if mode == "gatekeeper":
        responses.append(completion(tool="decide", arguments={"decision": "allow"}))
    responses.append(completion("The requested work is complete."))
    database = str(tmp_path / "landing.sqlite3")
    source = tmp_path / "evidence.txt"
    source.write_text("A reproducible example.")
    assert main(["--db", database, command, "Review the example.", "--input", str(source), "--json"]) == 0
    action = json.loads(capsys.readouterr().out)
    assert action["result"] == "The requested work is complete."
    assert action["mode"] == mode
    assert main(["--db", database, "action", "view", action["id"], "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == action


@pytest.mark.parametrize("decision", ["allow", "block", "inconclusive"])
def test_ci_exit_status(tmp_path, model, capsys, decision):
    responses, _ = model
    responses.extend([
        completion(tool="decide", arguments={"decision": decision}),
        completion("Reviewed the evidence."),
    ])
    assert main(["--db", str(tmp_path / "landing.sqlite3"), "review", "Review the change.", "--json"]) == (
        decision != "allow"
    )
    assert json.loads(capsys.readouterr().out)["decision"] == decision


def test_usage_errors_have_exit_two_and_json(tmp_path, capsys):
    assert main(["--db", str(tmp_path / "landing.sqlite3"), "triage", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "invalid_request"
    assert main(["action", "list", "--limit", "0", "--json"]) == 2
    diagnostic = capsys.readouterr()
    assert not diagnostic.out
    assert "limit" in diagnostic.err


def test_cli_reads_piped_evidence_and_writes_json_result(tmp_path, model):
    from tests.test_repository import report_reference

    responses, _ = model
    responses.append(report_reference)
    destination = tmp_path / "result.json"
    result = CliRunner().invoke(
        create_cli_app(),
        ["--db", str(tmp_path / "landing.sqlite3"), "explain", "--input", "-", "--output", str(destination), "--json"],
        input="reference=approved",
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["result"] == "Deployment reference: approved"
    assert json.loads(destination.read_text())["result"] == "Deployment reference: approved"


def run_in_shell(*arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 -- exercise the installed CLI with explicit arguments.
        ["/bin/bash", "-c", '"$0" "$@" || exit', sys.executable, *arguments],
        capture_output=True,
        text=True,
        env={**os.environ, "GITHUB_ACTIONS": "true", "FORCE_COLOR": "1"},
        check=False,
    )


@pytest.mark.parametrize(
    ("arguments", "status"),
    [
        (["triage", "--help"], 0),
        (["--show-completion"], 0),
        (["action", "list", "--limit", "0", "--json"], 2),
    ],
)
def test_captured_help_and_diagnostics_remain_plain_in_ci(arguments, status):
    # Without /proc, shellingham lists processes with ps, which omits processes lacking a terminal (macOS CI).
    if (
        "--show-completion" in arguments
        and run_in_shell("-c", "import shellingham; shellingham.detect_shell()").returncode
    ):
        pytest.skip("Typer cannot detect the calling shell in this environment.")
    result = run_in_shell("-m", "landing", *arguments)
    assert result.returncode == status
    assert "\x1b[" not in result.stdout + result.stderr
    if status:
        assert not result.stdout
        assert "--limit" in result.stderr
    elif "--show-completion" in arguments:
        assert "_LANDING_COMPLETE" in result.stdout
    else:
        assert "--help" in result.stdout


@pytest.mark.parametrize("native_host", [False, True])
def test_fix_uses_selected_workspace_for_files_and_shell(tmp_path, model, monkeypatch, native_host):
    responses, _ = model
    caller = tmp_path / "caller"
    candidate = tmp_path / "candidate"
    caller.mkdir()
    candidate.mkdir()
    monkeypatch.chdir(caller)
    responses.extend([
        completion(tool="fs_write", arguments={"path": "answer.txt", "content": "42"}),
        completion(tool="bash", arguments={"command": 'test "$(cat answer.txt)" = 42'}),
        completion("Wrote and checked the candidate's answer."),
    ])
    database = tmp_path / "landing.sqlite3"
    app = Runtime(database, framework=BubFramework()).framework.create_cli_app() if native_host else create_cli_app()
    options = ["--workspace", str(candidate)] if native_host else ["--db", str(database)]
    workspace = [] if native_host else ["--workspace", str(candidate)]
    runner = CliRunner()
    result = runner.invoke(app, [*options, "fix", "Write the answer.", *workspace, "--json"])
    assert result.exit_code == 0, result.output
    action = json.loads(result.stdout)
    assert action["status"] == "completed"
    result = runner.invoke(app, [*options[:2], "action", "list", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [action]
    assert (candidate / "answer.txt").read_text() == "42"
    assert not (caller / "answer.txt").exists()


@pytest.mark.parametrize("workspace", [None, "../candidate"])
def test_retry_keeps_original_workspace_after_changing_directory(tmp_path, model, monkeypatch, capsys, workspace):
    candidate = tmp_path / "candidate"
    caller = tmp_path / "caller"
    candidate.mkdir()
    caller.mkdir()
    monkeypatch.chdir(candidate if workspace is None else caller)
    responses, _ = model
    responses.extend([
        completion(tool="fs_write", arguments={"path": "answer.txt", "content": "initial"}),
        completion("Saved the answer."),
        completion(tool="fs_write", arguments={"path": "answer.txt", "content": "retried"}),
        completion("Saved the retry."),
    ])
    options = ["--db", str(tmp_path / "landing.sqlite3")]
    selection = ["--workspace", workspace] if workspace is not None else []
    assert main([*options, "fix", "Write the answer.", *selection, "--json"]) == 0
    action = json.loads(capsys.readouterr().out)
    assert (candidate / "answer.txt").read_text() == "initial"
    elsewhere = tmp_path / "other" / "caller"
    elsewhere.mkdir(parents=True)
    alternate = elsewhere.parent / "candidate"
    alternate.mkdir()
    monkeypatch.chdir(elsewhere)
    assert main([*options, "action", "retry", action["id"], "--json"]) == 0
    assert (candidate / "answer.txt").read_text() == "retried"
    assert not (elsewhere / "answer.txt").exists()
    assert not (alternate / "answer.txt").exists()


def test_malformed_tool_call_leaves_inspectable_diagnostics_without_arguments(tmp_path, model, capsys):
    responses, _ = model
    response = completion(tool="bash", arguments={"command": "unused"})
    response.choices[0].message.tool_calls[0].function.arguments = '{"command": "sensitive-task-value"'
    responses.append(response)
    database = str(tmp_path / "landing.sqlite3")
    assert main(["--db", database, "review", "Review the candidate.", "--json"]) == 1
    action = json.loads(capsys.readouterr().out)
    assert action["status"] == "failed"
    assert action["decision"] is None
    assert main(["--db", database, "action", "logs", action["id"]]) == 0
    diagnostic = capsys.readouterr().out
    assert "json_invalid" in diagnostic
    assert "bash" in diagnostic
    assert "sensitive-task-value" not in diagnostic

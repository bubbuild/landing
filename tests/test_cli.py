import json

import pytest

from landing.cli import main
from tests.conftest import completion


@pytest.mark.parametrize("mode", ["issuer", "fixer", "gatekeeper", "explainer"])
def test_cli_modes_complete_and_reopen_history(tmp_path, model, capsys, mode):
    responses, _ = model
    if mode == "gatekeeper":
        responses.append(completion(tool="decide", arguments={"decision": "allow"}))
    responses.append(completion("The requested work is complete."))
    database = str(tmp_path / "landing.sqlite3")
    source = tmp_path / "evidence.txt"
    source.write_text("A reproducible example.")
    assert main(["--db", database, mode, "Review the example.", "--input", str(source), "--json"]) == 0
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
    assert main(["--db", str(tmp_path / "landing.sqlite3"), "gatekeeper", "Review the change.", "--json"]) == (
        decision != "allow"
    )
    assert json.loads(capsys.readouterr().out)["decision"] == decision


def test_usage_errors_have_exit_two_and_json(tmp_path, capsys):
    assert main(["--db", str(tmp_path / "landing.sqlite3"), "issuer", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "invalid_request"
    assert main(["explainer", "Explain the failure.", "--detach"]) == 2
    assert "--detach requires --server" in capsys.readouterr().err

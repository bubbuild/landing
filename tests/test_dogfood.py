"""Run the CI script through the real CLI and SDK against a local model endpoint."""

import json
import os
import subprocess
import sys
from collections import deque
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from tests.conftest import completion

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "dogfood.sh"


def command(*args, cwd=None, env=None):
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, check=True, timeout=60)  # noqa: S603 -- explicit commands in a disposable integration fixture.


@contextmanager
def provider(responses):
    pending = deque(responses)
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            response = pending.popleft().model_dump()
            choice = response["choices"][0]
            delta = {key: value for key, value in choice["message"].items() if value is not None}
            for index, tool in enumerate(delta.get("tool_calls", [])):
                tool["index"] = index
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for content, finish in ((delta, None), ({}, choice["finish_reason"])):
                chunk = {
                    "id": response["id"],
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "test-model",
                    "choices": [{"index": 0, "delta": content, "finish_reason": finish}],
                }
                self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
            self.wfile.write(b"data: [DONE]\n\n")

        def log_message(self, format: str, *args) -> None:  # noqa: A002 -- the HTTP handler's public parameter name.
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests
        assert not pending
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture
def checkout(tmp_path):
    workspace = tmp_path / "checkout"
    workspace.mkdir()
    command("git", "init", "--initial-branch=dogfood-fixture", cwd=workspace)
    command("git", "config", "user.name", "Landing Test", cwd=workspace)
    command("git", "config", "user.email", "landing@example.test", cwd=workspace)
    (workspace / "candidate.txt").write_text("Initial candidate.\n")
    command("git", "add", ".", cwd=workspace)
    command("git", "commit", "-m", "Initial candidate", cwd=workspace)
    base = command("git", "rev-parse", "HEAD", cwd=workspace).stdout.strip()
    for number in (1, 2):
        (workspace / f"change-{number}.txt").write_text(f"Change {number}.\n")
        command("git", "add", ".", cwd=workspace)
        command("git", "commit", "-m", f"Candidate change {number}", cwd=workspace)
    return workspace, base


def dogfood(workspace, base, evidence, api_base, mode, check):
    environment = {key: value for key, value in os.environ.items() if not key.startswith(("BUB_", "LANDING_"))}
    environment.update(
        BUB_MODEL="openai:test-model",
        BUB_API_KEY="test-key",
        BUB_API_BASE=api_base,
        BUB_CLIENT_ARGS='{"max_retries":0}',
        BUB_MODEL_TIMEOUT_SECONDS="5",
        LANDING_BASE_REVISION=base,
        LANDING_ACTION_TIMEOUT_SECONDS="20",
        LANDING_EXPLANATION_TIMEOUT_SECONDS="20",
        PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
    )
    return subprocess.run(  # noqa: S603 -- the repository's script runs only inside the disposable checkout.
        ["/bin/bash", str(SCRIPT), mode, "Review the candidate.", str(evidence), check],
        cwd=workspace,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.mark.parametrize(
    ("check", "decision", "exit_code"),
    [("true", "allow", 0), ("printf 'validation failed'; exit 1", "allow", 1), ("true", None, 1)],
)
def test_gate_and_failure_explanation_preserve_evidence(tmp_path, checkout, check, decision, exit_code):
    workspace, base = checkout
    evidence = tmp_path / "evidence"
    responses = []
    if decision:
        responses.append(completion(tool="decide", arguments={"decision": decision}))
    responses.append(completion("Reviewed the candidate."))
    if exit_code:
        responses.append(completion("The recorded evidence explains why the candidate cannot proceed."))
    with provider(responses) as (api_base, requests):
        result = dogfood(workspace, base, evidence, api_base, "gatekeeper", check)
        assert result.returncode == exit_code, result.stderr + result.stdout + (evidence / "action.log").read_text()
        action = json.loads((evidence / "result.json").read_text())
        assert action["decision"] == ("block" if "exit 1" in check else decision or "inconclusive")
        events = json.loads((evidence / "events.json").read_text())
        assert next(event for event in events if event["type"] == "validation")["data"]["command"] == check
        if exit_code:
            explanation = json.loads((evidence / "explanation.json").read_text())
            assert explanation["status"] == "completed"
            assert (evidence / "explanation-exit-status.txt").read_text().strip() == "0"
            assert action["id"] in json.dumps(requests[-1]["messages"])
            if "exit 1" in check:
                assert "validation failed" in json.dumps(requests[-1]["messages"])
        else:
            assert not (evidence / "explanation.json").exists()
    assert "Change 1." in (evidence / "candidate.diff").read_text()
    assert "Change 2." in (evidence / "candidate.diff").read_text()
    assert "Reviewed the candidate." in (evidence / "summary.md").read_text()
    assert (evidence / "landing.sqlite3").is_file()


def test_manual_fixer_saves_new_files_without_staging(tmp_path, checkout):
    workspace, base = checkout
    evidence = tmp_path / "evidence"
    responses = [
        completion(tool="fs_write", arguments={"path": "answer with spaces.txt", "content": "42\n"}),
        completion("Wrote and validated the answer."),
    ]
    with provider(responses) as (api_base, _):
        result = dogfood(workspace, base, evidence, api_base, "fixer", "test \"$(cat 'answer with spaces.txt')\" = 42")
        assert result.returncode == 0, result.stderr + result.stdout + (evidence / "action.log").read_text()
    assert "answer with spaces.txt" in (evidence / "workspace.diff").read_text()
    assert "+42" in (evidence / "workspace.diff").read_text()
    assert command("git", "diff", "--cached", cwd=workspace).stdout == ""

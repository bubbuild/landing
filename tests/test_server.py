"""The receiver forwards GitHub's signed event, without owning a job queue."""

import hashlib
import hmac
import json
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from landing.server import ServerSettings, create_app


@pytest.fixture
def receiver(tmp_path, monkeypatch):
    for name in tuple(os.environ):
        if name.startswith(("GITHUB_", "INPUT_")):
            monkeypatch.delenv(name)
    monkeypatch.setenv("LANDING_RUNNER", "gha")
    monkeypatch.setenv("LANDING_GITHUB_WEBHOOK_SECRET", "webhook-fixture")
    state = tmp_path / "github.json"
    state.write_text(json.dumps({"dispatches": []}))
    monkeypatch.setenv("TEST_GITHUB_STATE", str(state))
    executable = tmp_path / "gh"
    executable.write_text("""#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
path = Path(os.environ["TEST_GITHUB_STATE"])
state = json.loads(path.read_text())
state["dispatches"].append({"endpoint": sys.argv[2], **json.load(sys.stdin)})
path.write_text(json.dumps(state))
if state.get("wait"):
    time.sleep(60)
if state.get("lost_receipt"):
    raise SystemExit("Dispatch response lost")
if state.get("empty_response"):
    raise SystemExit(0)
print(json.dumps({"workflow_run_id": 123, "html_url": "https://github.com/example/landing/actions/runs/123"}))
""")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    return state


def event(body="/landing explain this failure"):
    return {
        "repository": {"full_name": "example/landing", "default_branch": "main"},
        "action": "created",
        "sender": {"login": "writer"},
        "comment": {"id": 7, "body": body, "user": {"login": "writer"}},
        "issue": {"number": 42},
    }


def post(client, payload, *, delivery_id=None, name="issue_comment", signed=True):
    body = json.dumps(payload, ensure_ascii=False).encode()
    headers = {
        "Content-Type": "application/json",
        "X-GitHub-Delivery": delivery_id or str(uuid4()),
        "X-GitHub-Event": name,
        "X-Hub-Signature-256": "sha256="
        + hmac.new(b"webhook-fixture" if signed else b"wrong", body, hashlib.sha256).hexdigest(),
    }
    return client.post("/webhooks/github", content=body, headers=headers)


def test_dispatch_returns_github_receipt_and_preserves_original_event(receiver):
    with TestClient(create_app()) as client:
        payload = event("/landing explain 这次失败")
        response = post(client, payload, delivery_id="opaque-delivery")
        assert response.status_code == 202
        assert response.json()["workflow_run_id"] == 123
        dispatched = json.loads(receiver.read_text())["dispatches"][0]
        assert dispatched["ref"] == "main"
        inputs = dispatched["inputs"]
        assert json.loads(inputs["event"]) == payload
        assert inputs["event_name"] == "issue_comment"
        assert inputs["delivery"] == "opaque-delivery"
        assert (
            inputs["signature"]
            == "sha256=" + hmac.new(b"webhook-fixture", inputs["event"].encode(), hashlib.sha256).hexdigest()
        )
        assert client.get("/up").status_code == 200
        assert client.get("/v1/github/deliveries/opaque-delivery").status_code == 404


def test_repository_and_default_branch_come_from_verified_github_event(receiver, monkeypatch):
    monkeypatch.setenv("LANDING_GITHUB_WORKFLOW", "12345")
    with TestClient(create_app()) as client:
        payload = event()
        payload["repository"] = {"full_name": "another/project", "default_branch": "develop"}
        assert post(client, payload).status_code == 202
    dispatched = json.loads(receiver.read_text())["dispatches"][0]
    assert dispatched["endpoint"] == "repos/another/project/actions/workflows/12345/dispatches"
    assert dispatched["ref"] == "develop"


def test_invalid_signature_or_payload_never_dispatches(receiver):
    with TestClient(create_app()) as client:
        assert post(client, event(), signed=False).status_code == 401
        assert post(client, []).status_code == 422
        assert client.post("/v1/actions", json={"mode": "fixer", "instruction": "old API"}).status_code == 404
    assert json.loads(receiver.read_text())["dispatches"] == []


@pytest.mark.parametrize(
    ("body", "name"),
    [
        ("ordinary discussion", "issue_comment"),
        ("<!-- landing:reply --> result", "issue_comment"),
        ("/landing fix issue", "workflow_dispatch"),
    ],
)
def test_unrelated_events_are_acknowledged_without_dispatch(receiver, body, name):
    with TestClient(create_app()) as client:
        assert post(client, event(body), name=name).status_code == 204
    assert json.loads(receiver.read_text())["dispatches"] == []


def test_failed_dispatch_remains_redeliverable(receiver):
    receiver.write_text(json.dumps({"dispatches": [], "lost_receipt": True}))
    with TestClient(create_app()) as client:
        assert post(client, event(), delivery_id="retry-me").status_code == 502
        state = json.loads(receiver.read_text())
        state["lost_receipt"] = False
        receiver.write_text(json.dumps(state))
        assert post(client, event(), delivery_id="retry-me").status_code == 202
    # A lost response can hide a successful remote write. The bridge does not promise exactly-once execution.
    assert len(json.loads(receiver.read_text())["dispatches"]) == 2


def test_executor_workflow_never_recurses(receiver):
    payload = {
        "repository": {"full_name": "example/landing", "default_branch": "main"},
        "action": "completed",
        "workflow_run": {"path": ".github/workflows/landing-execute.yml", "conclusion": "failure"},
    }
    with TestClient(create_app()) as client:
        assert post(client, payload, name="workflow_run").status_code == 204
    assert json.loads(receiver.read_text())["dispatches"] == []


def test_slow_dispatch_is_reported_as_failure_not_acceptance(receiver):
    receiver.write_text(json.dumps({"dispatches": [], "wait": True}))
    with TestClient(create_app()) as client:
        assert post(client, event()).status_code == 502


def test_github_acceptance_does_not_require_run_metadata(receiver):
    receiver.write_text(json.dumps({"dispatches": [], "empty_response": True}))
    with TestClient(create_app()) as client:
        response = post(client, event())
        assert response.status_code == 202
        assert response.json() == {}


def test_dispatch_only_relay_never_builds_execution_framework(receiver, monkeypatch):
    def refuse(*args, **kwargs):
        message = "A dispatch-only relay must not construct the execution framework."
        raise AssertionError(message)

    monkeypatch.setattr("landing.server.BubFramework", refuse)
    app = create_app(options=ServerSettings(runner="gha"))
    with TestClient(app) as client:
        assert post(client, event()).status_code == 202
        assert client.get("/up").status_code == 200
    assert len(json.loads(receiver.read_text())["dispatches"]) == 1


def test_ping_needs_no_repository_identity(receiver):
    with TestClient(create_app()) as client:
        assert post(client, {"zen": "Keep delivery simple."}, name="ping").status_code == 204
    assert json.loads(receiver.read_text())["dispatches"] == []

"""Local webhook execution uses the same native runner without Actions context."""

import hashlib
import hmac
import json
import os
import time

import pytest
from fastapi.testclient import TestClient

from landing.adapters.github.publication import marker
from landing.database import open_database
from landing.server import create_app
from landing.tasks import Tasks
from tests.conftest import completion
from tests.provider import provider
from tests.test_github import platform as platform
from tests.test_server import event, post


def eventually(check):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            if result := check():
                return result
        except (OSError, ValueError):
            pass
        time.sleep(0.05)
    pytest.fail("Local execution did not reach the expected outcome")


@pytest.fixture
def local(tmp_path, monkeypatch, platform):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LANDING_RUNNER", "local")
    monkeypatch.setenv("LANDING_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("LANDING_DB", str(tmp_path / "landing.sqlite3"))
    monkeypatch.setenv("LANDING_GITHUB_WEBHOOK_SECRET", "webhook-fixture")
    monkeypatch.setenv("LANDING_MODEL", "openai:test-model")
    monkeypatch.setenv("LANDING_API_KEY", "test-key")
    monkeypatch.setenv("LANDING_CLIENT_ARGS", '{"api_format":"chat"}')
    return tmp_path / "landing.sqlite3"


def answers(payload, text, reply_id):
    signature = (
        "sha256="
        + hmac.new(b"webhook-fixture", json.dumps(payload, ensure_ascii=False).encode(), hashlib.sha256).hexdigest()
    )
    body = marker("explainer", f"webhook:{signature}") + "\n" + text
    return [
        completion(tool="fs_write", arguments={"path": "reply.json", "content": json.dumps({"body": body})}),
        completion(
            tool="bash", arguments={"command": "gh api repos/example/landing/issues/42/comments --input reply.json"}
        ),
        completion(tool="confirm_reply", arguments={"comment_id": reply_id}),
        completion(text),
    ]


def completed(database, count=1):
    with open_database(database) as engine:
        records = Tasks(engine).list()
    return records if sum(record.status == "completed" for record in records) == count else None


def test_local_worker_publishes_and_recovers_thread_history_without_gha(local, platform, monkeypatch, tmp_path):
    # A prepared checkout must not shadow the installed runner before authorization.
    (tmp_path / "landing.py").write_text("raise RuntimeError('Do not load the runner from the checkout')\n")
    first = event("/landing explain Remember reference=first-evidence.")
    second = event("/landing explain Reuse the earlier evidence.")
    second["comment"]["id"] = 8
    # Even a host launched from CI must not lend its Actions identity or outputs to the local worker.
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_REPOSITORY", "unrelated/repository")
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "parent-output"))

    def recall(request):
        text = (
            "Recovered reference=first-evidence"
            if "reference=first-evidence" in str(request["messages"])
            else "Earlier evidence unavailable"
        )
        return answers(second, text, 2)[0]

    responses = [*answers(first, "reference=first-evidence", 1), recall, *answers(second, "Reused evidence.", 2)[1:]]
    with provider(responses) as (url, _):
        monkeypatch.setenv("LANDING_API_BASE", url)
        app = create_app()
        with TestClient(app) as client:
            assert post(client, first).status_code == 202
            assert client.get("/up").status_code == 200
            assert eventually(lambda: completed(local))[0].mode == "explainer"
        with TestClient(app) as client:
            assert post(client, second).status_code == 202
            eventually(lambda: completed(local, 2))
    assert not (tmp_path / "parent-output").exists()
    published = json.loads(platform.read_text())
    assert len(published["comments"]) == 2
    assert published["comments"][1]["body"].endswith("Recovered reference=first-evidence")
    assert not published.get("dispatches")


def test_local_shutdown_stops_execution_and_preserves_waiting_work(local, platform, monkeypatch, tmp_path):
    first = event("/landing explain Pause this task.")
    second = event("/landing explain Execute the waiting task.")
    second["comment"]["id"] = 8
    responses = [
        completion(tool="bash", arguments={"command": "echo $$ > worker.pid; sleep 60"}),
        *answers(second, "Completed queued work.", 1),
    ]
    with provider(responses) as (url, _):
        monkeypatch.setenv("LANDING_API_BASE", url)
        with TestClient(create_app()) as client:
            assert post(client, first).status_code == 202
            eventually(lambda: (tmp_path / "worker.pid").exists())
            assert post(client, second).status_code == 202
        pid = int((tmp_path / "worker.pid").read_text())
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        with TestClient(create_app()) as client:
            assert client.get("/up").status_code == 200
            records = eventually(lambda: completed(local))
        assert any(record.status in {"cancelled", "interrupted"} for record in records)
        assert len(json.loads(platform.read_text())["comments"]) == 1


def test_local_worker_does_not_execute_in_another_repository(local, platform, monkeypatch):
    payload = event("/landing fix Change the candidate.")
    payload["repository"]["full_name"] = "another/repository"
    with provider([]) as (url, calls):
        monkeypatch.setenv("LANDING_API_BASE", url)
        with TestClient(create_app()) as client:
            response = post(client, payload)
            assert response.status_code == 202

            # Failed intake remains inspectable even when authorization prevents creation of an action.
            def failed():
                with open_database(local) as engine, engine.connect() as connection:
                    return connection.exec_driver_sql(
                        "SELECT error FROM inbox WHERE id = ?", (response.json()["queued"],)
                    ).scalar()

            assert "another repository" in eventually(failed)
        assert not calls
    assert not json.loads(platform.read_text())["comments"]

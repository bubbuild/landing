import asyncio
import threading
import time

import pytest
from fastapi.testclient import TestClient

from landing.server import create_app
from tests.conftest import completion


def wait(client, location):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        action = client.get(location).json()
        if action["status"] == "completed":
            return action
        assert action["status"] in {"queued", "running"}, action
        time.sleep(0.01)
    message = "The action did not finish."
    raise AssertionError(message)


def test_webhook_admission_history_retry_and_openapi(tmp_path, model):
    responses, _ = model
    responses.extend([completion("Explained the failing check.")] * 3)
    app = create_app(tmp_path / "landing.sqlite3", workspaces={"candidate": tmp_path})
    body = {"mode": "explainer", "instruction": "Explain the failing check.", "workspace": "candidate"}
    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        created = client.post("/v1/actions", json=body, headers={"Idempotency-Key": "delivery-1"})
        assert created.status_code == 201
        location = created.headers["Location"]
        action = wait(client, location)
        assert action["result"] == "Explained the failing check."
        repeated = client.post("/v1/actions", json=body, headers={"Idempotency-Key": "delivery-1"})
        assert repeated.status_code == 200
        assert repeated.json()["id"] == action["id"]
        assert (
            client.post(
                "/v1/actions", json={**body, "instruction": "Different."}, headers={"Idempotency-Key": "delivery-1"}
            ).status_code
            == 409
        )
        retry = client.post(location + "/retries", headers={"Idempotency-Key": "retry-1"})
        assert retry.status_code == 201
        retried = wait(client, retry.headers["Location"])
        assert retried["retry_of"] == action["id"]
        assert client.post(location + "/retries", headers={"Idempotency-Key": "retry-1"}).status_code == 200
        assert client.post(location + "/cancellation").status_code == 200
        history = client.get("/v1/actions?limit=1")
        assert len(history.json()) == 1
        assert 'rel="next"' in history.headers["Link"]
        assert client.get(history.links["next"]["url"]).json()[0]["id"] == action["id"]
        events = client.get(location + "/events?limit=1")
        assert events.json()[0]["type"] == "action.queued"
        assert client.get(events.links["next"]["url"]).json()[0]["type"] == "action.running"
        schema = client.get("/openapi.json").json()
        assert "/v1/actions/{action_id}/retries" in schema["paths"]
    # A restart does not require a provider call to read completed work.
    with TestClient(create_app(tmp_path / "landing.sqlite3")) as client:
        assert client.get(location).json() == action


def test_admission_rejects_invalid_requests_without_tasks(tmp_path, model):
    app = create_app(tmp_path / "landing.sqlite3", token="test-secret", workspaces={"candidate": tmp_path})  # noqa: S106 -- test-only credential.
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/v1/actions").status_code == 401
        client.headers["Authorization"] = "Bearer test-secret"
        body = {"mode": "issuer", "instruction": "Identify the problem.", "workspace": "candidate"}
        assert client.post("/v1/actions", content="{}").status_code == 415
        assert client.post("/v1/actions", content="{", headers={"Content-Type": "application/json"}).status_code == 400
        assert client.post("/v1/actions", json={**body, "mode": "unknown"}).status_code == 422
        assert client.post("/v1/actions", json={**body, "unknown": True}).status_code == 422
        assert client.post("/v1/actions", json={**body, "workspace": str(tmp_path)}).status_code == 422
        assert client.get("/v1/actions/missing").status_code == 404
        assert client.get("/v1/actions").json() == []


def test_cancel_queued_and_running_actions_without_stopping_worker(tmp_path, model):
    responses, _ = model
    started = threading.Event()

    async def blocked(**kwargs):
        started.set()
        await asyncio.Event().wait()

    responses.extend([blocked, completion("Explained the next failure.")])
    with TestClient(create_app(tmp_path / "landing.sqlite3")) as client:
        body = {"mode": "explainer", "instruction": "Explain the failure."}
        first = client.post("/v1/actions", json=body).headers["Location"]
        assert started.wait(timeout=5)
        second = client.post("/v1/actions", json=body).headers["Location"]
        assert client.post(first + "/retries").status_code == 409
        queued = client.post(second + "/cancellation")
        assert queued.status_code == 200
        assert queued.json()["status"] == "cancelled"
        active = client.post(first + "/cancellation")
        assert active.status_code == 202
        third = client.post("/v1/actions", json=body).headers["Location"]
        assert wait(client, third)["result"] == "Explained the next failure."
        assert client.get(first).json()["status"] == "cancelled"


def test_once_health_checks_worker_and_database_without_auth(tmp_path):
    app = create_app(tmp_path / "landing.sqlite3", token="test-secret")  # noqa: S106 -- test-only credential.
    with TestClient(app) as client:
        assert client.get("/up").json() == {"status": "ok"}
        assert client.get("/v1/actions").status_code == 401
        assert client.portal is not None
        client.portal.call(app.state.worker.cancel)
        deadline = time.monotonic() + 5
        while not app.state.worker.done() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert app.state.worker.done()
        assert client.get("/up").status_code == 503
        assert client.get("/healthz").status_code == 200
        response = client.post(
            "/v1/actions",
            json={"mode": "issuer", "instruction": "Identify the failure."},
            headers={"Authorization": "Bearer test-secret"},
        )
        assert response.status_code == 503
        assert client.get("/v1/actions", headers={"Authorization": "Bearer test-secret"}).json() == []


def test_public_origin_is_used_for_pagination(tmp_path, model):
    responses, _ = model
    responses.extend([completion("Explained the failure.")] * 2)
    app = create_app(tmp_path / "landing.sqlite3", base_url="https://landing.example.test:8443/")
    with TestClient(app) as client:
        for _ in range(2):
            response = client.post("/v1/actions", json={"mode": "explainer", "instruction": "Explain the failure."})
            wait(client, response.headers["Location"])
        history = client.get("/v1/actions?limit=1")
        assert history.links["next"]["url"].startswith("https://landing.example.test:8443/v1/actions?")
        events = client.get(response.headers["Location"] + "/events?limit=1")
        assert events.links["next"]["url"].startswith("https://landing.example.test:8443/v1/actions/")


@pytest.mark.parametrize(
    "origin", ["file:///storage", "https://user:password@example.test", "https://example.test/path"]
)
def test_public_origin_rejects_non_origin_urls(tmp_path, origin):
    with pytest.raises(ValueError, match=r"HTTP\(S\) origin"):
        create_app(tmp_path / "landing.sqlite3", base_url=origin)

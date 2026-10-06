import asyncio
import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from landing.models import ActionRequest
from landing.runtime import Runtime
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


def test_webhook_admission_history_and_retry(tmp_path, model):
    responses, _ = model
    responses.extend([completion("Explained the failing check.")] * 3)
    path = tmp_path / "landing.sqlite3"
    app = create_app(Runtime(path, workspaces={"candidate": tmp_path}))
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
        following = client.get(events.links["next"]["url"]).json()
        assert following[0]["id"] > events.json()[0]["id"]
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
        assert client.post("/v1/actions", content="{}").status_code == 422
        assert client.post("/v1/actions", content="{", headers={"Content-Type": "application/json"}).status_code == 422
        assert client.post("/v1/actions", json={**body, "mode": "unknown"}).status_code == 422
        assert client.post("/v1/actions", json={**body, "unknown": True}).status_code == 422
        assert client.post("/v1/actions", json={**body, "workspace": str(tmp_path)}).status_code == 422
        assert client.get("/v1/actions/missing").status_code == 404
        assert client.get("/v1/actions").json() == []


def test_embedded_application_uses_landing_lifespan(tmp_path, model):
    responses, _ = model
    started = threading.Event()
    release = threading.Event()

    async def blocked(**kwargs):
        started.set()
        await asyncio.to_thread(release.wait, 5)
        return completion("Explained for the embedding application.")

    responses.extend([blocked, completion("Explained after restarting the application.")])
    runtime = Runtime(tmp_path / "landing.sqlite3")
    app = FastAPI(lifespan=runtime.lifespan)

    @app.post("/delegate")
    async def delegate(body: ActionRequest):
        action, _ = runtime.submit(body)
        return action

    @app.get("/actions/{action_id}")
    async def view(action_id: str):
        return runtime.tasks.get(action_id)

    with TestClient(app) as client:
        created = client.post("/delegate", json={"mode": "explainer", "instruction": "Explain the failure."})
        assert created.status_code == 200
        assert started.wait(timeout=5)
        location = "/actions/" + created.json()["id"]
        assert client.get(location).json()["status"] == "running"
        release.set()
        assert wait(client, location)["result"] == "Explained for the embedding application."
    with TestClient(app) as client:
        assert client.get(location).json()["status"] == "completed"
        created = client.post("/delegate", json={"mode": "explainer", "instruction": "Explain the next failure."})
        assert created.status_code == 200
        location = "/actions/" + created.json()["id"]
        assert wait(client, location)["result"] == "Explained after restarting the application."


def test_cancellation_and_restart_preserve_work_outcomes(tmp_path, model):
    responses, _ = model
    started = threading.Event()

    async def blocked(**kwargs):
        started.set()
        await asyncio.Event().wait()

    responses.extend([
        blocked,
        completion("Explained the next failure."),
        blocked,
        completion("Explained queued work."),
    ])
    path = tmp_path / "landing.sqlite3"
    with TestClient(create_app(path)) as client:
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
        started.clear()
        interrupted = client.post("/v1/actions", json=body).headers["Location"]
        assert started.wait(timeout=5)
        pending = client.post("/v1/actions", json=body).headers["Location"]
    with TestClient(create_app(path)) as client:
        assert client.get(interrupted).json()["status"] == "interrupted"
        assert wait(client, pending)["result"] == "Explained queued work."
        assert client.get(first).json()["status"] == "cancelled"


def test_once_health_checks_worker_and_database_without_auth(tmp_path):
    runtime = Runtime(tmp_path / "landing.sqlite3")
    app = create_app(runtime, token="test-secret")  # noqa: S106 -- test-only credential.
    with TestClient(app) as client:
        assert client.get("/up").json() == {"status": "ok"}
        assert client.get("/v1/actions").status_code == 401
        assert client.portal is not None
        client.portal.call(runtime.stop)
        deadline = time.monotonic() + 5
        while client.get("/up").status_code == 200 and time.monotonic() < deadline:
            time.sleep(0.01)
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


@pytest.mark.parametrize("token", [None, "test-secret"])
def test_api_documentation_and_authenticated_requests(tmp_path, model, token):
    responses, _ = model
    responses.append(completion("Explained through the documented API."))
    with TestClient(create_app(tmp_path / "landing.sqlite3", token=token)) as client:
        page = client.get("/docs")
        assert page.status_code == 200
        assert "/openapi.json" in page.text
        schema = client.get("/openapi.json").json()
        operation = schema["paths"]["/v1/actions"]["post"]
        if token:
            scheme_name = next(iter(operation["security"][0]))
            scheme = schema["components"]["securitySchemes"][scheme_name]
            assert scheme["type"] == "http" and scheme["scheme"] == "bearer"
            assert "security" not in schema["paths"]["/healthz"]["get"]
            assert token not in page.text and token not in str(schema)
            assert client.post("/v1/actions", json={"mode": "explainer", "instruction": "Explain."}).status_code == 401
            client.headers["Authorization"] = "Bearer wrong-token"
            assert client.get("/v1/actions").status_code == 401
            client.headers["Authorization"] = "Bearer " + token
        else:
            assert "security" not in operation
        assert client.get("/redoc").status_code == 404
        created = client.post("/v1/actions", json={"mode": "explainer", "instruction": "Explain."})
        assert created.status_code == 201
        assert wait(client, created.headers["Location"])["result"] == "Explained through the documented API."

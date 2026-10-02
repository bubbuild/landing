"""Container, paused-volume and Litestream recovery; no external model calls.

Run: uv run python tests/container_smoke.py --engine docker --image landing:local
"""

import argparse
import contextlib
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import httpx


def run(engine, *args):
    result = subprocess.run([engine, *args], check=True, capture_output=True, text=True, timeout=45)  # noqa: S603 -- explicit Docker/Podman commands in a local integration test.
    return (result.stdout + (result.stderr if args[0] == "logs" else "")).strip()


def eventually(check):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            if check():
                return
        except (httpx.TransportError, subprocess.CalledProcessError):
            pass
        time.sleep(0.1)
    message = "The container did not reach the expected state."
    raise AssertionError(message)


@contextlib.contextmanager
def volume(engine, name):
    run(engine, "volume", "create", name)
    try:
        yield name
    finally:
        run(engine, "volume", "rm", name)


@contextlib.contextmanager
def service(engine, image, name, storage, replica, *, replica_path=None):
    # Docker enables unprivileged container ports by default; rootless Podman needs this explicitly.
    network = ["--sysctl", "net.ipv4.ip_unprivileged_port_start=0"] if Path(engine).name == "podman" else []
    run(
        engine,
        "run",
        "--detach",
        "--name",
        name,
        "--publish",
        "127.0.0.1::80",
        "--volume",
        f"{storage}:/storage",
        "--volume",
        f"{replica}:/replica",
        "--env",
        f"LITESTREAM_REPLICA_URL=file:///replica/{replica_path or storage}",
        "--env",
        "LANDING_TOKEN=container-fixture",
        "--env",
        "LANDING_MODEL=openai:container-fixture",
        "--env",
        "LANDING_API_KEY=container-fixture",
        "--env",
        "LANDING_API_BASE=http://127.0.0.1:9/v1",
        "--env",
        'LANDING_CLIENT_ARGS={"max_retries":0}',
        "--env",
        "LANDING_MODEL_TIMEOUT_SECONDS=3",
        "--env",
        "BASE_URL=https://landing.example.test",
        *network,
        image,
    )
    try:
        address = run(engine, "port", name, "80/tcp")
        with httpx.Client(
            base_url="http://" + address, timeout=3, headers={"Authorization": "Bearer container-fixture"}
        ) as client:
            eventually(lambda: client.get("/up", headers={"Authorization": ""}).status_code == 200)
            assert run(engine, "exec", name, "id", "-u") != "0"
            assert client.get("/v1/actions", headers={"Authorization": ""}).status_code == 401
            yield client
    except BaseException:
        print(run(engine, "logs", name))
        raise
    finally:
        run(engine, "rm", "--force", name)


def restore(engine, image, storage, snapshot):
    name = storage + "-copy"
    uid = run(engine, "run", "--rm", "--entrypoint", "id", image, "-u")
    gid = run(engine, "run", "--rm", "--entrypoint", "id", image, "-g")
    run(
        engine,
        "run",
        "--detach",
        "--name",
        name,
        "--user",
        "0",
        "--entrypoint",
        "/bin/sh",
        "--volume",
        f"{storage}:/storage",
        image,
        "-c",
        "sleep 300",
    )
    try:
        run(engine, "cp", str(snapshot) + "/.", name + ":/storage")
        run(engine, "exec", name, "chown", "-R", uid + ":" + gid, "/storage")
    finally:
        run(engine, "rm", "--force", name)


def verify(engine, image):
    name = "landing-once-" + uuid4().hex[:12]
    with (
        volume(engine, name + "-data") as storage,
        volume(engine, name + "-restored") as restored,
        volume(engine, name + "-empty") as empty,
        volume(engine, name + "-replica") as replica,
        tempfile.TemporaryDirectory(prefix="landing-once-") as directory,
    ):
        with service(engine, image, name, storage, replica) as client:
            explained = client.post(
                "/v1/actions", json={"mode": "explainer", "instruction": "Explain the test failure."}
            )
            assert explained.status_code == 201
            eventually(lambda: client.get(explained.headers["Location"]).json()["status"] == "failed")
            request = {
                "mode": "gatekeeper",
                "instruction": "Validate the candidate.",
                "checks": ["echo $$ > check.pid; sleep 60"],
            }
            created = client.post("/v1/actions", json=request)
            assert created.status_code == 201
            location = created.headers["Location"]
            eventually(lambda: run(engine, "exec", name, "test", "-s", "/storage/workspace/check.pid") == "")
            assert client.get(location).json()["status"] == "running"
            pending_body = {"mode": "explainer", "instruction": "Explain the validation."}
            pending = client.post("/v1/actions", json=pending_body, headers={"Idempotency-Key": "delivery-1"})
            assert pending.status_code == 201 and pending.json()["status"] == "queued"
            assert client.get("/v1/actions?limit=1").links["next"]["url"].startswith("https://landing.example.test/")
            pid = run(engine, "exec", name, "cat", "/storage/workspace/check.pid")
            run(
                engine,
                "exec",
                name,
                "litestream",
                "sync",
                "-wait",
                "-socket",
                "/run/landing/litestream.sock",
                "/storage/landing.sqlite3",
            )
            snapshot = Path(directory) / "storage"
            snapshot.mkdir()
            run(engine, "pause", name)
            try:
                run(engine, "cp", name + ":/storage/.", str(snapshot))
            finally:
                run(engine, "unpause", name)
            run(engine, "stop", "--time", "20", name)
            assert run(engine, "inspect", "--format", "{{.State.ExitCode}}", name) != "137"
            action = json.loads(
                run(
                    engine,
                    "run",
                    "--rm",
                    "--volume",
                    f"{storage}:/storage",
                    image,
                    "action",
                    "view",
                    created.json()["id"],
                    "--json",
                )
            )
            assert action["status"] == "interrupted"
            history_query = (
                "import asyncio; from pathlib import Path; from landing.runtime import Runtime; "
                "runtime=Runtime(Path('/storage/landing.sqlite3')); "
                f"tape=runtime.agent.tape.session_tape({explained.json()['id']!r}, Path('/storage/workspace')); "
                "print(bool(asyncio.run(tape.store.fetch_all(tape.query().query('Explain the test failure.')))))"
            )
            assert (
                run(
                    engine,
                    "run",
                    "--rm",
                    "--volume",
                    f"{storage}:/storage",
                    "--entrypoint",
                    "python",
                    image,
                    "-c",
                    history_query,
                )
                == "True"
            )
        restore(engine, image, restored, snapshot)
        with service(engine, image, name, restored, replica) as client:
            recovered = client.get(location).json()
            assert recovered["status"] == "interrupted"
            assert run(engine, "exec", name, "cat", "/storage/workspace/check.pid") == pid
            replay = client.post("/v1/actions", json=pending_body, headers={"Idempotency-Key": "delivery-1"})
            assert replay.status_code == 200 and replay.json()["id"] == pending.json()["id"]
            eventually(lambda: client.get(pending.headers["Location"]).json()["status"] == "failed")
            assert len(client.get("/v1/actions").json()) == 3
            assert client.get("/up").status_code == 200
        # Recover onto an empty primary volume, using only the independent Litestream replica.
        with service(engine, image, name, empty, replica, replica_path=storage) as client:
            assert client.get(location).json()["status"] == "interrupted"
            assert client.get(explained.headers["Location"]).json()["status"] == "failed"
            replay = client.post("/v1/actions", json=pending_body, headers={"Idempotency-Key": "delivery-1"})
            assert replay.status_code == 200 and replay.json()["id"] == pending.json()["id"]
            eventually(lambda: client.get(pending.headers["Location"]).json()["status"] == "failed")
            assert len(client.get("/v1/actions").json()) == 3
            assert run(engine, "exec", name, "python", "-c", history_query) == "True"
            run(engine, "exec", name, "test", "!", "-e", "/storage/workspace/check.pid")
    print("Container health, shutdown, paused-volume and Litestream restore, Bub tapes, and idempotency passed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=["docker", "podman"], default="docker")
    parser.add_argument("--image", default="landing:local")
    args = parser.parse_args()
    engine = shutil.which(args.engine)
    if engine is None:
        parser.error("The selected container engine is not installed.")
    verify(engine, args.image)

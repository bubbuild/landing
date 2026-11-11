"""Run the stateless webhook image without model keys, storage, or GitHub writes.

Run: uv run python tests/container_smoke.py --engine docker --image landing:local
"""

import argparse
import hashlib
import hmac
import json
import shutil
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx


def run(engine, *args):
    result = subprocess.run([engine, *args], check=True, capture_output=True, text=True, timeout=45)  # noqa: S603 -- local Docker/Podman test commands.
    return (result.stdout + (result.stderr if args[0] == "logs" else "")).strip()


def verify(engine, image):
    name = "landing-webhook-" + uuid4().hex[:12]
    network = ["--sysctl", "net.ipv4.ip_unprivileged_port_start=0"] if Path(engine).name == "podman" else []
    run(
        engine,
        "run",
        "--detach",
        "--name",
        name,
        "--publish",
        "127.0.0.1::80",
        "--env",
        "LANDING_GITHUB_WEBHOOK_SECRET=container-fixture",
        "--env",
        "LANDING_RUNNER=gha",
        *network,
        image,
    )
    try:
        address = run(engine, "port", name, "80/tcp")
        with httpx.Client(base_url="http://" + address, timeout=3) as client:
            deadline = time.monotonic() + 30
            while True:
                try:
                    if client.get("/up").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                assert time.monotonic() < deadline, "The webhook container did not become ready."
                time.sleep(0.1)
            assert run(engine, "exec", name, "id", "-u") != "0"
            body = json.dumps({"repository": {"full_name": "example/landing"}}).encode()
            headers = {
                "Content-Type": "application/json",
                "X-GitHub-Delivery": "container-ping",
                "X-GitHub-Event": "ping",
                "X-Hub-Signature-256": "invalid",
            }
            assert client.post("/webhooks/github", content=body, headers=headers).status_code == 401
            headers["X-Hub-Signature-256"] = (
                "sha256=" + hmac.new(b"container-fixture", body, hashlib.sha256).hexdigest()
            )
            assert client.post("/webhooks/github", content=body, headers=headers).status_code == 204
        run(engine, "stop", "--time", "20", name)
        assert run(engine, "inspect", "--format", "{{.State.ExitCode}}", name) != "137"
    except BaseException:
        print(run(engine, "logs", name))
        raise
    finally:
        run(engine, "rm", "--force", name)
    print("Stateless webhook container startup, signature checks, and shutdown passed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=["docker", "podman"], default="docker")
    parser.add_argument("--image", default="landing:local")
    args = parser.parse_args()
    engine = shutil.which(args.engine)
    if engine is None:
        parser.error("The selected container engine is not installed.")
    verify(engine, args.image)

import socket
import threading
import time

import uvicorn

from landing.cli import main
from landing.server import create_app
from tests.conftest import completion


def test_remote_cli_waits_and_detaches_over_real_http(tmp_path, model, capsys, monkeypatch):
    responses, _ = model
    responses.extend([
        completion(tool="decide", arguments={"decision": "block"}),
        completion("The change needs attention."),
        completion("Explained the failing check."),
    ])
    monkeypatch.delenv("LANDING_TOKEN", raising=False)
    app = create_app(tmp_path / "landing.sqlite3", workspaces={"candidate": tmp_path})
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]})
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert server.started
            base = ["--server", f"http://127.0.0.1:{port}"]
            assert main([*base, "gatekeeper", "Review the change.", "--workspace", "candidate", "--json"]) == 1
            import json

            assert json.loads(capsys.readouterr().out)["decision"] == "block"
            assert (
                main([*base, "explainer", "Explain the failure.", "--workspace", "candidate", "--detach", "--json"])
                == 0
            )
            action = json.loads(capsys.readouterr().out)
            assert main([*base, "action", "watch", action["id"], "--exit-status", "--json"]) == 0
            assert json.loads(capsys.readouterr().out)["result"] == "Explained the failing check."
            assert main([*base, "action", "list", "--json"]) == 0
            assert len(json.loads(capsys.readouterr().out)) == 2
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            assert not thread.is_alive()

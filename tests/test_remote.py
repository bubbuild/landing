import json
import socket
import threading
import time

import uvicorn

from landing.cli import main
from landing.server import create_app
from tests.conftest import completion


def test_remote_connection_failure_has_a_diagnostic(capsys):
    with socket.socket() as unavailable:
        unavailable.bind(("127.0.0.1", 0))
        port = unavailable.getsockname()[1]
        assert main(["--server", f"http://127.0.0.1:{port}", "action", "list", "--json"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Remote request failed" in captured.err
    assert "connect" in captured.err.lower()


def test_remote_cli_waits_and_detaches_over_real_http(tmp_path, model, capsys, monkeypatch):
    responses, _ = model
    responses.extend([
        completion(tool="decide", arguments={"decision": "block"}),
        completion("The change needs attention."),
        completion("Explained the failing check."),
    ])
    app = create_app(tmp_path / "landing.sqlite3", workspaces={"candidate": tmp_path}, token="remote-fixture")  # noqa: S106 -- test-only credential.
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
            monkeypatch.setenv("LANDING_COMPLETION_ARGS", "not-json")
            settings = {"server": f"http://127.0.0.1:{port}", "token": "remote-fixture"}
            config_file = tmp_path / "landing.yml"
            monkeypatch.setenv("LANDING_CONFIG", str(config_file))
            config_file.write_text(json.dumps(settings))
            assert main(["review", "Review the change.", "--workspace", "candidate", "--json"]) == 1
            assert json.loads(capsys.readouterr().out)["decision"] == "block"
            config_file.write_text(json.dumps({"server": 42, "token": "invalid"}))
            monkeypatch.setenv("LANDING_SERVER", "http://127.0.0.1:1")
            monkeypatch.setenv("LANDING_TOKEN", settings["token"])
            assert (
                main([
                    "--server",
                    settings["server"],
                    "explain",
                    "Explain the failure.",
                    "--workspace",
                    "candidate",
                    "--detach",
                    "--json",
                ])
                == 0
            )
            action = json.loads(capsys.readouterr().out)
            monkeypatch.setenv("LANDING_SERVER", settings["server"])
            assert main(["action", "watch", action["id"], "--exit-status", "--json"]) == 0
            assert json.loads(capsys.readouterr().out)["result"] == "Explained the failing check."
            assert main(["action", "list", "--json"]) == 0
            assert len(json.loads(capsys.readouterr().out)) == 2
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            assert not thread.is_alive()

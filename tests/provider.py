"""A disposable external completion endpoint for public integration tests."""

import json
from collections import deque
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread


@contextmanager
def provider(responses, *, authorization=None):
    pending = deque(responses)
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if authorization is not None:
                authorization.append(self.headers.get("Authorization"))
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

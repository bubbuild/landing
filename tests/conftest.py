"""Exercise the real Bub SDK; replace only the external model request."""

from collections import deque

import pytest
from any_llm.types.completion import ChatCompletion
from bub.builtin.model_runner import ModelRunner


@pytest.fixture(autouse=True)
def local_cli_environment(monkeypatch):
    monkeypatch.delenv("LANDING_SERVER", raising=False)
    monkeypatch.delenv("LANDING_TOKEN", raising=False)


def completion(text=None, *, tool=None, arguments=None):
    message = {"role": "assistant", "content": text}
    if tool:
        import json

        message["tool_calls"] = [
            {"id": "call-1", "type": "function", "function": {"name": tool, "arguments": json.dumps(arguments or {})}}
        ]
    return ChatCompletion.model_validate({
        "id": "completion-1",
        "object": "chat.completion",
        "created": 0,
        "model": "test-model",
        "choices": [{"index": 0, "finish_reason": "tool_calls" if tool else "stop", "message": message}],
    })


@pytest.fixture
def model(monkeypatch):
    responses = deque()
    requests = []
    monkeypatch.setenv("LANDING_MODEL", "openai:test-model")

    async def complete(self, **kwargs):
        requests.append(kwargs)
        response = responses.popleft()
        if isinstance(response, Exception):
            raise response
        if callable(response):
            return await response(**kwargs)
        return response

    monkeypatch.setattr(ModelRunner, "completion_response", complete)
    return responses, requests

"""Exercise Bub and Republic; replace only the provider's HTTP transport."""

import json
import os
from collections import deque
from contextlib import asynccontextmanager
from typing import Any

import httpx2
import pytest
import republic
from pydantic import BaseModel

from tests.provider import event_stream


class Function(BaseModel):
    name: str
    arguments: str


class ToolCall(BaseModel):
    id: str = "call-1"
    type: str = "function"
    function: Function


class Message(BaseModel):
    role: str = "assistant"
    content: str | None = None
    tool_calls: list[ToolCall] | None = None


class Choice(BaseModel):
    index: int = 0
    finish_reason: str
    message: Message


class Completion(BaseModel):
    id: str = "completion-1"
    object: str = "chat.completion"
    created: int = 0
    model: str = "test-model"
    choices: list[Choice]


@pytest.fixture(autouse=True)
def local_cli_environment(monkeypatch, tmp_path):
    for name in tuple(os.environ):
        if name.startswith(("LANDING_", "BUB_")):
            monkeypatch.delenv(name)
    mcp_config = tmp_path / "mcp.json"
    mcp_config.write_text('{"mcpServers": {}}')
    monkeypatch.setenv("LANDING_MCP_CONFIG", str(mcp_config))


def completion(text=None, *, tool=None, arguments=None):
    message = Message(content=text)
    if tool:
        message.tool_calls = [ToolCall(function=Function(name=tool, arguments=json.dumps(arguments or {})))]
    return Completion(choices=[Choice(finish_reason="tool_calls" if tool else "stop", message=message)])


@pytest.fixture
def model(monkeypatch):
    responses = deque()
    requests = []
    monkeypatch.setenv("LANDING_MODEL", "openai:test-model")
    get_model = republic.get_model

    async def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        response = responses.popleft()
        if isinstance(response, Exception):
            raise response
        if callable(response):
            response = await response(**body)
        return httpx2.Response(200, headers={"Content-Type": "text/event-stream"}, text=event_stream(response))

    @asynccontextmanager
    async def configured_model(spec, **kwargs):
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
            options: dict[str, Any] = {**kwargs, "api_format": "chat", "api_key": "test-key", "http_client": client}
            async with get_model(spec, **options) as selected:
                yield selected

    monkeypatch.setattr(republic, "get_model", configured_model)
    return responses, requests

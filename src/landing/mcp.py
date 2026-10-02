"""Bind prepared MCP servers to the existing SDK agent for one task."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from bub import ensure_config
from bub.builtin.agent import Agent
from bub_mcp.config import MCPSettings, read_config
from bub_mcp.plugin import MCPChannel


def configuration(workspace: Path, explicit: Path | None) -> Path:
    if explicit is not None:
        path = explicit.expanduser()
        path = path if path.is_absolute() else workspace / path
        if not path.is_file():
            message = f"MCP configuration does not exist: {path}"
            raise ValueError(message)
        return path
    for path in (workspace / ".agents/mcp.json", Path.home() / ".agents/mcp.json"):
        if path.is_file():
            return path
    return ensure_config(MCPSettings).config_path.expanduser()


@asynccontextmanager
async def connected_tools(agent: Agent, workspace: Path, explicit: Path | None) -> AsyncIterator[MCPChannel]:
    channel = MCPChannel.from_server_configs(read_config(configuration(workspace, explicit)))
    try:
        await channel.connect()
        failed = [name for name, server in channel.list().items() if not server.connected]
        if failed:
            message = "Could not connect configured MCP servers: " + ", ".join(failed)
            raise RuntimeError(message)
        channel.bind_agent(agent)
        yield channel
    finally:
        await channel.stop()

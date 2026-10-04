"""Bind prepared MCP servers to the existing SDK agent for one task."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from bub import Settings as BubSettings
from bub import config, ensure_config
from bub.builtin.agent import Agent
from bub_mcp.config import MCPSettings, read_config
from bub_mcp.plugin import MCPChannel
from pydantic import AliasChoices, Field
from pydantic_settings import SettingsConfigDict

from landing.settings import FileSettings


@config()
class MCPConfiguration(FileSettings, BubSettings):
    """Own the flat file-selection setting; bub-mcp owns the mcp section."""

    model_config = SettingsConfigDict(
        env_ignore_empty=True,
        env_parse_none_str="null",
        populate_by_name=True,
        extra="ignore",
        hide_input_in_errors=True,
    )
    mcp_config: Path | None = Field(
        default=None, validation_alias=AliasChoices("LANDING_MCP_CONFIG", "BUB_MCP_CONFIG_PATH")
    )


def configuration(workspace: Path) -> Path:
    explicit = ensure_config(MCPConfiguration).mcp_config
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
async def connected_tools(agent: Agent, workspace: Path) -> AsyncIterator[MCPChannel]:
    channel = MCPChannel.from_server_configs(read_config(configuration(workspace)))
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

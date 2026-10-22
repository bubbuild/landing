"""Landing configuration and Bub's native model settings."""

from pathlib import Path

from bub import config
from bub.builtin.settings import AgentSettings
from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from landing.models import Mode


class ModeSettings(BaseModel):
    """Additional mode instructions and native SDK capability limits."""

    model_config = ConfigDict(extra="forbid")
    instructions: str = ""
    allowed_tools: list[str] | None = None
    allowed_skills: list[str] | None = None
    excluded_tools: list[str] = Field(default_factory=list)
    excluded_skills: list[str] = Field(default_factory=list)


class ConfigurationFile(BaseSettings):
    model_config = SettingsConfigDict(env_ignore_empty=True)
    config_file: Path = Field(
        default_factory=lambda: Path.home() / ".landing" / "config.yml", validation_alias="LANDING_CONFIG"
    )


@config()
class Settings(AgentSettings):
    """Configure Bub's model policy using Landing's settings."""

    model_config = SettingsConfigDict(
        env_prefix="LANDING_",
        env_ignore_empty=True,
        populate_by_name=True,
        hide_input_in_errors=True,
    )
    skill_dirs: list[Path] = Field(default_factory=list)
    modes: dict[Mode, ModeSettings] = Field(default_factory=dict)

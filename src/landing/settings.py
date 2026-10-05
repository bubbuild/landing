"""Landing settings, with native Pydantic compatibility for existing configurations."""

from pathlib import Path

from bub import config
from bub.builtin.settings import AgentSettings
from pydantic import AliasChoices, AliasGenerator, BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict, YamlConfigSettingsSource

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


class FileSettings(BaseSettings):
    """Add YAML fallback sources without sharing component fields or prefixes."""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        sources = super().settings_customise_sources(
            settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
        )
        return (
            *sources[:3],
            YamlConfigSettingsSource(settings_cls, yaml_file=ConfigurationFile().config_file.expanduser()),
            YamlConfigSettingsSource(settings_cls, yaml_file=Path.home() / ".bub" / "config.yml"),
            *sources[3:],
        )


@config()
class Settings(FileSettings, AgentSettings):
    """Use Landing names first and reuse the SDK's model validation and clients."""

    model_config = SettingsConfigDict(
        env_prefix="LANDING_",
        env_ignore_empty=True,
        populate_by_name=True,
        hide_input_in_errors=True,
        alias_generator=AliasGenerator(
            validation_alias=lambda name: AliasChoices(f"LANDING_{name.upper()}", f"BUB_{name.upper()}")
        ),
    )
    skill_dirs: list[Path] = Field(default_factory=list)
    modes: dict[Mode, ModeSettings] = Field(default_factory=dict)

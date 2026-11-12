"""Landing configuration and Bub's native model settings."""

from pathlib import Path
from typing import Any, ClassVar

from bub import config
from bub.builtin.settings import AgentSettings
from bub.configure import get_config_data
from pydantic import BaseModel, ConfigDict, Field
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

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


class YamlPolicySource(PydanticBaseSettingsSource):
    """Operator policy from Landing's loaded YAML, restricted to names a component opts in to."""

    def __init__(self, settings_cls: type[BaseSettings], fields: frozenset[str]) -> None:
        super().__init__(settings_cls)
        self.fields = fields

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        return {name: value for name, value in get_config_data().items() if name in self.fields}


class PolicySettings(BaseSettings):
    """Component settings that also accept named operator policy from Landing's YAML.

    Priority is explicit programmatic values, environment, YAML, then defaults. The allow-list is
    the boundary: a component opts in to specific policy names, so the file cannot supply task
    inputs, caller identity, event context, or credentials.
    """

    yaml_fields: ClassVar[frozenset[str]] = frozenset()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            YamlPolicySource(settings_cls, cls.yaml_fields),
            dotenv_settings,
            file_secret_settings,
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

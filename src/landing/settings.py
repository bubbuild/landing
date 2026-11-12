"""Landing configuration and Bub's native model settings."""

from pathlib import Path
from typing import Any, ClassVar

from bub import config
from bub.builtin.settings import AgentSettings
from bub.configure import get_config_data, load
from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from landing.models import Mode

DEFAULT_DATABASE = Path("~/.local/share/landing/landing.sqlite3")


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


def load_configuration(path: Path | None = None) -> None:
    """Load Landing's operator YAML through Bub's loader.

    This is plain file loading: it creates no agent, database, or workspace, so the relay can read
    operator policy without starting execution resources. Each call replaces the loaded data, so
    two configurations in one process never mix.
    """
    load((path or ConfigurationFile().config_file).expanduser())


class YamlPolicySource(PydanticBaseSettingsSource):
    """Operator policy from Landing's loaded YAML, restricted to fields the file may set."""

    def __init__(self, settings_cls: type[BaseSettings], fields: frozenset[str]) -> None:
        super().__init__(settings_cls)
        self.fields = fields

    def get_field_value(self, field, field_name) -> tuple[Any, str, bool]:
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
    plugins: list[str] = Field(default_factory=list)
    skill_dirs: list[Path] = Field(default_factory=list)
    modes: dict[Mode, ModeSettings] = Field(default_factory=dict)

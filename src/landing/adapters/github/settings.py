"""GitHub integration settings and native Actions context."""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import AliasChoices, Field, FilePath, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class GitHubEnvironment(BaseSettings):
    """Native runner context; configuration files cannot grant caller authority."""

    model_config = SettingsConfigDict(env_prefix="GITHUB_", hide_input_in_errors=True)
    actions: bool = False
    repository: str = ""
    event_name: str = ""
    event_path: FilePath | None = None
    actor: str = ""
    triggering_actor: str = ""
    run_id: str = ""
    run_attempt: str = "1"
    output: Path | None = None
    step_summary: Path | None = None
    checked_revision: str = Field(
        default="", validation_alias=AliasChoices("LANDING_GITHUB_CHECKED_REVISION", "GITHUB_SHA")
    )
    runner_temp: Path = Field(default=Path("."), validation_alias="RUNNER_TEMP")


class GitHubSettings(BaseSettings):
    """Action task inputs and integration policy, independent of event content."""

    model_config = SettingsConfigDict(
        env_prefix="LANDING_GITHUB_",
        env_ignore_empty=True,
        populate_by_name=True,
        extra="ignore",
        hide_input_in_errors=True,
    )
    repository: str = Field(validation_alias=AliasChoices("LANDING_GITHUB_REPOSITORY", "GITHUB_REPOSITORY"))
    delegated_command: Literal["review", "fix", "triage", "explain", "bump"] = Field(
        default="review", validation_alias="INPUT_COMMAND"
    )
    instruction: str = Field(
        default="Carry out the delegated task using repository guidance.", validation_alias="INPUT_INSTRUCTION"
    )
    trust: Literal["repository", "owner"] = "repository"
    upstream_workflow: Annotated[list[str], NoDecode] = Field(default_factory=list)
    bump_on_failure: bool = False
    dependency_update_actors: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["dependabot[bot]", "renovate[bot]"]
    )
    command_prefix: str = "/landing"
    number: int = Field(default=0, ge=0)
    head: str = ""
    checked_revision: str | None = None
    run_id: str = Field(default="", validation_alias=AliasChoices("LANDING_GITHUB_RUN_ID", "GITHUB_RUN_ID"))
    delivery_key: str | None = None
    check: Annotated[list[str], NoDecode] = Field(default_factory=list, validation_alias="INPUT_CHECKS")

    @classmethod
    def for_webhook(cls, repository: str) -> "GitHubSettings":
        options = cls(repository=repository)
        options.delegated_command = "review"
        options.instruction = cls.model_fields["instruction"].default
        options.number, options.head, options.run_id, options.delivery_key = 0, "", "", None
        return options

    @field_validator("check", "upstream_workflow", "dependency_update_actors", mode="before")
    @classmethod
    def multiline_inputs(cls, value):
        return [line for line in value.splitlines() if line.strip()] if isinstance(value, str) else value


class GitHubAccess(BaseSettings):
    model_config = SettingsConfigDict(hide_input_in_errors=True)
    admission_token: str | None = Field(default=None, validation_alias="GH_ADMISSION_TOKEN", repr=False)

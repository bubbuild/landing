"""Shared CLI, SDK, and HTTP contracts."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Mode = Literal["issuer", "fixer", "gatekeeper", "explainer"]
Status = Literal["queued", "running", "completed", "failed", "cancelled", "interrupted"]
Decision = Literal["allow", "block", "inconclusive"]
TERMINAL = {"completed", "failed", "cancelled", "interrupted"}
MAX_REQUEST_BYTES = 16 * 1024 * 1024


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class TextInput(Model):
    type: Literal["text"] = "text"
    text: str


class FileInput(Model):
    type: Literal["file"] = "file"
    name: str
    media_type: str = "text/plain"
    content: str


Input = Annotated[TextInput | FileInput, Field(discriminator="type")]


class ActionRequest(Model):
    mode: Mode
    instruction: str | None = None
    input: list[Input] = Field(default_factory=list)
    workspace: str | None = None
    checks: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_work(self) -> ActionRequest:
        if not (self.instruction and self.instruction.strip()) and not self.input:
            message = "Provide an instruction or input."
            raise ValueError(message)
        if any(not command.strip() for command in self.checks):
            message = "Check commands must not be empty."
            raise ValueError(message)
        if self.checks and self.mode not in {"fixer", "gatekeeper"}:
            message = "Check commands are supported by fixer and gatekeeper."
            raise ValueError(message)
        if len(self.model_dump_json().encode()) > MAX_REQUEST_BYTES:
            message = "The request exceeds 16 MiB."
            raise ValueError(message)
        return self


class Action(Model):
    id: str
    mode: Mode
    instruction: str | None
    workspace: str | None
    status: Status = Field(description="Delegated task execution status; not human acceptance.")
    result: str | None = None
    decision: Decision | None = Field(default=None, description="Review recommendation; not human approval.")
    error: dict[str, str] | None = None
    retry_of: str | None = None
    created_at: str
    updated_at: str
    started_at: str | None = None
    completed_at: str | None = None
    cancel_requested_at: str | None = None

    def exit_code(self) -> int:
        return int(self.status != "completed" or (self.mode == "gatekeeper" and self.decision != "allow"))


class Event(Model):
    id: int
    type: str
    created_at: str
    data: dict = Field(default_factory=dict)

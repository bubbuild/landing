"""Verify GitHub's payload and hand the executor one ingress value."""

import hashlib
import hmac
import json
from dataclasses import dataclass
from urllib.parse import quote

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from landing.adapters.github.api import gh, required
from landing.adapters.github.settings import GitHubSettings


class WebhookSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LANDING_GITHUB_", env_ignore_empty=True, hide_input_in_errors=True)
    webhook_secret: str = Field(min_length=1, repr=False)
    workflow: str = "landing-execute.yml"
    ref: str | None = None
    # The receiver filters with the same prefix the executor admits commands with.
    command_prefix: str = GitHubSettings.model_fields["command_prefix"].default


@dataclass(frozen=True)
class Delivery:
    """A verified forwarded webhook body as the executing boundary receives it."""

    event: dict
    event_name: str
    signature: str

    @property
    def key(self) -> str:
        # GitHub signs the body, not delivery headers; a forwarded ID cannot mint publication identity.
        return f"webhook:{self.signature}"


def forwarded_inputs(envelope: dict) -> dict:
    """Return the forwarded inputs of an executor envelope, empty for a native event."""
    return envelope.get("inputs") or {}


def read_forwarded(inputs: dict, secret: str) -> Delivery:
    """Verify forwarded inputs at the receiving trust boundary."""
    signature = required(inputs, "signature")
    event = read_webhook(required(inputs, "event").encode(), signature, secret)
    return Delivery(event=event, event_name=required(inputs, "event_name"), signature=signature)


def read_webhook(body: bytes, signature: str, secret: str) -> dict:
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not secret or not hmac.compare_digest(expected.encode(), signature.encode()):
        message = "Invalid GitHub webhook signature."
        raise PermissionError(message)
    event = json.loads(body.decode())
    if not isinstance(event, dict):
        message = "The webhook must contain a JSON object."
        raise ValueError(message)  # noqa: TRY004 -- invalid serialized input, not a Python argument type.
    return event


def dispatch(settings: WebhookSettings, inputs: dict[str, str], event: dict) -> dict:
    repository = required(event, "repository", "full_name")
    result = gh(
        [
            "api",
            f"repos/{repository}/actions/workflows/{quote(settings.workflow, safe='')}/dispatches",
            "--method",
            "POST",
            "--input",
            "-",
        ],
        repository,
        timeout=8,  # Leave response time within GitHub's ten-second webhook deadline.
        stdin=json.dumps({
            "ref": settings.ref or required(event, "repository", "default_branch"),
            "inputs": inputs,
        }),
    )
    return json.loads(result or "{}")

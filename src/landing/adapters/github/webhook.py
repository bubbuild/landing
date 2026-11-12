"""Verify GitHub's payload and hand the executor one ingress value."""

import hashlib
import hmac
import json
from urllib.parse import quote

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from landing.adapters.github.api import gh, required
from landing.adapters.github.events import ForwardedDelivery
from landing.adapters.github.settings import GitHubSettings
from landing.settings import PolicySettings


class WebhookSettings(PolicySettings):
    """Receiver placement policy; the signing key stays environment-only."""

    yaml_fields = frozenset({"workflow", "ref", "command_prefix"})
    model_config = SettingsConfigDict(env_prefix="LANDING_GITHUB_", env_ignore_empty=True, hide_input_in_errors=True)
    webhook_secret: str = Field(min_length=1, repr=False)
    workflow: str = "landing-execute.yml"
    ref: str | None = None
    # The receiver filters with the same prefix the executor admits commands with.
    command_prefix: str = GitHubSettings.model_fields["command_prefix"].default


def forwarded_inputs(envelope: dict) -> dict:
    """Return the forwarded inputs of an executor envelope, empty when the event is native.

    A native event file omits ``inputs`` or sends ``null``. A native dispatch event does carry an
    ``inputs`` object, so the envelope is identified by the forwarded ``event`` field itself: once
    that key is present, the envelope is forwarded even when its value is empty, and only a valid
    signature decides whether it is admitted.
    """
    inputs = envelope.get("inputs")
    if inputs is None:
        return {}
    if not isinstance(inputs, dict):
        message = "The forwarded inputs must be a JSON object."
        raise ValueError(message)  # noqa: TRY004 -- invalid serialized input, not a Python argument type.
    return inputs


def carries_forwarded_body(envelope: dict) -> bool:
    """Whether the envelope claims a forwarded body, by field presence rather than truthiness.

    A native ``workflow_dispatch`` event also has an ``inputs`` object, so presence of ``inputs``
    alone cannot select verification; the forwarded ``event`` field can.
    """
    return "event" in forwarded_inputs(envelope)


def read_forwarded(inputs: dict, secret: str) -> ForwardedDelivery:
    """Verify forwarded inputs at the receiving trust boundary."""
    signature = required(inputs, "signature")
    body = required(inputs, "event")
    if not isinstance(body, str):
        # The forwarded field is present, so this is a broken envelope, never a native event.
        message = "The forwarded event body must be a JSON string."
        raise ValueError(message)  # noqa: TRY004 -- invalid serialized input, not a Python argument type.
    if not body.strip():
        # Authenticate first, so an empty body never becomes a way to probe the signature check.
        verify_signature(b"", signature, secret)
        message = "The forwarded event body must not be empty."
        raise ValueError(message)
    event = read_webhook(body.encode(), signature, secret)
    event_name = required(inputs, "event_name")
    if not isinstance(event_name, str) or not event_name.strip():
        # A forwarded delivery always names its original event; an empty name must never read as native.
        message = "The forwarded event name must be a non-empty string."
        raise ValueError(message)
    return ForwardedDelivery(event=event, event_name=event_name, signature=signature)


def verify_signature(body: bytes, signature: str, secret: str) -> None:
    """Require GitHub's SHA-256 HMAC over the exact body this process received."""
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not secret or not hmac.compare_digest(expected.encode(), signature.encode()):
        message = "Invalid GitHub webhook signature."
        raise PermissionError(message)


def read_webhook(body: bytes, signature: str, secret: str) -> dict:
    verify_signature(body, signature, secret)
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

"""Verify GitHub's payload and pass it directly to the repository's executor."""

import hashlib
import hmac
import json
from urllib.parse import quote

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from landing.adapters.github.api import gh, required


class WebhookSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LANDING_GITHUB_", env_ignore_empty=True, hide_input_in_errors=True)
    webhook_secret: str = Field(min_length=1, repr=False)
    workflow: str = "landing-execute.yml"
    ref: str | None = None
    command_prefix: str = "/landing"


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


def supported_event(name: str, event: dict, workflow: str, prefix: str) -> bool:
    action = event.get("action")
    if name in {"issue_comment", "pull_request_review_comment"} and action == "created":
        words = required(event, "comment", "body").strip().split(maxsplit=1)
        return bool(words) and words[0] == prefix
    if name == "pull_request" and action in {"opened", "reopened", "synchronize", "ready_for_review"}:
        pull = required(event, "pull_request")
        head_repository = pull.get("head", {}).get("repo") or {}
        return (
            not pull.get("draft", True)
            and head_repository.get("full_name", "").casefold() == required(event, "repository", "full_name").casefold()
        )
    if name == "workflow_run" and action == "completed":
        upstream = required(event, "workflow_run")
        path = (upstream.get("path") or "").split("@", 1)[0]
        # The executor's result must not generate another delegation to itself.
        return upstream.get("conclusion") == "failure" and workflow not in {
            str(upstream.get("workflow_id")),
            path.rsplit("/", 1)[-1],
        }
    return name == "dependabot_alert" and action in {"created", "reopened", "auto_reopened"}


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

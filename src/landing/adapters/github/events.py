"""One event-selection and caller-admission decision for every GitHub entry point."""

import json
from dataclasses import dataclass

from landing.models import COMMANDS

from .api import gh, identity, required, rows
from .settings import GitHubEnvironment, GitHubSettings


@dataclass(frozen=True)
class NativeEvent:
    """An event the runner read itself; the native Actions context supplies caller authority."""

    event: dict | None
    event_name: str
    run_id: str = ""
    run_attempt: str = "1"

    @property
    def delivery_key(self) -> str:
        return f"action:{self.run_id}:{self.run_attempt}" if self.run_id else ""


@dataclass(frozen=True)
class ForwardedDelivery:
    """A signed body forwarded by the receiver and re-verified in this process."""

    event: dict
    event_name: str
    signature: str

    @property
    def delivery_key(self) -> str:
        # GitHub signs the body, not delivery headers; a forwarded ID cannot mint publication identity.
        return f"webhook:{self.signature}"


Ingress = NativeEvent | ForwardedDelivery


@dataclass(frozen=True)
class Delegation:
    """The admitted work one event hands to execution, independent of entry point."""

    command: str
    instruction: str
    event: dict | None = None
    number: int = 0
    head: str = ""
    run_id: str = ""
    delivery_key: str = ""
    comment: bool = False
    reply_required: bool = True


def command_matches(event: dict, prefix: str) -> bool:
    """Whether the event's comment begins with the delegation prefix."""
    comment = event.get("comment") or {}
    first = (comment.get("body") or "").strip().split(maxsplit=1) if comment else []
    return bool(first) and first[0] == prefix


def selected_event(name: str, event: dict, *, workflow: str, prefix: str) -> bool:
    """Whether an event kind can carry delegated work; caller authority stays separate."""
    action = event.get("action")
    if name in {"issue_comment", "pull_request_review_comment"}:
        if action != "created":
            return False
        # Keep an unusable comment payload an invalid request rather than unrelated work.
        required(event, "comment", "body")
        return command_matches(event, prefix)
    if name == "pull_request" and action in {"opened", "reopened", "synchronize", "ready_for_review"}:
        pull = required(event, "pull_request")
        head_repository = pull.get("head", {}).get("repo") or {}
        return (
            not pull.get("draft", True)
            and head_repository.get("full_name", "").casefold() == required(event, "repository", "full_name").casefold()
        )
    if name == "workflow_run" and action == "completed":
        run = required(event, "workflow_run")
        path = (run.get("path") or "").split("@", 1)[0]
        # The executor's result must not generate another delegation to itself.
        return run.get("conclusion") == "failure" and workflow not in {
            str(run.get("workflow_id")),
            path.rsplit("/", 1)[-1],
        }
    return name == "dependabot_alert" and action in {"created", "reopened", "auto_reopened"}


def permitted(repository: str, user: dict, trust: str, owner: dict) -> bool:
    if trust == "owner" and owner["type"] == "User":
        return user["id"] == owner["id"]
    # Organization owners have admin access to every repository. Reject known
    # non-owners before querying private membership, where 404 can hide access.
    permission = identity(f"repos/{repository}/collaborators/{user['login']}/permission", repository)
    if trust == "repository":
        return permission["permission"] in {"admin", "write"}
    if permission["permission"] != "admin":
        return False
    membership = identity(f"orgs/{owner['login']}/memberships/{user['login']}", repository)
    return membership["state"] == "active" and membership["role"] == "admin"


def workflow_source(repository: str, event: dict, upstream: tuple[str, ...], *, bump_on_failure: bool = False) -> bool:
    run = required(event, "workflow_run")
    source = required(run, "event")
    return (
        required(run, "name") in upstream
        and required(run, "head_repository", "full_name").casefold() == repository.casefold()
        and (
            (source in {"pull_request", "workflow_dispatch"} and bump_on_failure and run.get("conclusion") == "failure")
            or (
                source in {"push", "release", "workflow_dispatch"}
                and (
                    source == "release"
                    or required(run, "head_branch") == required(event, "repository", "default_branch")
                )
            )
        )
    )


def admitted(  # noqa: C901 -- platform authority and original caller checks share one admission boundary.
    repository: str,
    event: dict | None,
    *,
    trust: str = "repository",
    upstream: tuple[str, ...] = (),
    prefix: str = "/landing",
    bump_on_failure: bool = False,
    authenticated_event: str | None = None,
    context: GitHubEnvironment | None = None,
) -> bool:
    if context is not None and context.repository and repository.casefold() != context.repository.casefold():
        message = "The Action target must be the workflow repository."
        raise ValueError(message)
    if event and required(event, "repository", "full_name").casefold() != repository.casefold():
        message = "The event belongs to another repository."
        raise ValueError(message)
    # A local invocation without an event uses the caller's prepared credentials.
    if not event and not (context and context.actions):
        return True
    event = event or {}
    run = event.get("workflow_run")
    if run and not workflow_source(repository, event, upstream, bump_on_failure=bump_on_failure):
        return False
    comment = event.get("comment")
    body = (comment.get("body") or "").strip() if comment else ""
    if comment and (not command_matches(event, prefix) or body.startswith("<!-- landing:")):
        return False
    # A forwarded delivery is identified by presence, never by truthiness: an empty authenticated
    # event name must not fall back to the native context and inherit its authority.
    forwarded = authenticated_event is not None
    if forwarded and not authenticated_event.strip():
        message = "A forwarded delivery must name its event."
        raise ValueError(message)
    # Only native Actions context authenticates writes and dispatch; forwarded headers cannot grant that authority.
    # workflow_run is covered only after validating its originating workflow above.
    source = authenticated_event if forwarded else (context.event_name if context else "")
    native_source = (
        (not forwarded and source in {"push", "release", "workflow_dispatch", "schedule"})
        or (source == "workflow_run" and run is not None)
        or (source == "dependabot_alert" and "alert" in event)
    )
    if trust == "repository" and ((context and context.actions) or forwarded) and native_source and not comment:
        return True
    actor = required(comment, "user") if comment else required(run, "actor") if run else event.get("sender")
    if not actor:
        if context is None or not context.actor:
            message = "The event has no caller identity."
            raise ValueError(message)
        actor = identity(f"users/{context.actor}", repository)
    owner = identity(f"repos/{repository}", repository)["owner"] if trust == "owner" else {}
    if not permitted(repository, actor, trust, owner):
        return False
    rerunner = context.triggering_actor if context else ""
    if context and trust == "owner" and rerunner and rerunner != context.actor:
        user = identity(f"users/{rerunner}", repository)
        return permitted(repository, user, trust, owner)
    return True


def dependency_pull(options: GitHubSettings, event: dict) -> dict | None:
    """Resolve an opted-in update account's failed CI to its current, same-repository PR."""
    run = required(event, "workflow_run")
    actors = {name.strip().casefold() for name in options.dependency_update_actors if name.strip()}
    pulls = required(run, "pull_requests")
    head = required(run, "head_sha")
    if not pulls:
        pulls = [
            pull
            for pull in rows(f"repos/{options.repository}/commits/{head}/pulls", options.repository)
            if pull.get("state") == "open" and pull.get("head", {}).get("sha") == head
        ]
    if len(pulls) != 1:
        return None
    number = required(pulls[0], "number")
    if (options.number and options.number != number) or (options.head and options.head != head):
        return None
    pull = json.loads(gh(["api", f"repos/{options.repository}/pulls/{number}"], options.repository))
    if (
        required(pull, "state") != "open"
        or required(pull, "user", "login").casefold() not in actors
        or required(pull, "head", "sha") != head
        or required(pull, "head", "repo", "full_name").casefold() != options.repository.casefold()
        or required(pull, "base", "repo", "full_name").casefold() != options.repository.casefold()
    ):
        return None
    return pull


def dependency_alert(options: GitHubSettings, event: dict) -> tuple[str, str, dict]:
    """Resolve a dispatched alert reference against its current repository state."""
    number = required(event, "alert", "number")
    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
        message = "alert.number must be a positive integer."
        raise ValueError(message)
    alert = json.loads(gh(["api", f"repos/{options.repository}/dependabot/alerts/{number}"], options.repository))
    command = "bump" if required(alert, "state") == "open" else "triage"
    instruction = f"Follow Dependabot alert {number} using its current API state. " + options.instruction
    return command, instruction, {**event, "dependabot_alert": alert}


def route_event(options: GitHubSettings, event: dict | None) -> Delegation:
    """Turn an admitted event into the delegation execution needs, without touching inputs."""
    command, instruction = options.delegated_command, options.instruction
    number, head, run_id, key = options.number, options.head, options.run_id, options.delivery_key or ""
    if event and "comment" in event:
        lines = required(event, "comment", "body").strip().splitlines()
        parts = lines[0].split(maxsplit=2)
        if len(parts) < 2 or parts[1] not in COMMANDS:
            message = "Choose triage, fix, review, explain, or bump after the command prefix."
            raise ValueError(message)
        command = parts[1]
        instruction = "\n".join([parts[2] if len(parts) > 2 else "", *lines[1:]]).strip()
        target = event.get("issue") or required(event, "pull_request")
        # pull_target reads the current head for a PR; the comment event does not carry it.
        number = required(target, "number")
    elif event and "alert" in event:
        command, instruction, event = dependency_alert(options, event)
    elif event and "workflow_run" in event:
        run = required(event, "workflow_run")
        command, run_id = "triage", str(required(run, "id"))
        if "pull_request" in event:
            pull = required(event, "pull_request")
            command = "bump"
            instruction = "Help this existing dependency update after candidate CI failed. " + instruction
            number, head = required(pull, "number"), required(pull, "head", "sha")
            key = key or f"bump:{number}:{head}"
    elif event and not number:
        pull = event.get("pull_request")
        if pull:
            number, head = required(pull, "number"), required(pull, "head", "sha")
    # Automatic sources publish their own outcome; a comment always expects an answer.
    comment = bool(event and "comment" in event)
    automatic = bool(event and ("workflow_run" in event or "dependabot_alert" in event))
    return Delegation(
        command, instruction, event, number, head, run_id, key, comment=comment, reply_required=not automatic
    )


def prepare_event(
    options: GitHubSettings,
    payload: dict | None,
    *,
    authenticated_event: str | None = None,
    context: GitHubEnvironment | None = None,
) -> tuple[bool, Delegation | None]:
    """Apply the same caller policy and routing to native Actions and verified webhooks."""
    if not admitted(
        options.repository,
        payload,
        trust=options.trust,
        upstream=tuple(name.strip() for name in options.upstream_workflow),
        prefix=options.command_prefix,
        bump_on_failure=options.bump_on_failure,
        authenticated_event=authenticated_event,
        context=context,
    ):
        return False, None
    upstream_run = (payload or {}).get("workflow_run", {})
    if payload and (
        upstream_run.get("event") == "pull_request"
        or (
            upstream_run.get("event") == "workflow_dispatch"
            and upstream_run.get("head_branch") != required(payload, "repository", "default_branch")
        )
    ):
        pull = dependency_pull(options, payload)
        if pull is None:
            return False, None
        payload = {**payload, "pull_request": pull}
    return True, route_event(options, payload)

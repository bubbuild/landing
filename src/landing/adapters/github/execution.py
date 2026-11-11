"""Compose GitHub work with the shared application runtime."""

import asyncio
import json
import shutil
import subprocess
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from string import Template

from landing.models import COMMANDS, Action, ActionRequest, FileInput
from landing.runtime import Runtime

from .api import gh, required
from .events import Delegation, prepare_event
from .publication import (
    AUTOMATIC_GUIDANCE,
    AUTOMATIC_REVIEW_GUIDANCE,
    CONVERSATION_GUIDANCE,
    PUBLICATION_GUIDANCE,
    REVIEW_GUIDANCE,
    THREAD_GUIDANCE,
    Publication,
    begin_reaction,
    conclude_reaction,
    confirm_reply,
    marker,
    reaction_endpoint,
)
from .settings import GitHubEnvironment, GitHubSettings

REPOSITORY_GUIDANCE = (
    "GitHub repository: $repository. Use the prepared gh CLI. In GitHub conversations use #number or owner/repo#number "
    "outside code spans; elsewhere use explicit links. Read contribution templates from the checkout's standard GitHub "
    "locations when needed."
)


def repository_context(repository: str) -> FileInput:
    return FileInput(
        name="github-repository.txt",
        content=Template(REPOSITORY_GUIDANCE).substitute(repository=repository),
    )


def with_repository_context(request: ActionRequest, repository: str) -> ActionRequest:
    """Add the repository context once; retried requests already carry it."""
    context = repository_context(repository)
    if context in request.input:
        return request
    return request.model_copy(update={"input": [*request.input, context]})


def pull_target(
    repository: str, number: int, head: str, event: dict | None, *, review: bool = False, current: bool = False
) -> tuple[bool, int, str]:
    comment = (event or {}).get("comment", {})
    thread = (comment.get("in_reply_to_id") or required(comment, "id")) if "pull_request_review_id" in comment else 0
    is_pr = bool(head) or "pull_request" in (event or {}) or "pull_request" in (event or {}).get("issue", {})
    target = (event or {}).get("pull_request") or (event or {}).get("issue", {})
    if number and not is_pr:
        target = json.loads(gh(["api", f"repos/{repository}/issues/{number}"], repository))
        is_pr = "pull_request" in target
    if is_pr and (not head or (review and not thread) or current):
        target = json.loads(gh(["api", f"repos/{repository}/pulls/{number}"], repository))
        if ((review and not thread) or current) and head and target["head"]["sha"] != head:
            message = "The PR head changed; delegate work for the current candidate."
            raise ValueError(message)
        head = target["head"]["sha"]
    return is_pr, thread, head


def checkout_contains(workspace: Path, head: str, checked_revision: str) -> bool | None:
    executable = shutil.which("git")
    if not head or not checked_revision or not executable:
        return None
    ancestry = subprocess.run(  # noqa: S603 -- inspect existing local objects; never fetch or change the checkout.
        [executable, "merge-base", "--is-ancestor", "--", head, checked_revision],
        cwd=workspace,
        capture_output=True,
        timeout=10,
    )
    return ancestry.returncode == 0 if ancestry.returncode in {0, 1} else None


async def _run(
    landing: Runtime,
    options: GitHubSettings,
    delegation: Delegation,
    *,
    checked_revision: str,
    event_name: str | None = None,
) -> Action | None:
    event = delegation.event
    repository, command = options.repository, delegation.command
    number, head, key = delegation.number, delegation.head, delegation.delivery_key
    reply_required = not bool(event and ("workflow_run" in event or "dependabot_alert" in event))
    workspace = landing.framework.workspace
    mode = COMMANDS[command]
    stamp = marker(mode, key)
    is_pr, thread, head = pull_target(
        repository, number, head, event, review=mode == "gatekeeper", current=mode == "bumper"
    )
    expected_review = is_pr and mode == "gatekeeper" and not thread
    automatic_review = bool(
        expected_review
        and event
        and not event.get("comment")
        and ("pull_request" in event or event_name == "workflow_dispatch")
    )
    publication = Publication(
        landing, repository, number, stamp, expected_review, thread, head, reply_required, automatic_review
    )
    # Automatic notifications can repeat on fresh runners without the original validation record.
    if mode == "bumper" and event and "workflow_run" in event and publication.find():
        return None
    source = {
        "repository": repository,
        "number": number,
        "candidate_head": head,
        "ci_checkout": checked_revision,
        "ci_checkout_contains_candidate": checkout_contains(workspace, head, checked_revision),
        "native_run_id": delegation.run_id,
        "thread_comment_id": thread,
        "publication_marker": stamp,
        "target_url": f"https://github.com/{repository}/{'pull' if is_pr else 'issues'}/{number}" if number else "",
        "comment": {
            name: value
            for name, value in (event or {}).get("comment", {}).items()
            if name
            in {
                "id",
                "body",
                "html_url",
                "path",
                "line",
                "side",
                "start_line",
                "start_side",
                "original_line",
                "diff_hunk",
                "in_reply_to_id",
                "pull_request_review_id",
            }
        },
        "dependabot_alert": (event or {}).get("dependabot_alert", {}),
    }
    destination = CONVERSATION_GUIDANCE if number else ""
    if thread:
        destination = THREAD_GUIDANCE
    elif expected_review:
        destination = AUTOMATIC_REVIEW_GUIDANCE if automatic_review else REVIEW_GUIDANCE
    elif mode == "issuer" and not reply_required:
        destination = AUTOMATIC_GUIDANCE
    guidance = Template(f"{PUBLICATION_GUIDANCE}\n\n{destination}").substitute(
        repository=repository, number=number, thread=thread, stamp=stamp
    )
    source_input = FileInput(name="github-context.json", content=json.dumps(source))
    request = ActionRequest(
        mode=mode,
        instruction=delegation.instruction or "Carry out the delegated work.",
        workspace=str(workspace),
        input=[repository_context(repository), source_input, FileInput(name="github-guidance.txt", content=guidance)],
        checks=options.check if mode in {"fixer", "gatekeeper", "bumper"} else [],
    )

    # Reject stale or already-delivered candidates before replacing the current reaction.
    endpoint = reaction_endpoint(repository, event)
    progress = begin_reaction(repository, endpoint) if endpoint else None
    status = "failed"
    manager = landing.framework.plugin_manager
    manager.register(publication, name="github-publication")
    landing.tools.append(confirm_reply)
    try:
        async with landing.running():
            action = publication.replay(request, key)
            if action is None:
                try:
                    action = await landing.run(
                        request,
                        session_id=f"github:{repository.casefold()}:{number or key}",
                        scope=repository,
                        key=key,
                        verify=publication.complete,
                    )
                except asyncio.CancelledError:
                    if not publication.action_id:
                        raise
                    action = landing.tasks.get(publication.action_id)
        status = action.status
    except asyncio.CancelledError:
        status = "cancelled"
        raise
    finally:
        landing.tools.remove(confirm_reply)
        manager.unregister(publication)
        if endpoint:
            conclude_reaction(repository, endpoint, progress, status)
    return action


async def execute(
    options: GitHubSettings,
    payload: dict | None,
    *,
    database: Path,
    workspace: Path | None = None,
    config_file: Path | None = None,
    skill_dirs: Iterable[Path] = (),
    plugins: Iterable[object] = (),
    context: GitHubEnvironment | None = None,
    source: str | None = None,
    fallback_key: str | None = None,
) -> Action | None:
    accepted, delegation = prepare_event(options, payload, authenticated_event=source, context=context)
    if not accepted or delegation is None:
        return None
    if not delegation.delivery_key:
        if not fallback_key:
            message = "Set LANDING_GITHUB_DELIVERY_KEY outside a GitHub workflow."
            raise ValueError(message)
        delegation = replace(delegation, delivery_key=fallback_key)
    checked_revision = options.checked_revision
    if checked_revision is None:
        checked_revision = context.checked_revision if context else ""
    runtime = Runtime(database, workspace=workspace, config_file=config_file, skill_dirs=skill_dirs, plugins=plugins)
    return await _run(
        runtime,
        options,
        delegation,
        checked_revision=checked_revision,
        event_name=source or (context.event_name if context else ""),
    )

"""Minimal GitHub admission and publication receipts around native agent tools."""

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import cast

from landing.commands import COMMANDS
from landing.models import MODES, Action, ActionRequest, FileInput, Mode
from landing.runtime import Runtime


def gh(args: list[str], repository: str) -> str:
    executable = shutil.which("gh")
    if executable is None:
        message = "Prepare gh in PATH before using the GitHub integration."
        raise FileNotFoundError(message)
    result = subprocess.run(  # noqa: S603 -- explicit argv, normal prepared gh authentication.
        [executable, *args], env={**os.environ, "GH_REPO": repository}, capture_output=True, text=True, timeout=120
    )
    if result.returncode:
        message = result.stderr.strip() or "GitHub CLI failed."
        raise RuntimeError(message)
    return result.stdout


def rows(endpoint: str, repository: str) -> list[dict]:
    output = gh(["api", endpoint, "--paginate", "--jq", ".[] | @json"], repository)
    return [json.loads(line) for line in output.splitlines() if line.strip()]


def marker(mode: Mode, key: str) -> str:
    digest = hashlib.sha256(key.encode()).hexdigest()[:24]
    return f"<!-- landing:{mode}:{digest} -->"


def delegation(event: dict, repository: str, prefix: str = "/landing") -> tuple[str, str, int] | None:
    if event["repository"]["full_name"].lower() != repository.lower():
        message = "The event belongs to another repository."
        raise ValueError(message)
    comment = event["comment"]
    if comment["user"]["type"] == "Bot" or (comment.get("body") or "").lstrip().startswith("<!-- landing:"):
        return None
    first = (comment.get("body") or "").strip().splitlines()
    parts = first[0].split(maxsplit=2) if first else []
    command = ""
    instruction = ""
    if parts and parts[0] == prefix:
        if len(parts) < 2 or parts[1] not in COMMANDS:
            message = "Choose triage, fix, review, or explain after the command prefix."
            raise ValueError(message)
        command = parts[1]
        instruction = "\n".join([parts[2] if len(parts) > 2 else "", *first[1:]]).strip()
    elif "pull_request_review_id" in comment:
        review = json.loads(
            gh(
                [
                    "api",
                    f"repos/{repository}/pulls/{event['pull_request']['number']}/reviews/{comment['pull_request_review_id']}",
                ],
                repository,
            )
        )
        if not (review.get("body") or "").startswith("<!-- landing:"):
            return None
        if not comment.get("in_reply_to_id") and comment["user"]["login"] == review.get("user", {}).get("login"):
            return None
        selected = review["body"].split(":", 2)[1]
        command = next((name for name, value in COMMANDS.items() if value == selected), "")
        instruction = comment.get("body") or ""
    if not command:
        return None
    permission = json.loads(
        gh(["api", f"repos/{repository}/collaborators/{comment['user']['login']}/permission"], repository)
    )
    if permission["permission"] not in {"admin", "maintain", "write"}:
        message = "A repository maintainer must delegate this action."
        raise ValueError(message)
    target = event.get("issue") or event["pull_request"]
    return command, instruction, target["number"]


def publication(repository: str, number: int, stamp: str, *, review: bool, thread: int = 0) -> dict | None:
    if not number:
        return None
    resource = "comments" if thread else "reviews" if review else "comments"
    kind = "pulls" if thread or review else "issues"
    records = rows(f"repos/{repository}/{kind}/{number}/{resource}", repository)
    return next(
        (
            item
            for item in records
            if stamp in (item.get("body") or "")
            and item.get("state") != "PENDING"
            and (not thread or item.get("in_reply_to_id") == thread)
        ),
        None,
    )


def repository_context(repository: str) -> FileInput:
    return FileInput(
        name="github-repository.txt",
        content=f"GitHub repository: {repository}. Use the prepared gh CLI and this repository's instructions. Do not change authentication.",
    )


def pull_target(repository: str, number: int, head: str, event: dict | None) -> tuple[bool, int, str]:
    comment = (event or {}).get("comment", {})
    thread = (comment.get("in_reply_to_id") or comment["id"]) if "pull_request_review_id" in comment else 0
    is_pr = bool(head) or "pull_request" in (event or {}) or "pull_request" in (event or {}).get("issue", {})
    if number and not is_pr:
        target = json.loads(gh(["api", f"repos/{repository}/issues/{number}"], repository))
        is_pr = "pull_request" in target
    if is_pr and not head:
        target = json.loads(gh(["api", f"repos/{repository}/pulls/{number}"], repository))
        head = target["head"]["sha"]
    return is_pr, thread, head


async def run(
    repository: str,
    mode: Mode,
    instruction: str,
    db: Path,
    workspace: Path,
    *,
    number: int = 0,
    head: str = "",
    run_id: str = "",
    checked_revision: str = "",
    key: str,
    checks: list[str],
    event: dict | None = None,
    skill_dirs: Iterable[Path] = (),
) -> Action:
    stamp = marker(mode, key)
    is_pr, thread, head = pull_target(repository, number, head, event)
    expected_review = is_pr and mode == "gatekeeper" and not thread
    source = {
        "repository": repository,
        "number": number,
        "candidate_head": head,
        "ci_checkout": checked_revision,
        "native_run_id": run_id,
        "thread_comment_id": thread,
        "publication_marker": stamp,
    }
    guidance = (
        f"Use the prepared gh CLI for {repository}. Investigation and publication are part of this task. "
        f"Include {stamp} at the start of the published body to identify this delivery. "
        "Read the applicable templates and repository instructions. Do not merge or change credentials. "
        "Claim publication only after the API confirms it. Refresh the current PR head before publishing. "
        "Keep the candidate head and the actual CI checkout revision distinct."
    )
    if thread:
        guidance += f" Reply to the question in the original inline thread using the review-comment replies API with comment ID {thread}; do not open a new review or conversation comment. Changing work mode requires explicit delegation."
    elif expected_review:
        guidance += f" Publish a native GitHub Review on PR #{number}, not an issue conversation comment. Use the reviews API with commit_id, event COMMENT, body, and native comments containing path, line, and side for actionable findings. Use start_line and start_side for ranges. Verify every location against the inspected diff. Put the marker in the review body. Do not invent findings merely to add inline comments. APPROVE and REQUEST_CHANGES require explicit repository authorization. Record the gate decision separately."
    elif number:
        guidance += f" Reply to issue or PR #{number} with the result and publication links. For a fix, verify the candidate before committing, pushing, and opening or updating its PR using gh; follow repository CI instructions."
    source_input = FileInput(name="github-context.json", content=json.dumps(source))
    request = ActionRequest(
        mode=mode,
        instruction=instruction or "Carry out the delegated work.",
        workspace=str(workspace),
        input=[repository_context(repository), source_input, FileInput(name="github-guidance.txt", content=guidance)],
        checks=checks if mode in {"fixer", "gatekeeper"} else [],
    )

    def verify(action: Action) -> None:
        if not number:
            return
        receipt = publication(repository, number, stamp, review=expected_review, thread=thread)
        if receipt is None or (expected_review and head and receipt.get("commit_id") != head):
            message = "The agent completed without a confirmed publication at the requested destination."
            raise RuntimeError(message)
        if expected_review and head:
            current = json.loads(gh(["api", f"repos/{repository}/pulls/{number}"], repository))
            if current["head"]["sha"] != head:
                message = "The PR head changed; delegate a new review for the current candidate."
                raise RuntimeError(message)
        landing.tasks.event(action.id, "github.published", {"id": receipt["id"], "url": receipt["html_url"]})

    async with Runtime(db, skill_dirs=skill_dirs, verify=verify).running() as landing:
        existing = publication(repository, number, stamp, review=expected_review, thread=thread)
        if existing:
            action, _ = landing.tasks.create(request, scope=repository, key=key)
            verify(action)
            action = landing.tasks.finish(action.id, "completed", result=f"Already published: {existing['html_url']}")
            return action
        command = next(name for name, value in COMMANDS.items() if value == mode)
        action = await landing.command(
            command, request, session_id=f"github:{number or key}", scope=repository, key=key
        )
        db.parent.joinpath("summary.md").write_text((action.result or json.dumps(action.error)) + "\n")
        return action


def main(argv: list[str] | None = None) -> Action | None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=[*COMMANDS, *MODES], nargs="?", default="review")
    parser.add_argument(
        "--repository", default=os.getenv("GITHUB_REPOSITORY"), required=not bool(os.getenv("GITHUB_REPOSITORY"))
    )
    parser.add_argument("--number", type=int, default=0)
    parser.add_argument("--head", default="")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--checked-revision", default="")
    parser.add_argument(
        "--instruction", default="Carry out the delegated task using repository guidance and independent checks."
    )
    parser.add_argument("--event", type=Path)
    parser.add_argument("--command-prefix", default="/landing")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--check", action="append", default=[])
    parser.add_argument("--skill-dir", type=Path, action="append", default=[])
    parser.add_argument("--delivery-key", required=True)
    args = parser.parse_args(argv)
    event = json.loads(args.event.read_text()) if args.event else None
    if event and "comment" in event:
        delegated = delegation(event, args.repository, args.command_prefix)
        if delegated is None:
            return
        args.command, args.instruction, args.number = delegated
        if "pull_request" in event or "pull_request" in event.get("issue", {}):
            pull = json.loads(gh(["api", f"repos/{args.repository}/pulls/{args.number}"], args.repository))
            args.head = pull["head"]["sha"]
    elif event and "workflow_run" in event:
        run_event = event["workflow_run"]
        default = event["repository"]["default_branch"]
        if run_event["head_branch"] != default or run_event["event"] == "pull_request":
            return
        args.command, args.run_id = "triage", str(run_event["id"])
    elif event and not args.number:
        pull = event.get("pull_request")
        if pull:
            args.number, args.head = pull["number"], pull["head"]["sha"]
    selected = cast("Mode", COMMANDS.get(args.command, args.command))
    action = asyncio.run(
        run(
            args.repository,
            selected,
            args.instruction,
            args.db,
            Path.cwd(),
            number=args.number,
            head=args.head,
            run_id=args.run_id,
            checked_revision=args.checked_revision,
            key=args.delivery_key,
            checks=args.check,
            event=event,
            skill_dirs=args.skill_dir,
        )
    )
    print(action.model_dump_json(indent=2))
    return action


if __name__ == "__main__":
    result = main()
    raise SystemExit(int(result is not None and result.status != "completed"))

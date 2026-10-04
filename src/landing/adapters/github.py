"""Minimal GitHub admission and publication receipts around native agent tools."""

import asyncio
import hashlib
import json
import os
import shutil
import signal
import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

import typer
from bub import hookimpl
from bub.hooks.interception import ToolCallDecision
from bub.tools import Tool, ToolContext

from landing.commands import COMMANDS
from landing.models import Action, ActionRequest, FileInput, Mode
from landing.runtime import Runtime
from landing.tasks import Tasks


def gh(args: list[str], repository: str, *, token: str | None = None) -> str:
    executable = shutil.which("gh")
    if executable is None:
        message = "Prepare gh in PATH before using the GitHub integration."
        raise FileNotFoundError(message)
    environment = {**os.environ, "GH_REPO": repository}
    if token:
        environment["GH_TOKEN"] = token
    result = subprocess.run(  # noqa: S603 -- explicit argv with caller-prepared authentication.
        [executable, *args], env=environment, capture_output=True, text=True, timeout=120
    )
    if result.returncode:
        message = result.stderr.strip() or "GitHub CLI failed."
        raise RuntimeError(message)
    return result.stdout


def identity(endpoint: str, repository: str) -> dict:
    return json.loads(gh(["api", endpoint], repository, token=os.getenv("GH_ADMISSION_TOKEN")))


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


def workflow_source(repository: str, event: dict, upstream: tuple[str, ...]) -> bool:
    run = event["workflow_run"]
    return (
        run["name"] in upstream
        and run["head_repository"]["full_name"].casefold() == repository.casefold()
        and run["event"] in {"push", "release", "workflow_dispatch"}
        and (run["event"] == "release" or run["head_branch"] == event["repository"]["default_branch"])
    )


def admitted(
    repository: str,
    event: dict | None,
    *,
    trust: str = "repository",
    upstream: tuple[str, ...] = (),
    prefix: str = "/landing",
) -> bool:
    native = os.getenv("GITHUB_REPOSITORY")
    if native and repository.casefold() != native.casefold():
        message = "The Action target must be the workflow repository."
        raise ValueError(message)
    if event and event["repository"]["full_name"].casefold() != repository.casefold():
        message = "The event belongs to another repository."
        raise ValueError(message)
    # A local invocation without an event uses the caller's prepared credentials.
    if not event and not os.getenv("GITHUB_ACTIONS"):
        return True
    event = event or {}
    run = event.get("workflow_run")
    if run and not workflow_source(repository, event, upstream):
        return False
    comment = event.get("comment")
    body = (comment.get("body") or "").strip() if comment else ""
    first = body.split(maxsplit=1)
    if comment and (body.startswith("<!-- landing:") or not first or first[0] != prefix):
        return False
    # Native writes and dispatch authenticate the source, including App tokens.
    # workflow_run is covered only after validating its originating workflow above.
    native_source = os.getenv("GITHUB_EVENT_NAME") in {"push", "release", "workflow_dispatch"} or (
        os.getenv("GITHUB_EVENT_NAME") == "workflow_run" and run is not None
    )
    if trust == "repository" and os.getenv("GITHUB_ACTIONS") and native_source and not comment:
        return True
    actor = comment["user"] if comment else run["actor"] if run else event.get("sender")
    actor = actor or identity(f"users/{os.environ['GITHUB_ACTOR']}", repository)
    owner = identity(f"repos/{repository}", repository)["owner"] if trust == "owner" else {}
    if not permitted(repository, actor, trust, owner):
        return False
    rerunner = os.getenv("GITHUB_TRIGGERING_ACTOR")
    if trust == "owner" and rerunner and rerunner != os.getenv("GITHUB_ACTOR"):
        user = identity(f"users/{rerunner}", repository)
        return permitted(repository, user, trust, owner)
    return True


def rows(endpoint: str, repository: str) -> list[dict]:
    output = gh(["api", endpoint, "--paginate", "--jq", ".[] | @json"], repository)
    return [json.loads(line) for line in output.splitlines() if line.strip()]


def marker(mode: Mode, key: str) -> str:
    digest = hashlib.sha256(key.encode()).hexdigest()[:24]
    return f"<!-- landing:{mode}:{digest} -->"


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


def verify_publication(
    action: Action,
    tasks: Tasks,
    repository: str,
    number: int,
    stamp: str,
    *,
    review: bool,
    thread: int,
    head: str,
    reply_required: bool,
) -> None:
    if not number:
        return
    receipt = publication(repository, number, stamp, review=review, thread=thread)
    if receipt is None and thread:
        saved = tasks.connection.execute(
            "SELECT data FROM action_events WHERE action_id = ? AND type = 'github.reply_confirmed' ORDER BY id DESC LIMIT 1",
            (action.id,),
        ).fetchone()
        if saved:
            candidate = json.loads(
                gh(["api", f"repos/{repository}/pulls/comments/{json.loads(saved['data'])['id']}"], repository)
            )
            if (
                candidate.get("in_reply_to_id") == thread
                and candidate.get("pull_request_url") == f"https://api.github.com/repos/{repository}/pulls/{number}"
            ):
                receipt = candidate
    if receipt is None and action.mode == "issuer" and not reply_required:
        unchanged = tasks.connection.execute(
            "SELECT 1 FROM action_events WHERE action_id = ? AND type = 'issue.unchanged'", (action.id,)
        ).fetchone()
        if unchanged:
            return
    if receipt is None or (review and head and receipt.get("commit_id") != head):
        message = "The agent completed without a confirmed publication at the requested destination."
        raise RuntimeError(message)
    if review and head:
        current = json.loads(gh(["api", f"repos/{repository}/pulls/{number}"], repository))
        if current["head"]["sha"] != head:
            message = "The PR head changed; delegate a new review for the current candidate."
            raise RuntimeError(message)
    tasks.event(action.id, "github.published", {"id": receipt["id"], "url": receipt["html_url"]})


class CandidateGuard:
    """Stop a bound PR review before starting tools against a replaced head."""

    def __init__(self, runtime: Runtime, repository: str, number: int, head: str) -> None:
        self.runtime, self.repository, self.number, self.head = runtime, repository, number, head
        self.lock = asyncio.Lock()
        self.batch = None
        self.reason = ""
        self.action_id: str | None = None

    @hookimpl
    async def before_tool_call(self, call, state):
        if "landing_action_id" not in state:
            return None
        async with self.lock:
            if call.run_id != self.batch:
                self.batch = call.run_id
                try:
                    pull = json.loads(
                        await asyncio.to_thread(
                            gh, ["api", f"repos/{self.repository}/pulls/{self.number}"], self.repository
                        )
                    )
                    self.reason = (
                        "The PR head changed; this review is superseded." if pull["head"]["sha"] != self.head else ""
                    )
                except Exception as exc:
                    self.reason = f"Cannot verify the current PR head: {exc}"
                if self.reason:
                    action_id = state["landing_action_id"]
                    self.action_id = action_id
                    self.runtime.tasks.event(action_id, "github.review_stopped", {"reason": self.reason})
                    self.runtime.tasks.output(action_id, self.reason)
                    self.runtime.cancel(action_id)
            if self.reason:
                return ToolCallDecision.deny(self.reason)


async def interruptible(awaitable):
    """Use normal stream cancellation and shell cleanup when the runner stops us."""
    loop = asyncio.get_running_loop()
    task = asyncio.create_task(awaitable)
    loop.add_signal_handler(signal.SIGTERM, task.cancel)
    try:
        return await task
    finally:
        loop.remove_signal_handler(signal.SIGTERM)


def repository_context(repository: str) -> FileInput:
    return FileInput(
        name="github-repository.txt",
        content=f"GitHub repository: {repository}. Use the prepared gh CLI and this repository's instructions. Do not change authentication. In GitHub conversations use #number or owner/repo#number and commit links that render as short hashes; keep autolink references outside code spans. Elsewhere use explicit links. Link directly to relevant reviews, comments, jobs and file lines with short descriptive labels. Read only the applicable contribution template from the checkout's standard GitHub locations when needed.",
    )


def pull_target(
    repository: str, number: int, head: str, event: dict | None, *, review: bool = False
) -> tuple[bool, int, str]:
    comment = (event or {}).get("comment", {})
    thread = (comment.get("in_reply_to_id") or comment["id"]) if "pull_request_review_id" in comment else 0
    is_pr = bool(head) or "pull_request" in (event or {}) or "pull_request" in (event or {}).get("issue", {})
    target = (event or {}).get("pull_request") or (event or {}).get("issue", {})
    if number and not is_pr:
        target = json.loads(gh(["api", f"repos/{repository}/issues/{number}"], repository))
        is_pr = "pull_request" in target
    if is_pr and (not head or (review and not thread)):
        target = json.loads(gh(["api", f"repos/{repository}/pulls/{number}"], repository))
        if review and not thread and head and target["head"]["sha"] != head:
            message = "The PR head changed; delegate a new review for the current candidate."
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


def reply_tool(repository: str, number: int, thread: int, record: Callable[[str, int], None]) -> Tool:
    previous_replies = (
        {item["id"] for item in rows(f"repos/{repository}/pulls/{number}/comments", repository)} if thread else set()
    )

    def confirm_reply(comment_id: int, *, context: ToolContext) -> str:
        """Read back a published reply at the delegated conversation or inline review thread."""
        kind = "pulls" if thread else "issues"
        receipt = json.loads(gh(["api", f"repos/{repository}/{kind}/comments/{comment_id}"], repository))
        matches = (
            comment_id not in previous_replies
            and receipt.get("in_reply_to_id") == thread
            and receipt.get("pull_request_url") == f"https://api.github.com/repos/{repository}/pulls/{number}"
            if thread
            else receipt.get("issue_url") == f"https://api.github.com/repos/{repository}/issues/{number}"
        )
        if not matches:
            message = "The comment is not a reply at the delegated destination."
            raise ValueError(message)
        record(context.state["landing_action_id"], comment_id)
        return json.dumps({"url": receipt["html_url"], "body": receipt["body"]})

    return Tool.from_callable(confirm_reply, context=True)


async def run(
    repository: str,
    command: str,
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
    reply_required: bool = True,
    skill_dirs: Iterable[Path] = (),
) -> Action:
    mode = COMMANDS[command]
    stamp = marker(mode, key)
    is_pr, thread, head = pull_target(repository, number, head, event, review=mode == "gatekeeper")
    expected_review = is_pr and mode == "gatekeeper" and not thread
    source = {
        "repository": repository,
        "number": number,
        "candidate_head": head,
        "ci_checkout": checked_revision,
        "ci_checkout_contains_candidate": checkout_contains(workspace, head, checked_revision),
        "native_run_id": run_id,
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
    }
    guidance = (
        f"Use the prepared gh CLI for {repository}. "
        f"When publishing, include {stamp} at the start of the body to identify this delivery. "
        "Read the applicable templates and repository instructions. Do not merge or change credentials. "
        "For a body file, use gh pr/issue comment --body-file FILE or gh api -F body=@FILE; -f body=@FILE sends the literal path. Use --input FILE for a JSON payload. "
        "Read back the published body and check its content and destination before claiming success; an ID or URL alone is insufficient. Refresh the current PR head before publishing. "
        "Supplemental evidence may be linked from the required reply or review; a separate evidence comment cannot replace that publication. "
        "Keep the candidate head and the actual CI checkout revision distinct. "
        "Use the supplied target reference, comment and checkout relationship. Fetch the specific target details, discussion or diff needed for this task; do not load all issues or review history."
    )
    if thread:
        guidance += f" Read the original comment at repos/{repository}/pulls/comments/{thread}. Reply in this thread with POST repos/{repository}/pulls/{number}/comments/{thread}/replies; do not open a new review. Include the delivery marker, or call confirm_reply with the returned comment ID after a successful reply. For a fix, finish after needed local validation, push, native CI dispatch and this reply; report pending CI with its link. Wait for native results only when explicitly requested, and never wait for this duty or Landing feedback job."
    elif expected_review:
        guidance += f" This delegation authorizes publishing a native GitHub COMMENT review on PR #{number}. The verdict and findings belong in this review. Publish it yourself using the supplied native check conclusions. Evaluate native check jobs separately from Landing feedback; the enclosing workflow cannot finish while its feedback job is running. Do not wait for that workflow to complete or for another Landing review. Use event COMMENT for allow, block and inconclusive verdicts. APPROVE and REQUEST_CHANGES require separate explicit repository authorization. Use the reviews API with commit_id, body, and native comments containing path, line, and side for actionable findings. Use start_line and start_side for ranges. Verify every location against the inspected diff. Put the marker in the review body, followed by a brief verdict such as 'One blocking finding; see inline.' or 'No blocking findings.' Keep finding explanations in inline comments; omit revision, diff and successful-check recaps. Do not invent findings merely to add inline comments. Record the gate decision separately."
    elif mode == "issuer" and not reply_required:
        guidance += " This is automatic follow-up. Update a matching issue only for useful new evidence or changed conditions. Otherwise call no_update and complete without a public write."
    elif number:
        guidance += f" Reply to issue or PR #{number} with the result and publication links. Call confirm_reply with the returned conversation comment ID to read back the published body. For a fix, verify the candidate before committing, pushing, and opening or updating its PR using gh; follow repository CI instructions."
    source_input = FileInput(name="github-context.json", content=json.dumps(source))
    request = ActionRequest(
        mode=mode,
        instruction=instruction or "Carry out the delegated work.",
        workspace=str(workspace),
        input=[repository_context(repository), source_input, FileInput(name="github-guidance.txt", content=guidance)],
        checks=checks if mode in {"fixer", "gatekeeper"} else [],
    )

    def verify(action: Action) -> None:
        verify_publication(
            action,
            landing.tasks,
            repository,
            number,
            stamp,
            review=expected_review,
            thread=thread,
            head=head,
            reply_required=reply_required,
        )

    landing = Runtime(
        db,
        verify=verify,
        skill_dirs=skill_dirs,
        tools=[
            reply_tool(
                repository,
                number,
                thread,
                lambda action_id, comment_id: landing.tasks.event(
                    action_id, "github.reply_confirmed", {"id": comment_id}
                ),
            )
        ],
    )
    guard = CandidateGuard(landing, repository, number, head) if expected_review else None
    if guard:
        landing.framework.plugin_manager.register(guard, name="github-candidate")
    async with landing.running():
        existing = publication(repository, number, stamp, review=expected_review, thread=thread)
        if existing:
            row = landing.tasks.connection.execute(
                "SELECT id FROM actions WHERE idempotency_scope = ? AND idempotency_key = ?", (repository, key)
            ).fetchone()
            # The receipt identifies this delivery; retain its original evidence snapshot on replay.
            snapshot = landing.tasks.request(row["id"]).input if row else request.input
            action, _ = landing.tasks.create(request.model_copy(update={"input": snapshot}), scope=repository, key=key)
            verify(action)
            action = landing.tasks.finish(action.id, "completed", result=f"Already published: {existing['html_url']}")
            return action
        try:
            action = await landing.command(
                command, request, session_id=f"github:{number or key}", scope=repository, key=key
            )
        except asyncio.CancelledError:
            if not guard or not guard.action_id:
                raise
            action = landing.tasks.get(guard.action_id)
        return action


def write_outputs(action: Action | None) -> None:
    """Write native Action outputs and the step summary when available."""
    outputs = {
        "id": action.id if action else "",
        "status": action.status if action else "skipped",
        "decision": action.decision or "" if action else "",
        "result": action.result or "" if action else "",
    }
    if destination := os.getenv("GITHUB_OUTPUT"):
        with Path(destination).open("a") as output:
            for name, value in outputs.items():
                delimiter = uuid4().hex
                output.write(f"{name}<<{delimiter}\n{value}\n{delimiter}\n")
    if summary := os.getenv("GITHUB_STEP_SUMMARY"):
        with Path(summary).open("a") as output:
            output.write(outputs["result"] + "\n")


app = typer.Typer(name="github", help="Handle native GitHub events with prepared credentials.", no_args_is_help=True)


@app.command("event")
def github_event(
    ctx: typer.Context,
    repository: Annotated[
        str, typer.Option(envvar=["INPUT_REPOSITORY", "GITHUB_REPOSITORY"], help="Workflow repository.")
    ],
    event: Annotated[
        Path | None,
        typer.Option(envvar="GITHUB_EVENT_PATH", exists=True, dir_okay=False, help="Native event JSON file."),
    ] = None,
    delegated_command: Annotated[
        Literal["review", "fix", "triage", "explain"],
        typer.Option("--command", envvar="INPUT_COMMAND", help="Default delegated action."),
    ] = "review",
    instruction: Annotated[
        str, typer.Option(envvar="INPUT_INSTRUCTION", help="Work and acceptance criteria.")
    ] = "Carry out the delegated task using repository guidance.",
    trust: Annotated[
        Literal["repository", "owner"], typer.Option(envvar="INPUT_TRUST", help="Native caller scope.")
    ] = "repository",
    upstream_workflow: Annotated[list[str] | None, typer.Option(help="Allowed upstream workflow (repeatable).")] = None,
    command_prefix: Annotated[
        str, typer.Option(envvar="INPUT_COMMAND_PREFIX", help="Comment command prefix.")
    ] = "/landing",
    number: Annotated[int, typer.Option(envvar="INPUT_NUMBER", min=0, help="Issue or PR number.")] = 0,
    head: Annotated[str, typer.Option(envvar="INPUT_HEAD", help="Candidate commit.")] = "",
    checked_revision: Annotated[
        str | None, typer.Option(envvar="INPUT_CHECKED_REVISION", help="Revision covered by native checks.")
    ] = None,
    run_id: Annotated[str, typer.Option(envvar=["INPUT_RUN_ID", "GITHUB_RUN_ID"], help="Native run to inspect.")] = "",
    delivery_key: Annotated[
        str | None, typer.Option(envvar="INPUT_DELIVERY_KEY", help="Stable delivery identity.")
    ] = None,
    check: Annotated[list[str] | None, typer.Option(help="Required validation (repeatable).")] = None,
) -> None:
    """Admit and route an event, then confirm native publication."""
    ctx.obj["execute"](**locals(), handler=delivery)


def route_event(args, event: dict | None) -> dict | None:
    if event and "comment" in event:
        lines = event["comment"]["body"].strip().splitlines()
        parts = lines[0].split(maxsplit=2)
        if len(parts) < 2 or parts[1] not in COMMANDS:
            message = "Choose triage, fix, review, or explain after the command prefix."
            raise ValueError(message)
        args.delegated_command = parts[1]
        args.instruction = "\n".join([parts[2] if len(parts) > 2 else "", *lines[1:]]).strip()
        target = event.get("issue") or event["pull_request"]
        args.number = target["number"]
        if "pull_request" in event or "pull_request" in event.get("issue", {}):
            pull = json.loads(gh(["api", f"repos/{args.repository}/pulls/{args.number}"], args.repository))
            args.head = pull["head"]["sha"]
            event = {**event, "pull_request": pull}
    elif event and "workflow_run" in event:
        args.delegated_command, args.run_id = "triage", str(event["workflow_run"]["id"])
    elif event and not args.number:
        pull = event.get("pull_request")
        if pull:
            args.number, args.head = pull["number"], pull["head"]["sha"]

    return event


async def delivery(args) -> int:
    if args.server:
        message = "--server cannot be combined with github."
        raise ValueError(message)

    event = json.loads(args.event.read_text()) if args.event else None
    upstream = (
        args.upstream_workflow
        if args.upstream_workflow is not None
        else os.getenv("INPUT_UPSTREAM_WORKFLOW", "").splitlines()
    )
    if not admitted(
        args.repository,
        event,
        trust=args.trust,
        upstream=tuple(name.strip() for name in upstream if name.strip()),
        prefix=args.command_prefix,
    ):
        write_outputs(None)
        return 0
    event = route_event(args, event)

    key = args.delivery_key
    if not key:
        if not (run_id := os.getenv("GITHUB_RUN_ID")):
            message = "Supply --delivery-key outside a GitHub workflow."
            raise ValueError(message)
        key = f"action:{run_id}:{os.getenv('GITHUB_RUN_ATTEMPT', '1')}"
    action = await interruptible(
        run(
            args.repository,
            args.delegated_command,
            args.instruction,
            (
                args.db
                or Path(
                    os.getenv("INPUT_DATABASE")
                    or os.getenv("LANDING_DB")
                    or Path(os.getenv("RUNNER_TEMP", ".")) / "landing/landing.sqlite3"
                )
            ).expanduser(),
            Path.cwd(),
            key=key,
            checks=args.check
            if args.check is not None
            else [line for line in os.getenv("INPUT_CHECKS", "").splitlines() if line.strip()],
            event=event,
            reply_required=not bool(event and "workflow_run" in event),
            number=args.number,
            head=args.head,
            run_id=args.run_id,
            checked_revision=args.checked_revision
            if args.checked_revision is not None
            else os.getenv("INPUT_CHECKED_REVISION", os.getenv("GITHUB_SHA", "")),
            skill_dirs=args.skill_dir,
        )
    )
    typer.echo(action.model_dump_json(indent=2))
    write_outputs(action)
    return int(action.status != "completed")

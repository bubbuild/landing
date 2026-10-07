"""Minimal GitHub admission and publication receipts around native agent tools."""

import asyncio
import hashlib
import json
import os
import shutil
import signal
import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Annotated, Literal, cast
from uuid import uuid4

import typer
from bub import hookimpl
from bub.hooks.interception import ToolCallDecision
from bub.tools import ToolContext, tool
from pydantic import AliasChoices, Field, FilePath, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from landing.models import COMMANDS, Action, ActionRequest, FileInput, Mode
from landing.prompts import render
from landing.runtime import Runtime

REPOSITORY_GUIDANCE = "GitHub repository: $repository. Use the prepared gh CLI. In GitHub conversations use #number or owner/repo#number outside code spans; elsewhere use explicit links. Read contribution templates from the checkout's standard GitHub locations when needed."

PUBLICATION_GUIDANCE = "When publishing, include $stamp at the start of the body to identify this delivery. For a body file, use gh pr/issue comment --body-file FILE or gh api -F body=@FILE; -f body=@FILE sends the literal path. Use --input FILE for a JSON payload. Refresh the current PR head before publishing. Supplemental evidence may be linked from the required reply or review; a separate evidence comment cannot replace that publication. The context records the candidate head separately from the actual CI checkout revision."

THREAD_GUIDANCE = "Reply in the original thread with POST repos/$repository/pulls/$number/comments/$thread/replies, rather than a new review. Include the delivery marker, or call confirm_reply with the returned comment ID."

REVIEW_GUIDANCE = "Publish a native GitHub COMMENT review on PR #$number; APPROVE and REQUEST_CHANGES require separate explicit authorization. Use the reviews API with commit_id, body and inline comments containing path, line and side; ranges also use start_line and start_side. Verify locations against the inspected diff. Put the verdict in the review body and findings in inline comments; use the body for findings with no suitable diff location. Use suggestion blocks for verified replacements within the commented range, preserving the surrounding syntax, and <details> for longer reproductions or evidence. Reply to existing findings through the original review thread. Native check jobs are independent of Landing feedback; do not wait for this feedback job or the enclosing workflow to complete."

AUTOMATIC_GUIDANCE = "This is automatic follow-up; no_update is available when there is no useful change."

CONVERSATION_GUIDANCE = "Answer the delegation in issue or PR #$number. Link a repair or supporting evidence when needed; do not repeat an explanation already published in the linked issue, PR or review. Call confirm_reply with the returned conversation comment ID to read back the published body."


class GitHubEnvironment(BaseSettings):
    """Native runner context; configuration files cannot grant caller authority."""

    model_config = SettingsConfigDict(env_prefix="GITHUB_", hide_input_in_errors=True)
    actions: bool = False
    repository: str = ""
    event_name: str = ""
    actor: str = ""
    triggering_actor: str = ""
    run_id: str = ""
    run_attempt: str = "1"
    output: Path | None = None
    step_summary: Path | None = None
    checked_revision: str = Field(default="", validation_alias=AliasChoices("INPUT_CHECKED_REVISION", "GITHUB_SHA"))
    runner_temp: Path = Field(default=Path("."), validation_alias="RUNNER_TEMP")
    admission_token: str | None = Field(default=None, validation_alias="GH_ADMISSION_TOKEN", repr=False)


class GitHubSettings(BaseSettings):
    """Action inputs and explicit CLI overrides, independent of native authority."""

    model_config = SettingsConfigDict(
        env_prefix="INPUT_", env_ignore_empty=True, populate_by_name=True, extra="ignore", hide_input_in_errors=True
    )
    repository: str = Field(validation_alias=AliasChoices("INPUT_REPOSITORY", "GITHUB_REPOSITORY"))
    event: FilePath | None = Field(default=None, validation_alias="GITHUB_EVENT_PATH")
    delegated_command: Literal["review", "fix", "triage", "explain"] = Field(
        default="review", validation_alias="INPUT_COMMAND"
    )
    instruction: str = "Carry out the delegated task using repository guidance."
    trust: Literal["repository", "owner"] = "repository"
    upstream_workflow: Annotated[list[str], NoDecode] = Field(default_factory=list)
    command_prefix: str = "/landing"
    number: int = Field(default=0, ge=0)
    head: str = ""
    checked_revision: str | None = None
    run_id: str = Field(default="", validation_alias=AliasChoices("INPUT_RUN_ID", "GITHUB_RUN_ID"))
    delivery_key: str | None = None
    database: Path | None = None
    check: Annotated[list[str], NoDecode] = Field(default_factory=list, validation_alias="INPUT_CHECKS")

    @field_validator("check", "upstream_workflow", mode="before")
    @classmethod
    def multiline_inputs(cls, value):
        return [line for line in value.splitlines() if line.strip()] if isinstance(value, str) else value


def gh(args: list[str], repository: str, *, token: str | None = None) -> str:
    environment = {**os.environ, "GH_REPO": repository}
    if token:
        environment["GH_TOKEN"] = token
    result = subprocess.run(  # noqa: S603 -- explicit argv with caller-prepared authentication.
        ["gh", *args],  # noqa: S607 -- caller-prepared PATH.
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode:
        message = result.stderr.strip() or "GitHub CLI failed."
        raise RuntimeError(message)
    return result.stdout


def identity(endpoint: str, repository: str) -> dict:
    return json.loads(gh(["api", endpoint], repository, token=GitHubEnvironment().admission_token))


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
    context = GitHubEnvironment()
    if context.repository and repository.casefold() != context.repository.casefold():
        message = "The Action target must be the workflow repository."
        raise ValueError(message)
    if event and event["repository"]["full_name"].casefold() != repository.casefold():
        message = "The event belongs to another repository."
        raise ValueError(message)
    # A local invocation without an event uses the caller's prepared credentials.
    if not event and not context.actions:
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
    native_source = context.event_name in {"push", "release", "workflow_dispatch"} or (
        context.event_name == "workflow_run" and run is not None
    )
    if trust == "repository" and context.actions and native_source and not comment:
        return True
    actor = comment["user"] if comment else run["actor"] if run else event.get("sender")
    actor = actor or identity(f"users/{context.actor}", repository)
    owner = identity(f"repos/{repository}", repository)["owner"] if trust == "owner" else {}
    if not permitted(repository, actor, trust, owner):
        return False
    rerunner = context.triggering_actor
    if trust == "owner" and rerunner and rerunner != context.actor:
        user = identity(f"users/{rerunner}", repository)
        return permitted(repository, user, trust, owner)
    return True


def rows(endpoint: str, repository: str) -> list[dict]:
    return [item for page in json.loads(gh(["api", endpoint, "--paginate", "--slurp"], repository)) for item in page]


def marker(mode: Mode, key: str) -> str:
    digest = hashlib.sha256(key.encode()).hexdigest()[:24]
    return f"<!-- landing:{mode}:{digest} -->"


@dataclass
class Publication:
    """Bind publication receipts and candidate checks to one GitHub delegation."""

    runtime: Runtime
    repository: str
    number: int
    stamp: str
    review: bool
    thread: int
    head: str
    reply_required: bool
    publisher: int = field(default=0, init=False)
    previous_replies: set[int] = field(default_factory=set, init=False)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    batch: str | None = field(default=None, init=False)
    reason: str = field(default="", init=False)
    action_id: str | None = field(default=None, init=False)

    @hookimpl
    def load_state(self):
        return {"_runtime_github_publication": self}

    @hookimpl(specname="provide_lifespan")
    async def publication_lifespan(self):
        tools = self.runtime.tools
        previous = tools[confirm_reply.name]
        tools[confirm_reply.name] = replace(confirm_reply, agent_use=True)
        loop = asyncio.get_running_loop()
        task = cast("asyncio.Task", asyncio.current_task())
        loop.add_signal_handler(signal.SIGTERM, task.cancel)
        try:
            yield
        finally:
            loop.remove_signal_handler(signal.SIGTERM)
            tools[confirm_reply.name] = previous

    def __post_init__(self) -> None:
        if self.number:
            identity = gh(
                ["api", "graphql", "-f", "query={viewer{databaseId}}", "--jq", ".data.viewer.databaseId"],
                self.repository,
            )
            self.publisher = int(identity)
        if self.thread:
            self.previous_replies = {
                item["id"] for item in rows(f"repos/{self.repository}/pulls/{self.number}/comments", self.repository)
            }

    def find(self) -> dict | None:
        if not self.number:
            return None
        kind = "pulls" if self.thread or self.review else "issues"
        resource = "reviews" if self.review and not self.thread else "comments"
        for item in rows(f"repos/{self.repository}/{kind}/{self.number}/{resource}", self.repository):
            if (
                self.stamp in (item.get("body") or "")
                and item.get("user", {}).get("id") == self.publisher
                and item.get("state") != "PENDING"
                and (not self.thread or item.get("in_reply_to_id") == self.thread)
            ):
                return item
        return None

    def read_reply(self, comment_id: int) -> dict:
        kind = "pulls" if self.thread else "issues"
        receipt = json.loads(gh(["api", f"repos/{self.repository}/{kind}/comments/{comment_id}"], self.repository))
        target = f"https://api.github.com/repos/{self.repository}/{kind}/{self.number}"
        matches = (
            receipt.get("pull_request_url") == target and receipt.get("in_reply_to_id") == self.thread
            if self.thread
            else receipt.get("issue_url") == target
        )
        if not matches or receipt.get("user", {}).get("id") != self.publisher:
            message = "The comment is not a reply by the prepared publisher at the delegated destination."
            raise ValueError(message)
        return receipt

    def verify(self, action: Action) -> None:
        tasks = self.runtime.tasks
        if not self.number:
            return
        receipt = self.find()
        if receipt is None and self.thread:
            saved = tasks.event_data(action.id, "github.reply_confirmed")
            if saved:
                receipt = self.read_reply(saved["id"])
        if (
            receipt is None
            and action.mode == "issuer"
            and not self.reply_required
            and tasks.event_data(action.id, "issue.unchanged") is not None
        ):
            return
        if receipt is None or (self.review and self.head and receipt.get("commit_id") != self.head):
            message = "The agent completed without a confirmed publication at the requested destination."
            raise RuntimeError(message)
        if self.review and self.head:
            current = json.loads(gh(["api", f"repos/{self.repository}/pulls/{self.number}"], self.repository))
            if current["head"]["sha"] != self.head:
                message = "The PR head changed; delegate a new review for the current candidate."
                raise RuntimeError(message)
        tasks.event(action.id, "github.published", {"id": receipt["id"], "url": receipt["html_url"]})

    @hookimpl
    async def before_tool_call(self, call, state):
        if not self.review or "landing_action_id" not in state:
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


@tool(context=True, agent_use=False)
def confirm_reply(comment_id: int, *, context: ToolContext) -> str:
    """Read back a published reply at the delegated conversation or inline review thread."""
    publication = cast("Publication", context.state["_runtime_github_publication"])
    if comment_id in publication.previous_replies:
        message = "The reply predates this delegation."
        raise ValueError(message)
    receipt = publication.read_reply(comment_id)
    publication.runtime.tasks.event(context.state["landing_action_id"], "github.reply_confirmed", {"id": comment_id})
    return json.dumps({"url": receipt["html_url"], "body": receipt["body"]})


def repository_context(repository: str) -> FileInput:
    return FileInput(
        name="github-repository.txt",
        content=render(REPOSITORY_GUIDANCE, repository=repository),
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


async def run(landing: Runtime, options: GitHubSettings, event: dict | None = None) -> Action:
    repository, command = options.repository, options.delegated_command
    number, head, key = options.number, options.head, cast(str, options.delivery_key)
    reply_required = not bool(event and "workflow_run" in event)
    workspace = landing.framework.workspace
    mode = COMMANDS[command]
    stamp = marker(mode, key)
    is_pr, thread, head = pull_target(repository, number, head, event, review=mode == "gatekeeper")
    expected_review = is_pr and mode == "gatekeeper" and not thread
    source = {
        "repository": repository,
        "number": number,
        "candidate_head": head,
        "ci_checkout": options.checked_revision,
        "ci_checkout_contains_candidate": checkout_contains(workspace, head, options.checked_revision or ""),
        "native_run_id": options.run_id,
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
    destination = ""
    if thread:
        destination = THREAD_GUIDANCE
    elif expected_review:
        destination = REVIEW_GUIDANCE
    elif mode == "issuer" and not reply_required:
        destination = AUTOMATIC_GUIDANCE
    elif number:
        destination = CONVERSATION_GUIDANCE
    guidance = render(
        PUBLICATION_GUIDANCE, destination, repository=repository, number=number, thread=thread, stamp=stamp
    )
    source_input = FileInput(name="github-context.json", content=json.dumps(source))
    request = ActionRequest(
        mode=mode,
        instruction=options.instruction or "Carry out the delegated work.",
        workspace=landing.workspace_name(workspace),
        input=[repository_context(repository), source_input, FileInput(name="github-guidance.txt", content=guidance)],
        checks=options.check if mode in {"fixer", "gatekeeper"} else [],
    )

    publication = Publication(landing, repository, number, stamp, expected_review, thread, head, reply_required)
    manager = landing.framework.plugin_manager
    manager.register(publication, name="github-publication")
    try:
        async with landing.running():
            existing = publication.find()
            if existing:
                recorded = landing.tasks.find(repository, key)
                # The receipt identifies this delivery; retain its original evidence snapshot on replay.
                snapshot = landing.tasks.request(recorded.id).input if recorded else request.input
                action, _ = landing.tasks.create(
                    request.model_copy(update={"input": snapshot}), scope=repository, key=key
                )
                publication.verify(action)
                return landing.tasks.finish(action.id, "completed", result=f"Already published: {existing['html_url']}")
            try:
                action = await landing.run(
                    request,
                    session_id=f"github:{number or key}",
                    scope=repository,
                    key=key,
                    verify=publication.verify,
                )
            except asyncio.CancelledError:
                if not publication.action_id:
                    raise
                action = landing.tasks.get(publication.action_id)
            return action
    finally:
        manager.unregister(publication)


def write_outputs(action: Action | None) -> None:
    """Write native Action outputs and the step summary when available."""
    outputs = {
        "id": action.id if action else "",
        "status": action.status if action else "skipped",
        "decision": action.decision or "" if action else "",
        "result": action.result or "" if action else "",
    }
    context = GitHubEnvironment()
    if context.output:
        with context.output.open("a") as output:
            for name, value in outputs.items():
                delimiter = uuid4().hex
                output.write(f"{name}<<{delimiter}\n{value}\n{delimiter}\n")
    if context.step_summary:
        with context.step_summary.open("a") as output:
            output.write(outputs["result"] + "\n")


@hookimpl
def register_cli_commands(app: typer.Typer) -> None:
    from landing.cli import Command

    commands = typer.Typer(help="Handle native GitHub events with prepared credentials.", no_args_is_help=True)
    commands.command("event", cls=Command)(github_event)
    app.add_typer(commands, name="github")


def github_event(
    ctx: typer.Context,
    repository: Annotated[str | None, typer.Option(help="Workflow repository.")] = None,
    event: Annotated[
        Path | None,
        typer.Option(exists=True, dir_okay=False, help="Native event JSON file."),
    ] = None,
    delegated_command: Annotated[
        Literal["review", "fix", "triage", "explain"] | None,
        typer.Option("--command", help="Default delegated action."),
    ] = None,
    instruction: Annotated[str | None, typer.Option(help="Work and acceptance criteria.")] = None,
    trust: Annotated[Literal["repository", "owner"] | None, typer.Option(help="Native caller scope.")] = None,
    upstream_workflow: Annotated[list[str] | None, typer.Option(help="Allowed upstream workflow (repeatable).")] = None,
    command_prefix: Annotated[str | None, typer.Option(help="Comment command prefix.")] = None,
    number: Annotated[int | None, typer.Option(min=0, help="Issue or PR number.")] = None,
    head: Annotated[str | None, typer.Option(help="Candidate commit.")] = None,
    checked_revision: Annotated[str | None, typer.Option(help="Revision covered by native checks.")] = None,
    run_id: Annotated[str | None, typer.Option(help="Native run to inspect.")] = None,
    delivery_key: Annotated[str | None, typer.Option(help="Stable delivery identity.")] = None,
    check: Annotated[list[str] | None, typer.Option(help="Required validation (repeatable).")] = None,
) -> None:
    """Admit and route an event, then confirm native publication."""
    parameters = {**locals(), "database": ctx.find_root().params.get("db")}
    options = GitHubSettings(**{
        name: value for name, value in parameters.items() if name != "ctx" and value is not None
    })
    context = GitHubEnvironment()
    payload = json.loads(options.event.read_text()) if options.event else None
    if not admitted(
        options.repository,
        payload,
        trust=options.trust,
        upstream=tuple(name.strip() for name in options.upstream_workflow),
        prefix=options.command_prefix,
    ):
        write_outputs(None)
        raise typer.Exit()
    from landing.cli import execution_runtime, execution_settings

    settings = execution_settings(ctx)
    payload = route_event(options, payload)

    if not options.delivery_key:
        if not context.run_id:
            message = "Supply --delivery-key outside a GitHub workflow."
            raise ValueError(message)
        options.delivery_key = f"action:{context.run_id}:{context.run_attempt}"
    if options.checked_revision is None:
        options.checked_revision = context.checked_revision
    runtime = execution_runtime(
        ctx, settings, database=options.database or settings.db or context.runner_temp / "landing/landing.sqlite3"
    )
    action = asyncio.run(run(runtime, options, payload))
    typer.echo(action.model_dump_json(indent=2))
    write_outputs(action)
    raise typer.Exit(action.status != "completed")


def route_event(args: GitHubSettings, event: dict | None) -> dict | None:
    if event and "comment" in event:
        lines = event["comment"]["body"].strip().splitlines()
        parts = lines[0].split(maxsplit=2)
        if len(parts) < 2 or parts[1] not in COMMANDS:
            message = "Choose triage, fix, review, or explain after the command prefix."
            raise ValueError(message)
        args.delegated_command = parts[1]
        args.instruction = "\n".join([parts[2] if len(parts) > 2 else "", *lines[1:]]).strip()
        target = event.get("issue") or event["pull_request"]
        # pull_target reads the current head for a PR; the comment event does not carry it.
        args.number = target["number"]
    elif event and "workflow_run" in event:
        args.delegated_command, args.run_id = "triage", str(event["workflow_run"]["id"])
    elif event and not args.number:
        pull = event.get("pull_request")
        if pull:
            args.number, args.head = pull["number"], pull["head"]["sha"]

    return event

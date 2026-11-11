"""Native publication policy, receipts, and per-action Bub hooks."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import subprocess
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from bub import hookimpl, tool
from bub.core.events import ToolCallDecision
from bub.core.tools import ToolContext
from pydantic import BaseModel

from landing.models import Action, ActionRequest, Decision, Mode

from .api import gh, rows

if TYPE_CHECKING:
    from landing.runtime import Runtime

PUBLICATION_GUIDANCE = (
    "When publishing, include $stamp at the start of the body to identify this delivery. "
    "For a body file, use gh pr/issue comment --body-file FILE or gh api -F body=@FILE; "
    "-f body=@FILE sends the literal path. Use --input FILE for a JSON payload. "
    "Refresh the current PR head before publishing. Supplemental evidence may be linked from the required reply or "
    "review; a separate evidence comment cannot replace that publication. "
    "The context records the candidate head separately from the actual CI checkout revision."
)


THREAD_GUIDANCE = (
    "Reply in the original thread with POST repos/$repository/pulls/$number/comments/$thread/replies, rather than a new "
    "review. Include the delivery marker, or call confirm_reply with the returned comment ID."
)


REVIEW_GUIDANCE = (
    "Publish a native GitHub COMMENT review on PR #$number; APPROVE and REQUEST_CHANGES require separate explicit "
    "authorization. Use the reviews API with commit_id, body and inline comments containing path, line and side; "
    "ranges also use start_line and start_side. Verify locations against the inspected diff. "
    "Put the verdict in the review body and findings in inline comments; use the body for findings with no suitable "
    "diff location. Use suggestion blocks for verified replacements within the commented range, preserving the "
    "surrounding syntax, and <details> for longer reproductions or evidence. "
    "Before publishing, read the PR's existing review threads, including their resolved and outdated state, through the "
    "GraphQL reviewThreads connection. Add inline comments only for findings that no existing thread raises. "
    "Leave a still-applicable open finding in its thread and refer to it from the review body by link; "
    "reply in that thread only when evidence changes the conclusion, impact or required repair, or answers a question. "
    "A new SHA, moved line or repeated successful reproduction alone is not a discussion update. "
    "Resolved threads stay settled unless new evidence changes their conclusion. "
    "Native check jobs are independent of Landing feedback; do not wait for this feedback job or the enclosing workflow "
    "to complete."
)


REVIEW_STATUS_MARKER = "<!-- landing:review-status "


AUTOMATIC_REVIEW_GUIDANCE = (
    "This is an automatic candidate review. Landing publishes your final response in one reusable PR status comment. "
    "Do not submit a native review just to announce an unchanged or clean verdict, or edit the status comment yourself. "
    "Read the current Landing status and existing GraphQL reviewThreads, including resolved and outdated state, "
    "before deciding what changed. Resolved findings stay settled unless new evidence changes their conclusion. "
    "Group new findings in one native COMMENT review, with an inline thread for each independent finding "
    "where the diff supports it. Keep the parent review brief; do not repeat the inline explanations. "
    "A finding without a suitable diff location may use that review's body. "
    "APPROVE and REQUEST_CHANGES require separate explicit authorization. "
    "Reuse existing finding threads. Reply only to report a changed conclusion, impact or required repair, "
    "confirm a fix, withdraw a claim, or answer a question. A new SHA, moved line or repeated reproduction "
    "does not warrant a reply. Do not restate unchanged findings. "
    "Read back findings and inline replies through the native reviews/comments API; confirm_reply here targets "
    "the PR conversation, not those inline threads. "
    "Record your recommendation with decide. Your final response is the current status, not a work report: "
    "one short sentence when clean; otherwise use only short labels and links for outstanding findings, "
    "or state an unresolved acceptance condition without an existing finding. Do not repeat a linked finding's "
    "explanation or repair. Omit the verdict heading, candidate hash and delivery marker from the final response; "
    "Landing supplies them. "
    "Keep scripts, check inventories and unchanged evidence in execution history, not the status comment. "
    "Native check jobs are independent of Landing feedback; do not wait for this job or the enclosing workflow."
)


AUTOMATIC_GUIDANCE = "This is automatic follow-up; no_update is available when there is no useful change."


CONVERSATION_GUIDANCE = (
    "Answer the delegation in issue or PR #$number. Link a repair or supporting evidence when needed; "
    "do not repeat an explanation already published in the linked issue, PR or review. "
    "Call confirm_reply with the returned conversation comment ID to read back the published body. "
    "After successful delivery, return a short link to that reply rather than repeating its body in the final response."
)


def marker(mode: Mode, key: str) -> str:
    digest = hashlib.sha256(key.encode()).hexdigest()[:24]
    return f"<!-- landing:{mode}:{digest} -->"


class ReviewStatus(BaseModel):
    """Candidate and advisory decision carried by the mutable PR status."""

    head: str
    decision: Decision


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
    automatic_review: bool = False
    publisher: int = field(default=0, init=False)
    previous_replies: set[int] = field(default_factory=set, init=False)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    batch: str | None = field(default=None, init=False)
    reason: str = field(default="", init=False)
    action_id: str | None = field(default=None, init=False)

    @hookimpl
    def load_state(self):
        return {"_runtime_github_publication": self}

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
        kind = "pulls" if self.thread or (self.review and not self.automatic_review) else "issues"
        resource = "reviews" if self.review and not self.thread and not self.automatic_review else "comments"
        for item in rows(f"repos/{self.repository}/{kind}/{self.number}/{resource}", self.repository):
            if (
                self.stamp in (item.get("body") or "")
                and item.get("user", {}).get("id") == self.publisher
                and item.get("state") != "PENDING"
                and (not self.thread or item.get("in_reply_to_id") == self.thread)
                and (
                    not self.automatic_review
                    or ((status := self.status_metadata(item)) is not None and status.head == self.head)
                )
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

    def status_metadata(self, receipt: dict) -> ReviewStatus | None:
        header = (receipt.get("body") or "").split("\n", 1)[0]
        if header.startswith(REVIEW_STATUS_MARKER) and header.endswith(" -->"):
            return ReviewStatus.model_validate_json(header[len(REVIEW_STATUS_MARKER) : -4])
        return None

    def check_head(self) -> None:
        if self.review and self.head:
            current = json.loads(gh(["api", f"repos/{self.repository}/pulls/{self.number}"], self.repository))
            if current["head"]["sha"] != self.head:
                message = "The PR head changed; delegate a new review for the current candidate."
                raise RuntimeError(message)

    def publish_status(self, action: Action) -> None:
        metadata = ReviewStatus(head=self.head, decision=action.decision or "inconclusive")
        body = (
            f"{REVIEW_STATUS_MARKER}{metadata.model_dump_json()} -->\n{self.stamp}\n"
            f"**Landing: {metadata.decision}** · [{self.head[:7]}]"
            f"(https://github.com/{self.repository}/commit/{self.head})\n\n{action.result}"
        )
        previous = next(
            (
                item
                for item in rows(f"repos/{self.repository}/issues/{self.number}/comments", self.repository)
                if item.get("user", {}).get("id") == self.publisher and self.status_metadata(item) is not None
            ),
            None,
        )
        self.check_head()
        if previous and previous["body"] == body:
            receipt = previous
        else:
            endpoint = (
                f"repos/{self.repository}/issues/comments/{previous['id']}"
                if previous
                else f"repos/{self.repository}/issues/{self.number}/comments"
            )
            written = json.loads(
                gh(["api", endpoint, "-X", "PATCH" if previous else "POST", "-f", f"body={body}"], self.repository)
            )
            receipt = self.read_reply(written["id"])
        if receipt.get("body") != body:
            message = "The review status could not be confirmed."
            raise RuntimeError(message)

    def complete(self, action: Action) -> None:
        if self.automatic_review:
            self.publish_status(action)
        self.verify(action)

    def verify(self, action: Action) -> None:
        tasks = self.runtime.tasks
        if not self.number:
            return
        self.check_head()
        receipt = self.find()
        if receipt is not None and self.automatic_review:
            receipt = self.read_reply(receipt["id"])
            metadata = self.status_metadata(receipt)
            if (
                metadata is None
                or metadata.head != self.head
                or self.stamp not in receipt["body"]
                or (
                    action.decision is not None
                    and (metadata.decision != action.decision or receipt["body"].partition("\n\n")[2] != action.result)
                )
            ):
                message = "The review status no longer matches this delivery."
                raise RuntimeError(message)
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
        if receipt is None or (
            self.review and not self.automatic_review and self.head and receipt.get("commit_id") != self.head
        ):
            message = "The agent completed without a confirmed publication at the requested destination."
            raise RuntimeError(message)
        self.check_head()
        tasks.event(action.id, "github.published", {"id": receipt["id"], "url": receipt["html_url"]})

    def replay(self, request: ActionRequest, key: str) -> Action | None:
        """Reuse delivery evidence without replacing execution or validation history."""
        tasks = self.runtime.tasks
        receipt = self.find()
        recorded = tasks.find(self.repository, key)
        if receipt is None:
            if recorded and recorded.status == "completed":
                self.verify(recorded)
            return None
        if recorded and recorded.status != "completed":
            return recorded
        # Retain this delivery's original evidence when a later event repeats it.
        snapshot = tasks.request(recorded.id).input if recorded else request.input
        action, _ = tasks.create(request.model_copy(update={"input": snapshot}), scope=self.repository, key=key)
        if not recorded and request.mode in {"fixer", "bumper"}:
            return tasks.finish(
                action.id,
                "failed",
                result=f"Already published: {receipt['html_url']}",
                error={
                    "code": "unknown",
                    "message": "The original execution and validation record is unavailable. "
                    "Delegate a new key with current evidence instead of repeating this delivery.",
                },
            )
        self.verify(action)
        if self.automatic_review and (status := self.status_metadata(receipt)):
            return tasks.finish(
                action.id, "completed", result=receipt["body"].partition("\n\n")[2], decision=status.decision
            )
        return tasks.finish(action.id, "completed", result=f"Already published: {receipt['html_url']}")

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


@tool(context=True)
def confirm_reply(comment_id: int, *, context: ToolContext) -> str:
    """Read back a published reply at the delegated conversation or inline review thread."""
    publication = cast("Publication", context.state["_runtime_github_publication"])
    if comment_id in publication.previous_replies:
        message = "The reply predates this delegation."
        raise ValueError(message)
    receipt = publication.read_reply(comment_id)
    publication.runtime.tasks.event(context.state["landing_action_id"], "github.reply_confirmed", {"id": comment_id})
    return json.dumps({"url": receipt["html_url"], "body": receipt["body"]})


def reaction_endpoint(repository: str, event: dict | None) -> str | None:
    """Locate the triggering comment or PR, which receives Landing's progress reaction."""
    comment = (event or {}).get("comment")
    if comment:
        kind = "pulls" if "pull_request_review_id" in comment else "issues"
        return f"repos/{repository}/{kind}/comments/{comment['id']}/reactions" if "id" in comment else None
    pull = (event or {}).get("pull_request") or {}
    return f"repos/{repository}/issues/{pull['number']}/reactions" if "number" in pull else None


def react(repository: str, endpoint: str, content: str) -> dict | None:
    # Reactions only signal progress; a missing reaction permission must not fail the delegated work.
    try:
        return json.loads(gh(["api", endpoint, "-f", f"content={content}"], repository))
    except (RuntimeError, subprocess.TimeoutExpired, ValueError) as exc:
        logging.getLogger(__name__).warning(f"Cannot add the {content} reaction: {exc}")
        return None


def unreact(repository: str, endpoint: str, reaction: dict) -> None:
    try:
        gh(["api", f"{endpoint}/{reaction['id']}", "-X", "DELETE"], repository)
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        logging.getLogger(__name__).warning(f"Cannot remove the {reaction['content']} reaction: {exc}")


def begin_reaction(repository: str, endpoint: str) -> dict | None:
    progress = react(repository, endpoint, "eyes")
    if progress is None:
        return None
    # A new PR candidate replaces the previous run's outcome, so the PR shows only the latest result.
    try:
        reactions = rows(endpoint, repository)
    except (RuntimeError, subprocess.TimeoutExpired, ValueError) as exc:
        logging.getLogger(__name__).warning(f"Cannot read earlier reactions: {exc}")
        reactions = []
    for item in reactions:
        if item.get("user", {}).get("id") == progress["user"]["id"] and item.get("content") in {"rocket", "confused"}:
            unreact(repository, endpoint, item)
    return progress


def conclude_reaction(repository: str, endpoint: str, progress: dict | None, status: str) -> None:
    if progress is not None:
        unreact(repository, endpoint, progress)
    # A superseded or terminated run leaves no outcome; the next run reports its own.
    if status != "cancelled":
        react(repository, endpoint, "rocket" if status == "completed" else "confused")

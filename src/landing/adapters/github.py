"""Necessary GitHub wiring; platform capabilities come from the installed gh CLI."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
from collections.abc import Iterable
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import cast

from bub.tools import Tool, ToolContext

from landing.models import MODES, Action, ActionRequest, FileInput, Mode
from landing.runtime import Runtime

READ = {
    ("issue", "list"),
    ("issue", "view"),
    ("pr", "list"),
    ("pr", "view"),
    ("pr", "diff"),
    ("pr", "checks"),
    ("run", "list"),
    ("run", "view"),
    ("repo", "view"),
}
ISSUER = {("issue", "create"), ("issue", "edit"), ("issue", "comment")}


def gh(args: list[str], repository: str, *, body: str | None = None, env: dict | None = None) -> str:
    """Use gh's active OAuth login locally and the workflow token in CI."""
    if args[:2] == ["repo", "view"] and (len(args) == 2 or args[2].startswith("-")):
        args = [*args[:2], repository, *args[2:]]
    with NamedTemporaryFile(mode="w", suffix=".md", encoding="utf-8") as text:
        if body is not None:
            text.write(body)
            text.flush()
            args = [*args, "--body-file", text.name]
        environment = {**(os.environ if env is None else env), "GH_REPO": repository}
        result = subprocess.run(  # noqa: S603 -- explicit argv; no shell or token arguments.
            ["/usr/bin/gh", *args], env=environment, capture_output=True, text=True, timeout=120
        )
        if result.returncode:
            message = result.stderr.strip() or "GitHub CLI failed."
            raise RuntimeError(message)
        return result.stdout


def github_tool(repository: str) -> Tool:
    credentials = dict(os.environ)

    def invoke(args: list[str], body: str | None = None, *, context: ToolContext) -> str:
        """Run gh read commands or issuer issue operations in this repository. Supply multiline text as body.
        Use issue list/view, pr list/view/diff/checks, run list/view, repo view. Issuer can create/edit/comment issues.
        No merging, approving, credential changes, shell syntax, or arbitrary API writes.
        """
        pair = tuple(args[:2])
        api_read = len(args) >= 2 and args[0] == "api" and args[1].startswith(f"repos/{repository}/")
        if pair not in READ and not api_read and not (context.state["landing_mode"] == "issuer" and pair in ISSUER):
            message = "This gh operation is unavailable in the current mode."
            raise ValueError(message)
        if any(arg.startswith(("-R", "--repo", "--hostname", "http://", "https://")) for arg in args):
            message = "Use identifiers in the configured repository."
            raise ValueError(message)
        if (
            pair == ("repo", "view")
            and len(args) > 2
            and not args[2].startswith("-")
            and args[2].lower() != repository.lower()
        ):
            message = "Use the configured repository."
            raise ValueError(message)
        if "--body" in args or "--body-file" in args or any(arg.startswith("--body=") for arg in args):
            message = "Supply the message through the body parameter."
            raise ValueError(message)
        if api_read:
            if body is not None or any(
                arg.startswith(("--method", "--input", "--field", "--raw-field", "-X", "-F", "-f")) for arg in args[2:]
            ):
                message = "The gh API tool supports repository GET requests only."
                raise ValueError(message)
            args = [*args, "--method", "GET"]
        output = gh(args, repository, body=body, env=credentials)
        if len(output) > 100000:
            return "[Earlier output omitted; narrow the query for complete evidence.]\n" + output[-100000:]
        return output if output.strip() else "Command succeeded; no matching results."

    return Tool.from_callable(invoke, name="gh", context=True)


def delegation(event: dict, repository: str) -> tuple[Mode, str, int] | None:
    if event["repository"]["full_name"].lower() != repository.lower():
        message = "The event belongs to another repository."
        raise ValueError(message)
    comment = event["comment"]
    if comment["user"]["type"] == "Bot":
        return None
    line = next((line for line in (comment.get("body") or "").splitlines() if line.startswith("@landing ")), "")
    parts = line.split(maxsplit=2)
    if len(parts) < 2 or parts[1] not in MODES:
        return None
    permission = json.loads(
        gh(["api", f"repos/{repository}/collaborators/{comment['user']['login']}/permission"], repository)
    )
    if permission["permission"] not in {"admin", "maintain", "write"}:
        message = "A repository maintainer must delegate this action."
        raise ValueError(message)
    return cast("Mode", parts[1]), parts[2] if len(parts) > 2 else "", event["issue"]["number"]


def reply(
    repository: str, number: int, mode: str, text: str, *, head: str = "", login: str = "github-actions[bot]"
) -> str | None:
    """Update one mode reply using gh, checking the current head immediately before writing."""
    marker = f"<!-- landing:{mode} -->"
    rows = gh(
        ["api", f"repos/{repository}/issues/{number}/comments?per_page=100", "--paginate", "--jq", ".[] | @json"],
        repository,
    )
    comments = [json.loads(row) for row in rows.splitlines() if row.strip()]
    if head:
        current = json.loads(gh(["pr", "view", str(number), "--json", "headRefOid,state"], repository))
        if current["headRefOid"] != head or current["state"] != "OPEN":
            return None
    existing = next(
        (c for c in comments if c["user"]["login"] == login and (c.get("body") or "").startswith(marker)), None
    )
    body = marker + "\n" + text.encode()[:56000].decode(errors="ignore")
    endpoint = (
        f"repos/{repository}/issues/comments/{existing['id']}"
        if existing
        else f"repos/{repository}/issues/{number}/comments"
    )
    method = "PATCH" if existing else "POST"
    # gh api consumes the body as JSON from stdin; tokens remain in its normal credential source.
    result = subprocess.run(  # noqa: S603 -- explicit argv and JSON stdin.
        ["/usr/bin/gh", "api", endpoint, "--method", method, "--input", "-"],
        input=json.dumps({"body": body}),
        text=True,
        capture_output=True,
        timeout=120,
        check=True,
    )
    return json.loads(result.stdout)["html_url"]


def git(args: list[str], workspace: Path) -> str:
    return subprocess.run(  # noqa: S603 -- explicit argv, checked status, no shell.
        ["/usr/bin/git", *args],
        cwd=workspace,
        env={**os.environ, "GIT_CONFIG_COUNT": "0"},
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    ).stdout


def publish_fix(
    repository: str, number: int, workspace: Path, action: Action, revision: str, base: str = ""
) -> str | None:
    dirty = git(["status", "--porcelain"], workspace).strip()
    if not dirty and git(["rev-parse", "HEAD"], workspace).strip() == revision:
        return None
    branch = f"landing/fix-{number}"
    if dirty:
        git(["add", "--all"], workspace)
        git(
            [
                "-c",
                "user.name=Landing",
                "-c",
                "user.email=landing@users.noreply.github.com",
                "commit",
                "-m",
                f"fix: address issue {number}",
            ],
            workspace,
        )
    git(
        [
            "-c",
            "credential.helper=",
            "-c",
            "credential.helper=!/usr/bin/gh auth git-credential",
            "push",
            f"https://github.com/{repository}.git",
            f"HEAD:refs/heads/{branch}",
        ],
        workspace,
    )
    existing = json.loads(gh(["pr", "list", "--head", branch, "--json", "url"], repository))
    body = f"{action.result}\n\nAddresses #{number}.\n\nAction `{action.id}`. Independent CI and human review are required."
    if existing:
        gh(["pr", "edit", existing[0]["url"]], repository, body=body)
        url = existing[0]["url"]
    else:
        args = ["pr", "create", "--head", branch, "--title", f"fix: address issue {number}"]
        if base:
            args += ["--base", base]
        url = gh(args, repository, body=body).strip()
    # GITHUB_TOKEN-created PRs do not emit another CI run. Dispatch the repository's
    # existing checks explicitly; ordinary OAuth pushes already trigger PR checks.
    workflow = os.getenv("LANDING_CHECK_WORKFLOW")
    if os.getenv("GITHUB_ACTIONS") and workflow:
        head = git(["rev-parse", "HEAD"], workspace).strip()
        gh(
            [
                "workflow",
                "run",
                workflow,
                "--ref",
                branch,
                "-f",
                f"number={url.rsplit('/', 1)[-1]}",
                "-f",
                f"head={head}",
            ],
            repository,
        )
    return url


async def run(  # noqa: C901 -- linear admission, execution, and delivery around the existing runtime.
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
    base: str = "",
    skill_dirs: Iterable[Path] = (),
) -> Action:
    if mode == "fixer" and not number:
        message = "Delegate fixer to a specific issue or PR."
        raise ValueError(message)
    inputs = []
    if number:
        issue = gh(["issue", "view", str(number), "--json", "title,body,comments,url"], repository)
        inputs.append(FileInput(name="work-item.json", content=issue))
    if run_id:
        evidence = gh(["run", "view", run_id, "--json", "jobs,conclusion,headSha,url,workflowName"], repository)
        inputs.append(FileInput(name="native-checks.json", content=evidence))
        if checked_revision:
            inputs.append(
                FileInput(
                    name="ci-checkout.txt",
                    content=f"Native CI checked out {checked_revision}. This workflow-supplied checkout revision is authoritative; run headSha identifies the triggering change and does not override it.",
                )
            )
        try:
            logs = gh(["run", "view", run_id, "--log-failed"], repository)
        except RuntimeError:
            logs = (
                "Logs are unavailable while the parent workflow is running. Read individual completed job logs with gh."
            )
        inputs.append(FileInput(name="failed-checks.log", content=logs[-100000:]))
    db.parent.mkdir(parents=True, exist_ok=True)
    async with Runtime(db, tools=[github_tool(repository)], skill_dirs=skill_dirs).running() as runtime:
        known = runtime.tasks.connection.execute(
            "SELECT id FROM actions WHERE idempotency_scope=? AND idempotency_key=?", (repository, key)
        ).fetchone()
        if known:
            action = runtime.tasks.get(known["id"])
            original = runtime.tasks.request(action.id)
            if original.mode != mode or original.instruction != instruction:
                message = "The delivery key belongs to another delegation."
                raise ValueError(message)
        else:
            target = db.parent / "checkout"
            revision = head or base or "HEAD"
            if mode == "fixer" and not head:
                candidates = json.loads(
                    gh(["pr", "list", "--head", f"landing/fix-{number}", "--json", "headRefOid"], repository)
                )
                if candidates:
                    revision = candidates[0]["headRefOid"]
            if revision != "HEAD":
                try:
                    revision = git(["rev-parse", "--verify", revision + "^{commit}"], workspace).strip()
                except subprocess.CalledProcessError:
                    git(
                        [
                            "-c",
                            "credential.helper=",
                            "-c",
                            "credential.helper=!/usr/bin/gh auth git-credential",
                            "fetch",
                            f"https://github.com/{repository}.git",
                            revision,
                        ],
                        workspace,
                    )
                    revision = "FETCH_HEAD"
            git(["worktree", "add", "--detach", str(target), revision], workspace)
            inspected = git(["rev-parse", "HEAD"], target).strip()
            inputs.append(
                FileInput(name="revision.txt", content=git(["show", "-s", "--format=fuller", "HEAD"], target))
            )
            request = ActionRequest(
                mode=mode,
                instruction=instruction,
                input=[*inputs],
                workspace=str(target),
                checks=checks if mode in {"fixer", "gatekeeper"} else [],
            )
            action, _ = runtime.tasks.create(
                request,
                scope=repository,
                key=key,
                event=(
                    "github.target",
                    {
                        "number": number,
                        "head": head,
                        "revision": inspected,
                        "workspace": str(target),
                        "checked_revision": checked_revision,
                    },
                ),
            )
        if action.status == "queued":
            credentials = {name: os.environ.pop(name) for name in ("GH_TOKEN", "GITHUB_TOKEN") if name in os.environ}
            try:
                async with asyncio.timeout(600):
                    action = await runtime.execute(action.id)
            except TimeoutError:
                action = runtime.tasks.get(action.id)
            finally:
                os.environ.update(credentials)
        target_record = json.loads(
            runtime.tasks.connection.execute(
                "SELECT data FROM action_events WHERE action_id=? AND type='github.target' LIMIT 1", (action.id,)
            ).fetchone()[0]
        )
        text = f"Action `{action.id}`: {action.status}; decision: {action.decision or 'not applicable'}.\n\n{action.result or 'No explanation returned.'}\n\nInspected revision: `{target_record['revision']}`."
        if target_record.get("checked_revision"):
            text += f"\nNative CI checkout revision (supplied by the workflow): `{target_record['checked_revision']}`."
        if action.error:
            text += "\n\n" + action.error["message"]
        delivered = runtime.tasks.connection.execute(
            "SELECT 1 FROM action_events WHERE action_id=? AND type='github.delivered'", (action.id,)
        ).fetchone()
        if action.mode == "fixer" and action.status == "completed" and not delivered:
            if (
                target_record["head"]
                and json.loads(gh(["pr", "view", str(number), "--json", "headRefOid"], repository))["headRefOid"]
                != target_record["head"]
            ):
                message = "The delegated PR advanced; inspect the saved changes before publishing."
                raise ValueError(message)
            url = publish_fix(
                repository, number, Path(target_record["workspace"]), action, target_record["revision"], base
            )
            text += "\n\n" + (f"Candidate: {url}" if url else "No code changes were produced.")
        db.parent.joinpath("summary.md").write_text(text + "\n")
        if number and not delivered:
            try:
                login = (
                    "github-actions[bot]"
                    if os.getenv("GITHUB_ACTIONS")
                    else gh(["api", "user", "--jq", ".login"], repository).strip()
                )
                url = reply(repository, number, mode, text, head=target_record["head"], login=login)
            except Exception as exc:
                runtime.tasks.event(action.id, "github.delivery_failed", {"error": str(exc)})
                raise
            runtime.tasks.event(action.id, "github.delivered", {"url": url, "superseded": url is None})
        db.parent.joinpath("summary.md").write_text(text + "\n")
        return action


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=MODES, nargs="?", default="gatekeeper")
    parser.add_argument(
        "--repository", default=os.getenv("GITHUB_REPOSITORY"), required=not bool(os.getenv("GITHUB_REPOSITORY"))
    )
    parser.add_argument("--number", type=int, default=0)
    parser.add_argument("--head", default="")
    parser.add_argument("--base", default="", help="Base branch for the isolated checkout and fix PR")
    parser.add_argument("--run-id", default="")
    parser.add_argument(
        "--checked-revision", default="", help="Actual native CI checkout SHA, separate from run headSha"
    )
    parser.add_argument(
        "--instruction",
        default="Investigate the supplied evidence. Use gh for existing issues, PRs, and checks; explain what needs attention and the next useful action.",
    )
    parser.add_argument("--event", type=Path)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--check", action="append", default=[])
    parser.add_argument(
        "--skill-dir", type=Path, action="append", default=[], help="Additional trusted skill root (repeatable)"
    )
    parser.add_argument("--delivery-key", required=True)
    args = parser.parse_args()
    if args.event:
        event = json.loads(args.event.read_text())
        if "comment" in event:
            delegated = delegation(event, args.repository)
            if delegated is None:
                return
            args.mode, args.instruction, args.number = delegated
            if "pull_request" in event["issue"]:
                pull = json.loads(gh(["api", f"repos/{args.repository}/pulls/{args.number}"], args.repository))
                if pull["head"]["repo"]["full_name"].lower() != args.repository.lower():
                    parser.error("Delegate to a candidate in this repository.")
                args.head = pull["head"]["sha"]
        elif "workflow_run" in event:
            workflow = event["workflow_run"]
            default = json.loads(gh(["repo", "view", "--json", "defaultBranchRef"], args.repository))[
                "defaultBranchRef"
            ]["name"]
            if workflow["head_branch"] != default or workflow["event"] == "pull_request":
                return
            args.mode, args.run_id = "issuer", str(workflow["id"])
            args.instruction = "Maintain the existing CI issue for this workflow. Search open and closed issues before creating one. Reopen a recurring problem; close only with reliable recovery evidence for the current default-branch revision. Record the evidence URL, reproduction and acceptance criteria. Do not create work for a healthy run without an existing problem."
    action = asyncio.run(
        run(
            args.repository,
            cast("Mode", args.mode),
            args.instruction,
            args.db,
            Path.cwd(),
            number=args.number,
            head=args.head,
            run_id=args.run_id,
            checked_revision=args.checked_revision,
            key=args.delivery_key,
            checks=args.check,
            base=args.base,
            skill_dirs=args.skill_dir,
        )
    )
    print(action.model_dump_json(indent=2))
    raise SystemExit(int(action.status != "completed"))


if __name__ == "__main__":
    main()

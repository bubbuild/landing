"""Exercise SDK permissions, isolated fixes, and delivery retries through gh."""

import asyncio
import json
import re
from contextlib import closing

import pytest

from landing.adapters import github
from landing.models import Action, ActionRequest
from landing.runtime import Runtime
from landing.tasks import Tasks
from tests.conftest import completion


@pytest.mark.parametrize(
    ("mode", "args", "allowed"),
    [
        ("issuer", ["issue", "create", "--title", "A verified problem"], True),
        ("explainer", ["issue", "view", "2"], True),
        ("explainer", ["repo", "view", "other/repository"], False),
        ("gatekeeper", ["pr", "merge", "2"], False),
        ("fixer", ["auth", "token"], False),
        ("issuer", ["issue", "create", "-Rother/repository"], False),
        ("issuer", ["api", "repos/example/landing/issues", "--method=POST"], False),
        ("explainer", ["api", "repos/example/landing/actions/jobs/1/logs"], True),
    ],
)
def test_real_sdk_scopes_gh_capabilities(tmp_path, monkeypatch, model, mode, args, allowed):
    calls = []

    def invoke(args, repository, **kwargs):
        calls.append((args, repository, kwargs))
        return "Verified GitHub evidence."

    monkeypatch.setattr(github, "gh", invoke)
    responses, _ = model
    responses.extend([completion(tool="gh", arguments={"args": args}), completion("Explained the evidence.")])

    async def run():
        async with Runtime(
            tmp_path / "landing.sqlite3", tools=[github.github_tool("example/landing")]
        ).running() as runtime:
            action = await runtime.run(
                ActionRequest(mode=mode, instruction="Investigate the problem.", workspace=str(tmp_path))
            )
            assert action.status == "completed"
            tape = runtime.agent.tape.session_tape(action.id, tmp_path)
            assert runtime.store.read(tape.name)

    asyncio.run(run())
    assert bool(calls) == allowed
    if allowed and args[0] == "api":
        assert calls[0][0][-2:] == ["--method", "GET"]


def test_comment_requires_maintainer_and_never_reacts_to_bot(monkeypatch):
    event = {
        "repository": {"full_name": "example/landing"},
        "issue": {"number": 2},
        "comment": {"body": "@landing fixer Fix the recovery bug.", "user": {"type": "Bot", "login": "bot"}},
    }
    assert github.delegation(event, "example/landing") is None
    event["comment"]["user"] = {"type": "User", "login": "contributor"}
    monkeypatch.setattr(github, "gh", lambda *args: '{"permission":"read"}')
    with pytest.raises(ValueError, match="maintainer"):
        github.delegation(event, "example/landing")
    monkeypatch.setattr(github, "gh", lambda *args: '{"permission":"write"}')
    assert github.delegation(event, "example/landing") == ("fixer", "Fix the recovery bug.", 2)
    with pytest.raises(ValueError, match="another repository"):
        github.delegation(event, "other/repository")


def test_stale_head_is_checked_after_comment_lookup_and_never_written(monkeypatch):
    calls = []

    def invoke(args, repository):
        calls.append(args)
        return "" if args[0] == "api" else '{"headRefOid":"new-head","state":"OPEN"}'

    monkeypatch.setattr(github, "gh", invoke)
    assert github.reply("example/landing", 2, "gatekeeper", "Old recommendation.", head="old-head") is None
    assert len(calls) == 2


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    github.git(["init", "--initial-branch=github-loop-fixture"], root)
    github.git(["config", "user.name", "Landing Test"], root)
    github.git(["config", "user.email", "landing@example.test"], root)
    (root / "source.txt").write_text("Initial implementation.\n")
    github.git(["add", "."], root)
    github.git(["commit", "-m", "Initial implementation"], root)
    return root


def test_issuer_publishes_issue_form_fields_from_selected_checkout(tmp_path, checkout, monkeypatch, model):
    form = checkout / ".github/ISSUE_TEMPLATE/bug.yml"
    form.parent.mkdir(parents=True)
    form.write_text(
        "name: Bug report\nbody:\n  - type: textarea\n    attributes:\n      label: Reproduction\n    validations:\n      required: true\n  - type: textarea\n    attributes:\n      label: Expected behavior\n"
    )
    github.git(["add", "."], checkout)
    github.git(["commit", "-m", "Add issue form"], checkout)
    bodies = []

    def gh(args, repository, *, body=None, **kwargs):
        if args[:2] == ["issue", "create"]:
            bodies.append(body)
            return "https://example.test/issues/3"
        return "test-login"

    monkeypatch.setattr(github, "gh", gh)
    responses, _ = model

    async def issue(**kwargs):
        fields = re.findall(r"label: ([^\\\n]+)", str(kwargs["messages"]))
        body = "\n\n".join(f"### {field}\nVerified evidence." for field in fields)
        return completion(
            tool="gh", arguments={"args": ["issue", "create", "--title", "Reported behavior"], "body": body}
        )

    responses.extend([issue, completion("Created the actionable issue.")])
    action = asyncio.run(
        github.run(
            "example/landing",
            "issuer",
            "Report the bug.",
            tmp_path / "evidence/landing.sqlite3",
            checkout,
            key="issue-form",
            checks=[],
        )
    )
    assert action.status == "completed"
    assert bodies == ["### Reproduction\nVerified evidence.\n\n### Expected behavior\nVerified evidence."]


@pytest.mark.parametrize("template_path", ["docs/pull_request_template.md", ".github/PULL_REQUEST_TEMPLATE/patch.txt"])
def test_fixer_publishes_template_shaped_body_with_checked_candidate(
    tmp_path, checkout, monkeypatch, model, template_path
):
    template = checkout / template_path
    template.parent.mkdir(parents=True)
    template.write_text("## Summary\n\n## Validation\n\n- [ ] Human acceptance\n")
    github.git(["add", "."], checkout)
    github.git(["commit", "-m", "Add PR template"], checkout)
    bodies = []
    original_git = github.git

    def git(args, workspace):
        return "" if "push" in args else original_git(args, workspace)

    def gh(args, repository, *, body=None, **kwargs):
        if args[:2] == ["pr", "create"]:
            bodies.append(body)
            return "https://example.test/pull/3"
        return "[]" if args[0] == "pr" else "{}" if args[0] == "issue" else "test-login"

    monkeypatch.setattr(github, "git", git)
    monkeypatch.setattr(github, "gh", gh)
    monkeypatch.setattr(github, "reply", lambda *args, **kwargs: "https://example.test/comment")
    responses, _ = model

    async def summary(**kwargs):
        headings = re.findall(r"## (Summary|Validation)", str(kwargs["messages"]))
        return completion(
            "\n\n".join(f"## {heading}\nVerified candidate." for heading in headings) + "\n\n- [ ] Human acceptance"
        )

    responses.extend([
        completion(tool="fs_write", arguments={"path": "source.txt", "content": "Verified fix.\n"}),
        summary,
    ])
    db = tmp_path / "evidence/landing.sqlite3"
    action = asyncio.run(
        github.run(
            "example/landing",
            "fixer",
            "Fix the issue.",
            db,
            checkout,
            number=2,
            key="pr-template",
            checks=['test "$(cat source.txt)" = "Verified fix."'],
        )
    )
    assert action.status == "completed"
    assert bodies[0].startswith("## Summary\nVerified candidate.\n\n## Validation\nVerified candidate.")
    assert "- [ ] Human acceptance" in bodies[0]
    assert "Addresses #2." in bodies[0]
    assert (checkout / "source.txt").read_text() == "Initial implementation.\n"


def test_failed_fix_preserves_isolated_changes_and_replies_without_a_pr(tmp_path, checkout, monkeypatch, model):
    responses, _ = model
    responses.extend([
        completion(tool="fs_write", arguments={"path": "source.txt", "content": "Candidate fix.\n"}),
        completion("Changed the implementation; verification will follow."),
    ])
    monkeypatch.setattr(
        github,
        "gh",
        lambda *args, **kwargs: "{}" if args[0][0] == "issue" else "[]" if args[0][0] == "pr" else "test-login",
    )
    replies = []
    monkeypatch.setattr(github, "reply", lambda *args, **kwargs: replies.append(args) or "https://example.test/comment")
    db = tmp_path / "evidence" / "landing.sqlite3"
    action = asyncio.run(
        github.run(
            "example/landing", "fixer", "Fix the bug.", db, checkout, number=2, key="comment:1", checks=["false"]
        )
    )
    assert action.status == "failed"
    assert (checkout / "source.txt").read_text() == "Initial implementation.\n"
    assert (db.parent / "checkout" / "source.txt").read_text() == "Candidate fix.\n"
    assert len(replies) == 1 and "failed" in replies[0][3]


def test_failed_delivery_retry_does_not_repeat_model_or_create_a_new_action(tmp_path, checkout, monkeypatch, model):
    responses, requests = model
    responses.append(completion("The native failure is a missing dependency."))
    monkeypatch.setattr(
        github,
        "gh",
        lambda *args, **kwargs: "{}" if args[0][0] == "issue" else "[]" if args[0][0] == "pr" else "test-login",
    )
    attempts = []

    def deliver(*args, **kwargs):
        attempts.append(args)
        if len(attempts) == 1:
            message = "The delivery response was lost."
            raise RuntimeError(message)
        return "https://example.test/comment"

    monkeypatch.setattr(github, "reply", deliver)
    db = tmp_path / "evidence" / "landing.sqlite3"

    async def invoke():
        return await github.run(
            "example/landing", "explainer", "Explain the failure.", db, checkout, number=2, key="comment:1", checks=[]
        )

    with pytest.raises(RuntimeError, match="response was lost"):
        asyncio.run(invoke())
    action = asyncio.run(invoke())
    assert action.status == "completed"
    assert len(requests) == 1 and len(attempts) == 2
    with closing(Tasks(db)) as tasks:
        assert len(tasks.list()) == 1
        assert [e.type for e in tasks.events(action.id)][-2:] == ["github.delivery_failed", "github.delivered"]


def test_commit_survives_publish_failure_without_a_second_commit(checkout, monkeypatch):
    original = github.git
    pushes = []

    def git(args, workspace):
        if "push" in args:
            pushes.append(args)
            if len(pushes) == 1:
                message = "The push response was lost."
                raise RuntimeError(message)
            return ""
        return original(args, workspace)

    revision = original(["rev-parse", "HEAD"], checkout).strip()
    (checkout / "source.txt").write_text("Verified fix.\n")
    monkeypatch.setattr(github, "git", git)
    monkeypatch.setattr(
        github, "gh", lambda args, *a, **k: "[]" if args[:2] == ["pr", "list"] else "https://example.test/pull/3"
    )
    action = Action(
        id="act_example",
        mode="fixer",
        status="completed",
        instruction="Fix the issue.",
        workspace=str(checkout),
        result="Fixed and verified.",
        created_at="2026-10-02T00:00:00Z",
        updated_at="2026-10-02T00:00:00Z",
    )
    with pytest.raises(RuntimeError, match="response was lost"):
        github.publish_fix("example/landing", 2, checkout, action, revision)
    committed = original(["rev-parse", "HEAD"], checkout).strip()
    assert github.publish_fix("example/landing", 2, checkout, action, revision) == "https://example.test/pull/3"
    assert original(["rev-parse", "HEAD"], checkout).strip() == committed
    assert len(pushes) == 2


def test_repo_view_uses_explicit_repository_without_reconfiguring_auth(monkeypatch):
    from types import SimpleNamespace

    calls = []

    def command(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout='{"name":"landing"}', stderr="")

    monkeypatch.setattr(github.subprocess, "run", command)
    assert json.loads(github.gh(["repo", "view", "--json", "name"], "example/landing"))["name"] == "landing"
    assert calls[0][0] == ["/usr/bin/gh", "repo", "view", "example/landing", "--json", "name"]
    assert calls[0][1]["env"]["GH_REPO"] == "example/landing"


def test_existing_candidate_commit_needs_no_anonymous_fetch(tmp_path, checkout, monkeypatch, model):
    responses, _ = model
    responses.append(completion("Explained the checked revision."))
    revision = github.git(["rev-parse", "HEAD"], checkout).strip()
    original = github.git
    commands = []

    def command(args, workspace):
        commands.append(args)
        return original(args, workspace)

    monkeypatch.setattr(github, "git", command)
    monkeypatch.setattr(github, "gh", lambda *a, **k: "{}" if a[0][0] == "issue" else "test-login")
    monkeypatch.setattr(github, "reply", lambda *a, **k: "https://example.test/comment")
    action = asyncio.run(
        github.run(
            "example/landing",
            "explainer",
            "Explain the candidate.",
            tmp_path / "evidence" / "landing.sqlite3",
            checkout,
            number=2,
            head=revision,
            key="private-pr:1",
            checks=[],
        )
    )
    assert action.status == "completed"
    assert not any("fetch" in args for args in commands)

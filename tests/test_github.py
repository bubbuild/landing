"""Native agent publication and event admission through a prepared gh executable."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from landing.adapters import github
from landing.tasks import ConflictError
from tests.conftest import completion


@pytest.fixture
def platform(tmp_path, monkeypatch):
    for name in tuple(os.environ):
        if name.startswith("GITHUB_") or name == "GH_ADMISSION_TOKEN":
            monkeypatch.delenv(name)
    database = tmp_path / "github.json"
    database.write_text(json.dumps({"reviews": [], "comments": [], "permission": "write"}))
    binary = tmp_path / "bin"
    binary.mkdir()
    executable = binary / "gh"
    executable.write_text("""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
path = Path(os.environ["TEST_GITHUB_STATE"])
state = json.loads(path.read_text())
args = sys.argv[1:]
endpoint = args[1]
if state.get("unavailable"):
    raise SystemExit("GitHub is unavailable")
if args[0] != "api":
    raise SystemExit("Use the native API in this fixture.")
if "--input" in args:
    source = args[args.index("--input") + 1]
    body = json.loads(sys.stdin.read() if source == "-" else Path(source).read_text())
    kind = "reviews" if endpoint.endswith("/reviews") else "comments"
    record = {**body, "id": len(state[kind]) + 1, "html_url": "https://example.test/" + kind + "/1", "state": "COMMENTED"}
    if endpoint.endswith("/replies"):
        record["in_reply_to_id"] = int(endpoint.split("/")[-2])
        record["pull_request_url"] = "https://api.github.com/repos/example/landing/pulls/42"
    elif kind == "comments":
        record["issue_url"] = "https://api.github.com/repos/example/landing/issues/42"
    state[kind].append(record)
    path.write_text(json.dumps(state))
    print(json.dumps(record))
elif endpoint.endswith("/permission"):
    print(json.dumps({"permission": state["permission"]}))
elif "/memberships/" in endpoint:
    if state["membership"] is None:
        raise SystemExit("gh: Not Found (HTTP 404); membership is not visible")
    print(json.dumps(state["membership"]))
elif endpoint.startswith("users/"):
    print(json.dumps(state["user"]))
elif endpoint == "repos/example/landing":
    print(json.dumps({"owner": state["owner"]}))
elif "/reviews/" in endpoint:
    number = int(endpoint.rsplit("/", 1)[-1])
    print(json.dumps(next(record for record in state["reviews"] if record["id"] == number)))
elif "/comments/" in endpoint:
    number = int(endpoint.rsplit("/", 1)[-1])
    record = next(record for record in state["comments"] if record["id"] == number)
    if "/pulls/comments/" in endpoint and "issue_url" in record:
        raise SystemExit("gh: Not Found (HTTP 404)")
    print(json.dumps(record))
elif endpoint.endswith("/reviews") or endpoint.endswith("/comments"):
    kind = endpoint.rsplit("/", 1)[-1]
    for record in state[kind]:
        print(json.dumps(record))
else:
    print(json.dumps({**state.get("target", {}), "head": {"sha": state.get("head", "candidate-head")}}))
""")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("TEST_GITHUB_STATE", str(database))
    monkeypatch.setenv("BASH_ENV", "/dev/null")
    for key in os.environ:
        if key.startswith("BASH_FUNC_"):
            monkeypatch.delenv(key)
    return database


@pytest.fixture
def invoke(tmp_path, platform, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def call(event, *, key="delegation", repository="example/landing", prefix="/landing", number=0, trust="repository"):
        source = tmp_path / "event.json"
        source.write_text(json.dumps(event))
        return github.main([
            "--repository",
            repository,
            "--event",
            str(source),
            "--delivery-key",
            key,
            "--db",
            str(tmp_path / "landing.sqlite3"),
            "--command-prefix",
            prefix,
            "--number",
            str(number),
            "--trust",
            trust,
            "--upstream-workflow",
            "release-main",
        ])

    return call


def test_comment_actions_require_maintainer_and_use_configurable_identity(platform, invoke, model):
    event = {
        "repository": {"full_name": "example/landing"},
        "issue": {"number": 42},
        "comment": {"body": "/landing fix Repair retry behavior.", "user": {"type": "Bot", "login": "bot"}},
    }
    event["comment"]["body"] = "Handled the task."
    assert invoke(event) is None
    event["comment"]["body"] = "/landing fix Repair retry behavior."
    event["comment"]["user"] = {"type": "User", "login": "contributor"}
    state = json.loads(platform.read_text())
    state["permission"] = "read"
    platform.write_text(json.dumps(state))
    assert invoke(event) is None
    state["permission"] = "write"
    platform.write_text(json.dumps(state))
    responses, _ = model
    for key, mode in (("repair", "fixer"), ("review", "gatekeeper")):
        responses.extend([
            completion(
                tool="fs_write",
                arguments={
                    "path": "comment.json",
                    "content": json.dumps({"body": github.marker(mode, key) + "\nHandled the task."}),
                },
            ),
            completion(
                tool="bash",
                arguments={"command": "gh api repos/example/landing/issues/42/comments --input comment.json"},
            ),
            completion("Published the result."),
        ])
    event["comment"]["body"] += "\nPreserve the public CLI behavior."
    action = invoke(event, key="repair")
    assert action is not None
    assert action.status == "completed"
    assert action.mode == "fixer"
    assert action.instruction == "Repair retry behavior.\nPreserve the public CLI behavior."
    event["comment"]["body"] = "@team-bot review Inspect retry behavior."
    action = invoke(event, key="review", prefix="@team-bot")
    assert action is not None
    assert action.status == "completed"
    assert action.mode == "gatekeeper"
    with pytest.raises(ValueError, match="another repository"):
        invoke(event, repository="other/repository")
    event["comment"]["body"] = "/landing execute arbitrary-shell"
    with pytest.raises(ValueError, match="Choose"):
        invoke(event)


def test_agent_publishes_native_review_with_inline_comment_and_deduplicates(tmp_path, platform, model):
    responses, requests = model
    stamp = github.marker("gatekeeper", "review:42")
    review = {
        "commit_id": "candidate-head",
        "event": "COMMENT",
        "body": stamp + "\nOne retry defect.",
        "comments": [
            {"path": "candidate.py", "line": 2, "side": "RIGHT", "body": "This condition permits an extra retry."}
        ],
    }
    responses.extend([
        completion(tool="fs_write", arguments={"path": "review.json", "content": json.dumps(review)}),
        completion(
            tool="bash", arguments={"command": "gh api repos/example/landing/pulls/42/reviews --input review.json"}
        ),
        completion(tool="decide", arguments={"decision": "block"}),
        completion("Published the retry finding on the candidate."),
    ])

    async def run(instruction="Review retry behavior."):
        return await github.run(
            "example/landing",
            "gatekeeper",
            instruction,
            tmp_path / "landing.sqlite3",
            tmp_path,
            number=42,
            head="candidate-head",
            key="review:42",
            checks=["true"],
        )

    action = asyncio.run(run())
    assert action.status == "completed"
    assert action.decision == "block"
    published = json.loads(platform.read_text())["reviews"]
    assert len(published) == 1
    assert published[0]["commit_id"] == "candidate-head"
    assert published[0]["comments"][0]["path"] == "candidate.py"
    assert published[0]["comments"][0]["line"] == 2
    state = json.loads(platform.read_text())
    state["reviews"].append({"id": 9, "body": "A later independent review.", "state": "COMMENTED"})
    platform.write_text(json.dumps(state))
    calls = len(requests)
    assert asyncio.run(run()).id == action.id
    assert len(requests) == calls
    assert json.loads(platform.read_text())["reviews"] == state["reviews"]
    with pytest.raises(ConflictError, match="different request"):
        asyncio.run(run("Review deployment behavior."))


@pytest.mark.parametrize("is_pr", [False, True])
def test_explainer_reads_back_its_conversation_reply(platform, invoke, model, is_pr):
    event = {
        "repository": {"full_name": "example/landing"},
        "issue": {"number": 42, **({"pull_request": {}} if is_pr else {})},
        "comment": {
            "body": "/landing explain Why is HTTPS unavailable?",
            "user": {"type": "User", "login": "maintainer"},
        },
    }
    answer = "The domain certificate is still being issued."
    responses, _ = model

    async def report_confirmed_body(**kwargs):
        return completion(answer if answer in str(kwargs["messages"][-1]) else "Reply confirmation failed.")

    responses.extend([
        completion(
            tool="fs_write",
            arguments={
                "path": "reply.json",
                "content": json.dumps({"body": github.marker("explainer", "explain") + "\n" + answer}),
            },
        ),
        completion(
            tool="bash", arguments={"command": "gh api repos/example/landing/issues/42/comments --input reply.json"}
        ),
        completion(tool="confirm_reply", arguments={"comment_id": 1}),
        report_confirmed_body,
    ])
    action = invoke(event, key="explain")
    assert action is not None
    assert action.status == "completed"
    assert action.result == answer
    assert json.loads(platform.read_text())["comments"][0]["body"].endswith(answer)


@pytest.mark.parametrize("marked", [True, False])
def test_delegated_inline_reply_is_confirmed_and_replay_does_not_publish_twice(platform, invoke, model, marked):
    state = json.loads(platform.read_text())
    state["reviews"] = [
        {"id": 9, "body": github.marker("gatekeeper", "earlier") + "\nA retry finding."},
        {"id": 10, "body": "", "user": {"login": "maintainer"}},
    ]
    state["target"] = {"title": "Bound retries", "body": "Keep the initial request outside the retry loop."}
    state["comments"] = [
        {
            "id": 17,
            "body": "The loop controls only retry requests.",
            "path": "retry.py",
            "line": 7,
            "pull_request_review_id": 9,
        },
        {"id": 20, "in_reply_to_id": 19, "body": "Unrelated deployment discussion."},
    ]
    platform.write_text(json.dumps(state))
    event = {
        "repository": {"full_name": "example/landing"},
        "pull_request": {"number": 42},
        "comment": {
            "id": 18,
            "in_reply_to_id": 17,
            "pull_request_review_id": 10,
            "body": "/landing review Does this affect the initial attempt?",
            "path": "retry.py",
            "line": 7,
            "user": {"type": "User", "login": "maintainer"},
        },
    }
    stamp = github.marker("gatekeeper", "comment:18")
    responses, _ = model

    async def reply_from_evidence(**kwargs):
        evidence = str(kwargs["messages"])
        informed = (
            all(
                text in evidence
                for text in (
                    "Keep the initial request outside the retry loop.",
                    "The loop controls only retry requests.",
                    "retry.py",
                )
            )
            and "Unrelated deployment discussion." not in evidence
        )
        answer = "The initial attempt is unaffected." if informed else "The thread evidence is unavailable."
        return completion(
            tool="fs_write",
            arguments={
                "path": "reply.json",
                "content": json.dumps({"body": (stamp + "\n" if marked else "") + answer}),
            },
        )

    responses.extend([
        completion(tool="bash", arguments={"command": "gh api repos/example/landing/issues/42"}),
        completion(tool="bash", arguments={"command": "gh api repos/example/landing/pulls/comments/17"}),
        reply_from_evidence,
        completion(
            tool="bash",
            arguments={"command": "gh api repos/example/landing/pulls/42/comments/17/replies --input reply.json"},
        ),
        completion("Replied in the original review thread."),
    ])
    if not marked:
        responses.insert(4, completion(tool="confirm_reply", arguments={"comment_id": 3}))
    action = invoke(event, key="comment:18")
    assert action is not None
    assert action.mode == "gatekeeper"
    assert action.status == "completed"
    state = json.loads(platform.read_text())
    assert len(state["reviews"]) == 2
    assert state["comments"][-1]["in_reply_to_id"] == 17
    assert "initial attempt is unaffected" in state["comments"][-1]["body"]
    assert invoke(event, key="comment:18").id == action.id
    assert json.loads(platform.read_text())["comments"] == state["comments"]
    event["comment"]["body"] = "Fixed in the latest commit; CI is pending."
    assert invoke(event, key="status-update") is None


def test_text_without_required_publication_is_failed_work(tmp_path, platform, model):
    responses, _ = model
    responses.append(completion("The review is ready."))
    action = asyncio.run(
        github.run(
            "example/landing",
            "gatekeeper",
            "Review the candidate.",
            tmp_path / "landing.sqlite3",
            tmp_path,
            number=42,
            head="candidate-head",
            key="missing-review",
            checks=[],
        )
    )
    assert action.status == "failed"
    assert action.result == "The review is ready."
    assert action.error is not None
    assert "confirmed publication" in action.error["message"]
    assert not json.loads(platform.read_text())["reviews"]


@pytest.mark.parametrize("lookup_fails", [False, True])
def test_review_stops_queued_tools_for_superseded_or_unverifiable_head(tmp_path, platform, model, lookup_fails):
    responses, requests = model
    state = json.loads(platform.read_text())
    state["head"] = "new-candidate"
    platform.write_text(json.dumps(state))
    review = {
        "commit_id": "candidate-head",
        "event": "COMMENT",
        "body": github.marker("gatekeeper", "superseded") + "\nReviewed the original candidate.",
    }

    async def replace_candidate(**kwargs):
        state["unavailable"] = lookup_fails
        state["head"] = "new-candidate"
        platform.write_text(json.dumps(state))
        queued = completion(tool="fs_write", arguments={"path": "review.json", "content": json.dumps(review)})
        publish = completion(tool="bash", arguments={"command": "touch publication-started"})
        publish.choices[0].message.tool_calls[0].id = "call-publication"
        queued.choices[0].message.tool_calls.extend(publish.choices[0].message.tool_calls)
        return queued

    responses.extend([
        replace_candidate,
        completion(
            tool="bash", arguments={"command": "gh api repos/example/landing/pulls/42/reviews --input review.json"}
        ),
        completion(tool="decide", arguments={"decision": "allow"}),
        completion("Published the original candidate review."),
    ])

    async def run():
        return await github.run(
            "example/landing",
            "gatekeeper",
            "Review the candidate.",
            tmp_path / "landing.sqlite3",
            tmp_path,
            number=42,
            head="candidate-head",
            key="superseded",
            checks=[],
        )

    with pytest.raises(ValueError, match="PR head changed"):
        asyncio.run(run())
    assert not requests
    assert not json.loads(platform.read_text())["reviews"]
    state["head"] = "candidate-head"
    platform.write_text(json.dumps(state))
    action = asyncio.run(run())
    assert action.status == "cancelled"
    assert not json.loads(platform.read_text())["reviews"]
    assert not (tmp_path / "review.json").exists()
    assert not (tmp_path / "publication-started").exists()


def test_release_tag_event_delegates_maintenance(invoke, model):
    responses, _ = model
    responses.append(completion("Documentation deployment needs its repository Pages configuration."))
    action = invoke({
        "repository": {"full_name": "example/landing", "default_branch": "main"},
        "workflow_run": {
            "id": 123,
            "name": "release-main",
            "head_repository": {"full_name": "example/landing"},
            "actor": {"login": "maintainer"},
            "head_branch": "0.0.0",
            "event": "release",
        },
    })
    assert action is not None
    assert action.status == "completed"
    assert action.mode == "issuer"
    assert action.result == "Documentation deployment needs its repository Pages configuration."


def test_unchanged_followup_completes_with_later_reads_and_remains_idempotent(tmp_path, platform, invoke, model):
    responses, requests = model
    (tmp_path / "evidence.txt").write_text("The original response is missing.")
    response = completion(
        tool="no_update", arguments={"reason": "The original response is still missing; no new evidence."}
    )
    tool_calls = response.choices[0].message.tool_calls
    assert tool_calls is not None
    read_calls = completion(tool="fs_read", arguments={"path": "evidence.txt"}).choices[0].message.tool_calls or []
    read_calls[0].id = "call-read"
    tool_calls.extend(read_calls)
    responses.extend([
        response,
        completion("No new evidence; the issue remains open."),
    ])
    event = {
        "repository": {"full_name": "example/landing", "default_branch": "main"},
        "workflow_run": {
            "id": 123,
            "name": "release-main",
            "head_repository": {"full_name": "example/landing"},
            "actor": {"login": "maintainer"},
            "head_branch": "main",
            "event": "push",
        },
    }
    action = invoke(event, key="followup:123", number=42)
    assert action is not None
    assert action.status == "completed"
    assert not json.loads(platform.read_text())["comments"]
    calls = len(requests)
    replay = invoke(event, key="followup:123", number=42)
    assert replay is not None and replay.id == action.id
    assert replay.status == "completed"
    assert len(requests) == calls
    assert not json.loads(platform.read_text())["comments"]


def test_explicit_triage_still_requires_the_requested_reply(platform, invoke, model):
    responses, _ = model
    responses.extend([
        completion(tool="no_update", arguments={"reason": "No useful change."}),
        completion("The issue is unchanged."),
    ])
    action = invoke({
        "repository": {"full_name": "example/landing"},
        "issue": {"number": 42},
        "comment": {
            "body": "/landing triage Check this issue and explain what is still missing.",
            "user": {"type": "User", "login": "maintainer"},
        },
    })
    assert action is not None
    assert action.status == "failed"
    assert not json.loads(platform.read_text())["comments"]


def test_changed_followup_publishes_once(platform, invoke, model):
    responses, _ = model
    stamp = github.marker("issuer", "followup:124")
    responses.extend([
        completion(
            tool="fs_write",
            arguments={
                "path": "update.json",
                "content": json.dumps({"body": stamp + "\nThe new failure includes the missing diagnostic."}),
            },
        ),
        completion(
            tool="bash", arguments={"command": "gh api repos/example/landing/issues/42/comments --input update.json"}
        ),
        completion("Published the new diagnostic evidence."),
    ])
    action = invoke(
        {
            "repository": {"full_name": "example/landing", "default_branch": "main"},
            "workflow_run": {
                "id": 124,
                "name": "release-main",
                "head_repository": {"full_name": "example/landing"},
                "actor": {"login": "maintainer"},
                "head_branch": "main",
                "event": "push",
            },
        },
        key="followup:124",
        number=42,
    )
    assert action is not None
    assert action.status == "completed"
    comments = json.loads(platform.read_text())["comments"]
    assert len(comments) == 1
    assert "missing diagnostic" in comments[0]["body"]


@pytest.mark.parametrize("declare_unchanged", [False, True])
def test_failed_automatic_publication_is_not_a_quiet_completion(tmp_path, platform, invoke, model, declare_unchanged):
    responses, _ = model
    response = completion(
        tool="bash",
        arguments={
            "command": "touch publication-attempted && gh api repos/example/landing/issues/42/comments --input missing.json"
        },
    )
    if declare_unchanged:
        calls = response.choices[0].message.tool_calls
        assert calls is not None
        unchanged = completion(tool="no_update", arguments={"reason": "No useful change."})
        unchanged_calls = unchanged.choices[0].message.tool_calls or []
        unchanged_calls[0].id = "call-unchanged"
        calls.extend(unchanged_calls)
    responses.extend([
        response,
        completion("Published an update."),
    ])
    action = invoke(
        {
            "repository": {"full_name": "example/landing", "default_branch": "main"},
            "workflow_run": {
                "id": 125,
                "name": "release-main",
                "head_repository": {"full_name": "example/landing"},
                "actor": {"login": "maintainer"},
                "head_branch": "main",
                "event": "push",
            },
        },
        number=42,
    )
    assert action is not None
    assert action.status == "failed"
    assert (tmp_path / "publication-attempted").exists()
    assert not json.loads(platform.read_text())["comments"]


@pytest.mark.parametrize(
    ("trust", "owner", "actor_id", "membership", "allowed"),
    [
        ("repository", {"type": "User", "id": 1}, 2, None, True),
        ("owner", {"type": "User", "id": 1}, 2, None, False),
        ("owner", {"type": "User", "id": 1}, 1, None, True),
        ("owner", {"type": "Organization", "login": "example"}, 2, None, False),
        ("owner", {"type": "Organization", "login": "example"}, 2, {"state": "active", "role": "member"}, False),
        ("owner", {"type": "Organization", "login": "example"}, 2, {"state": "active", "role": "admin"}, True),
    ],
)
def test_native_admission_runs_before_runtime_installation(
    tmp_path, platform, trust, owner, actor_id, membership, allowed
):
    state = json.loads(platform.read_text())
    state["owner"] = owner
    if owner["type"] == "Organization":
        state["permission"] = "admin" if membership is not None else "read"
    state["membership"] = membership
    platform.write_text(json.dumps(state))
    source = tmp_path / "event.json"
    source.write_text(
        json.dumps({
            "repository": {"full_name": "example/landing", "owner": owner},
            "issue": {"number": 42},
            "comment": {
                "body": "/landing explain Explain this failure.",
                "user": {"id": actor_id, "login": "maintainer"},
            },
        })
    )
    script = Path(github.__file__).with_name("admission.py")
    # -S makes installed runtime packages unavailable to this admission process.
    result = subprocess.run(  # noqa: S603 -- fixed admission entry point and disposable platform.
        [
            sys.executable,
            "-S",
            str(script),
            "--repository",
            "example/landing",
            "--event",
            str(source),
            "--trust",
            trust,
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"allowed={str(allowed).lower()}"


def test_unknown_permissions_fail_instead_of_skipping_or_invoking_agent(platform, invoke, model):
    state = json.loads(platform.read_text())
    state["unavailable"] = True
    platform.write_text(json.dumps(state))
    event = {
        "repository": {"full_name": "example/landing"},
        "issue": {"number": 42},
        "comment": {"body": "/landing fix Repair this issue.", "user": {"login": "maintainer"}},
    }
    with pytest.raises(RuntimeError, match="GitHub is unavailable"):
        invoke(event)
    assert not model[1]


def test_owner_rerun_does_not_borrow_original_owners_authority(platform, invoke, model, monkeypatch):
    state = json.loads(platform.read_text())
    state["owner"] = {"type": "User", "id": 1}
    state["user"] = {"id": 2, "login": "writer"}
    platform.write_text(json.dumps(state))
    monkeypatch.setenv("GITHUB_ACTOR", "owner")
    monkeypatch.setenv("GITHUB_TRIGGERING_ACTOR", "writer")
    event = {
        "repository": {"full_name": "example/landing", "owner": {"type": "User", "id": 1}},
        "issue": {"number": 42},
        "comment": {"body": "/landing fix Repair this issue.", "user": {"id": 1, "login": "owner"}},
    }
    assert invoke(event, trust="owner") is None
    assert not model[1]


@pytest.mark.parametrize(
    ("name", "source", "event_name", "branch"),
    [
        ("other-workflow", "example/landing", "release", "v1.0"),
        ("release-main", "outside/landing", "push", "main"),
        ("release-main", "example/landing", "pull_request", "main"),
        ("release-main", "example/landing", "push", "candidate"),
    ],
)
def test_untrusted_upstream_does_not_delegate_even_when_successful(invoke, model, name, source, event_name, branch):
    event = {
        "repository": {"full_name": "example/landing", "default_branch": "main"},
        "workflow_run": {
            "id": 123,
            "name": name,
            "head_repository": {"full_name": source},
            "event": event_name,
            "head_branch": branch,
            "conclusion": "success",
            "actor": {"login": "maintainer"},
        },
    }
    assert invoke(event) is None
    assert not model[1]


def test_owner_admission_uses_current_repository_ownership(platform, invoke, model):
    state = json.loads(platform.read_text())
    state["owner"] = {"type": "User", "id": 2}
    platform.write_text(json.dumps(state))
    event = {
        "repository": {"full_name": "example/landing", "owner": {"type": "User", "id": 1}},
        "issue": {"number": 42},
        "comment": {"body": "/landing fix Repair this issue.", "user": {"id": 1, "login": "former-owner"}},
    }
    assert invoke(event, trust="owner") is None
    assert not model[1]


def test_invisible_owner_membership_does_not_hide_admission_failure(platform, invoke, model):
    state = json.loads(platform.read_text())
    state.update(owner={"type": "Organization", "login": "example"}, permission="admin", membership=None)
    platform.write_text(json.dumps(state))
    event = {
        "repository": {"full_name": "example/landing"},
        "issue": {"number": 42},
        "comment": {"body": "/landing fix Repair this issue.", "user": {"login": "maintainer"}},
    }
    with pytest.raises(RuntimeError, match="membership is not visible"):
        invoke(event, trust="owner")
    assert not model[1]

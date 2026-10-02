"""Native agent publication and event admission through a prepared gh executable."""

import asyncio
import json
import os

import pytest

from landing.adapters import github
from landing.tasks import ConflictError
from tests.conftest import completion


@pytest.fixture
def platform(tmp_path, monkeypatch):
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
if args[0] != "api":
    raise SystemExit("Use the native API in this fixture.")
if "--input" in args:
    source = args[args.index("--input") + 1]
    body = json.loads(sys.stdin.read() if source == "-" else Path(source).read_text())
    kind = "reviews" if endpoint.endswith("/reviews") else "comments"
    record = {**body, "id": len(state[kind]) + 1, "html_url": "https://example.test/" + kind + "/1", "state": "COMMENTED"}
    if endpoint.endswith("/replies"):
        record["in_reply_to_id"] = int(endpoint.split("/")[-2])
    state[kind].append(record)
    path.write_text(json.dumps(state))
    print(json.dumps(record))
elif endpoint.endswith("/permission"):
    print(json.dumps({"permission": state["permission"]}))
elif "/reviews/" in endpoint:
    number = int(endpoint.rsplit("/", 1)[-1])
    print(json.dumps(next(record for record in state["reviews"] if record["id"] == number)))
elif "/comments/" in endpoint:
    number = int(endpoint.rsplit("/", 1)[-1])
    print(json.dumps(next(record for record in state["comments"] if record["id"] == number)))
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

    def call(event, *, key="delegation", repository="example/landing", prefix="/landing", number=0):
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
        ])

    return call


def test_comment_actions_require_maintainer_and_use_configurable_identity(platform, invoke, model):
    event = {
        "repository": {"full_name": "example/landing"},
        "issue": {"number": 42},
        "comment": {"body": "/landing fix Repair retry behavior.", "user": {"type": "Bot", "login": "bot"}},
    }
    assert invoke(event) is None
    event["comment"]["user"] = {"type": "User", "login": "contributor"}
    state = json.loads(platform.read_text())
    state["permission"] = "read"
    platform.write_text(json.dumps(state))
    with pytest.raises(ValueError, match="maintainer"):
        invoke(event)
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


def test_owned_inline_followup_retains_mode_and_replies_to_original_thread(platform, invoke, model):
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
            "body": "Does this affect the initial attempt?",
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
            tool="fs_write", arguments={"path": "reply.json", "content": json.dumps({"body": stamp + "\n" + answer})}
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
    action = invoke(event, key="comment:18")
    assert action is not None
    assert action.mode == "gatekeeper"
    assert action.status == "completed"
    state = json.loads(platform.read_text())
    assert len(state["reviews"]) == 2
    assert state["comments"][-1]["in_reply_to_id"] == 17
    assert "initial attempt is unaffected" in state["comments"][-1]["body"]


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


def test_review_of_superseded_candidate_does_not_report_success(tmp_path, platform, model):
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
        state["head"] = "new-candidate"
        platform.write_text(json.dumps(state))
        return completion(tool="fs_write", arguments={"path": "review.json", "content": json.dumps(review)})

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
    assert action.status == "failed"
    assert action.error is not None
    assert "PR head changed" in action.error["message"]


def test_release_tag_event_delegates_maintenance(invoke, model):
    responses, _ = model
    responses.append(completion("Documentation deployment needs its repository Pages configuration."))
    action = invoke({
        "repository": {"full_name": "example/landing", "default_branch": "main"},
        "workflow_run": {"id": 123, "head_branch": "0.0.0", "event": "release"},
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
        "workflow_run": {"id": 123, "head_branch": "main", "event": "push"},
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
            "workflow_run": {"id": 124, "head_branch": "main", "event": "push"},
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
            "workflow_run": {"id": 125, "head_branch": "main", "event": "push"},
        },
        number=42,
    )
    assert action is not None
    assert action.status == "failed"
    assert (tmp_path / "publication-attempted").exists()
    assert not json.loads(platform.read_text())["comments"]

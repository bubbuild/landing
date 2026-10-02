"""Native agent publication and event admission through a prepared gh executable."""

import asyncio
import json
import os

import pytest

from landing.adapters import github
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
    print(json.dumps(state["reviews"][0]))
elif endpoint.endswith("/reviews") or endpoint.endswith("/comments"):
    kind = endpoint.rsplit("/", 1)[-1]
    for record in state[kind]:
        print(json.dumps(record))
else:
    print(json.dumps({"head": {"sha": state.get("head", "candidate-head")}}))
""")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("TEST_GITHUB_STATE", str(database))
    monkeypatch.setenv("BASH_ENV", "/dev/null")
    for key in os.environ:
        if key.startswith("BASH_FUNC_"):
            monkeypatch.delenv(key)
    return database


def test_comment_actions_require_maintainer_and_use_configurable_identity(platform):
    event = {
        "repository": {"full_name": "example/landing"},
        "issue": {"number": 42},
        "comment": {"body": "/landing fix Repair retry behavior.", "user": {"type": "Bot", "login": "bot"}},
    }
    assert github.delegation(event, "example/landing") is None
    event["comment"]["user"] = {"type": "User", "login": "contributor"}
    state = json.loads(platform.read_text())
    state["permission"] = "read"
    platform.write_text(json.dumps(state))
    with pytest.raises(ValueError, match="maintainer"):
        github.delegation(event, "example/landing")
    state["permission"] = "write"
    platform.write_text(json.dumps(state))
    assert github.delegation(event, "example/landing") == ("fix", "Repair retry behavior.", 42)
    event["comment"]["body"] += "\nPreserve the public CLI behavior."
    assert github.delegation(event, "example/landing") == (
        "fix",
        "Repair retry behavior.\nPreserve the public CLI behavior.",
        42,
    )
    event["comment"]["body"] = "@team-bot review Inspect retry behavior."
    assert github.delegation(event, "example/landing", prefix="@team-bot") == ("review", "Inspect retry behavior.", 42)
    with pytest.raises(ValueError, match="another repository"):
        github.delegation(event, "other/repository")
    event["comment"]["body"] = "/landing execute arbitrary-shell"
    with pytest.raises(ValueError, match="Choose"):
        github.delegation(event, "example/landing")


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

    async def run():
        return await github.run(
            "example/landing",
            "gatekeeper",
            "Review retry behavior.",
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
    calls = len(requests)
    assert asyncio.run(run()).id == action.id
    assert len(requests) == calls
    assert len(json.loads(platform.read_text())["reviews"]) == 1


def test_owned_inline_followup_retains_mode_and_replies_to_original_thread(tmp_path, platform, model):
    state = json.loads(platform.read_text())
    state["reviews"] = [{"id": 9, "body": github.marker("gatekeeper", "earlier") + "\nA retry finding."}]
    platform.write_text(json.dumps(state))
    event = {
        "repository": {"full_name": "example/landing"},
        "pull_request": {"number": 42},
        "comment": {
            "id": 18,
            "in_reply_to_id": 17,
            "pull_request_review_id": 9,
            "body": "Does this affect the initial attempt?",
            "user": {"type": "User", "login": "maintainer"},
        },
    }
    assert github.delegation(event, "example/landing") == ("review", "Does this affect the initial attempt?", 42)
    stamp = github.marker("gatekeeper", "comment:18")
    responses, _ = model
    responses.extend([
        completion(
            tool="fs_write",
            arguments={
                "path": "reply.json",
                "content": json.dumps({"body": stamp + "\nThe initial attempt is unaffected."}),
            },
        ),
        completion(
            tool="bash",
            arguments={"command": "gh api repos/example/landing/pulls/42/comments/17/replies --input reply.json"},
        ),
        completion("Replied in the original review thread."),
    ])
    action = asyncio.run(
        github.run(
            "example/landing",
            "gatekeeper",
            "Does this affect the initial attempt?",
            tmp_path / "landing.sqlite3",
            tmp_path,
            number=42,
            head="candidate-head",
            key="comment:18",
            checks=[],
            event=event,
        )
    )
    assert action.status == "completed"
    state = json.loads(platform.read_text())
    assert len(state["reviews"]) == 1
    assert state["comments"][0]["in_reply_to_id"] == 17
    assert "initial attempt is unaffected" in state["comments"][0]["body"]


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
    responses, _ = model
    state = json.loads(platform.read_text())
    state["head"] = "new-candidate"
    platform.write_text(json.dumps(state))
    review = {
        "commit_id": "candidate-head",
        "event": "COMMENT",
        "body": github.marker("gatekeeper", "superseded") + "\nReviewed the original candidate.",
    }
    responses.extend([
        completion(tool="fs_write", arguments={"path": "review.json", "content": json.dumps(review)}),
        completion(
            tool="bash", arguments={"command": "gh api repos/example/landing/pulls/42/reviews --input review.json"}
        ),
        completion(tool="decide", arguments={"decision": "allow"}),
        completion("Published the original candidate review."),
    ])
    action = asyncio.run(
        github.run(
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
    )
    assert action.status == "failed"
    assert action.error is not None
    assert "PR head changed" in action.error["message"]

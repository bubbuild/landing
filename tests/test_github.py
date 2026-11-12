"""Native agent publication and event admission through a prepared gh executable."""

import asyncio
import contextlib
import hashlib
import hmac
import json
import os
from typing import cast

import pytest
from typer.testing import CliRunner

from landing.adapters.github import execution
from landing.adapters.github import publication as github
from landing.adapters.github.events import route_event
from landing.adapters.github.settings import GitHubSettings
from landing.cli import create_cli_app
from landing.models import Action
from landing.runtime import Runtime
from tests.conftest import completion


@pytest.fixture
def platform(tmp_path, monkeypatch):
    for name in tuple(os.environ):
        if name.startswith("GITHUB_") or name == "GH_ADMISSION_TOKEN":
            monkeypatch.delenv(name)
    database = tmp_path / "github.json"
    database.write_text(json.dumps({"reviews": [], "comments": [], "permission": "write", "publisher": {"id": 314}}))
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
if args[0] == "repo" and args[1] == "view":
    print(state.get("workspace_repository", "example/landing"))
    raise SystemExit(0)
if state.get("unavailable"):
    raise SystemExit("GitHub is unavailable")
if args[0] != "api":
    raise SystemExit("Use the native API in this fixture.")
reactions = state.setdefault("reactions", {})
if "/reactions" in endpoint:
    subject, _, reaction = endpoint.partition("/reactions")
    if "DELETE" in args:
        reactions[subject] = [item for item in reactions[subject] if item["id"] != int(reaction.strip("/"))]
    elif "-f" in args:
        content = args[args.index("-f") + 1].split("=", 1)[1]
        state["reaction_id"] = state.get("reaction_id", 0) + 1
        record = {"id": state["reaction_id"], "content": content, "user": state["publisher"]}
        reactions.setdefault(subject, []).append(record)
        print(json.dumps(record))
    else:
        print(json.dumps([reactions.get(subject, [])]))
    path.write_text(json.dumps(state))
elif "-f" in args and args[args.index("-f") + 1].startswith("body="):
    if state.get("status_write_fails"):
        raise SystemExit("Status publication denied")
    body = args[args.index("-f") + 1].split("=", 1)[1]
    if "PATCH" in args:
        number = int(endpoint.rsplit("/", 1)[-1])
        record = next(item for item in state["comments"] if item["id"] == number)
        record["body"] = body
    else:
        record = {
            "id": len(state["comments"]) + 1, "body": body, "user": state["publisher"],
            "html_url": "https://example.test/comments/status",
            "issue_url": "https://api.github.com/repos/example/landing/issues/42",
        }
        state["comments"].append(record)
    state["status_writes"] = state.get("status_writes", 0) + 1
    if state.get("status_readback_mismatch"):
        record["body"] = "Changed after publication"
    if state.get("status_head_changes"):
        state["head"] = "next-candidate"
    path.write_text(json.dumps(state))
    print(json.dumps(record))
elif endpoint.endswith("/dispatches"):
    state.setdefault("dispatches", []).append(json.load(sys.stdin))
    path.write_text(json.dumps(state))
    print(json.dumps({"workflow_run_id": 123, "html_url": "https://github.com/example/landing/actions/runs/123"}))
elif "--input" in args:
    source = args[args.index("--input") + 1]
    body = json.loads(sys.stdin.read() if source == "-" else Path(source).read_text())
    kind = "reviews" if endpoint.endswith("/reviews") else "comments"
    record = {**body, "id": len(state[kind]) + 1, "html_url": "https://example.test/" + kind + "/1", "state": "COMMENTED", "user": state["publisher"]}
    if endpoint.endswith("/replies"):
        record["in_reply_to_id"] = int(endpoint.split("/")[-2])
        record["pull_request_url"] = "https://api.github.com/repos/example/landing/pulls/42"
    elif kind == "comments":
        record["issue_url"] = "https://api.github.com/repos/example/landing/issues/42"
    state[kind].append(record)
    path.write_text(json.dumps(state))
    print(json.dumps(record))
elif endpoint == "graphql":
    print(json.dumps(state["publisher"]["id"]))
elif "/dependabot/alerts/" in endpoint:
    print(json.dumps(state["alert"]))
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
    print(json.dumps([state[kind]] if "--slurp" in args else state[kind]))
elif "/commits/" in endpoint and endpoint.endswith("/pulls"):
    print(json.dumps([state.get("associated_pulls", [])]))
else:
    target = state.get("target", {})
    print(json.dumps({**target, "head": {**target.get("head", {}), "sha": state.get("head", "candidate-head")}}))
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
    for name in tuple(os.environ):
        if name.startswith("INPUT_"):
            monkeypatch.delenv(name)

    def call(
        event,
        *,
        key="delegation",
        repository="example/landing",
        prefix="/landing",
        number=0,
        trust="repository",
        error=None,
        database=None,
    ):
        source = tmp_path / "event.json"
        source.write_text(json.dumps(event))
        for name, value in {
            "GITHUB_EVENT_PATH": str(source),
            "GITHUB_RUN_ID": "acceptance",
            "LANDING_GITHUB_REPOSITORY": repository,
            "LANDING_GITHUB_DELIVERY_KEY": key or "",
            "LANDING_DB": str(database or tmp_path / "landing.sqlite3"),
            "LANDING_GITHUB_COMMAND_PREFIX": prefix,
            "LANDING_GITHUB_NUMBER": str(number),
            "LANDING_GITHUB_TRUST": trust,
            "LANDING_GITHUB_UPSTREAM_WORKFLOW": "release-main",
        }.items():
            monkeypatch.setenv(name, value)
        result = CliRunner().invoke(create_cli_app(), ["github", "event"])
        if error:
            assert result.exit_code != 0
            assert error in result.stderr
            assert not result.stdout
            return None
        if not result.stdout:
            assert result.exit_code == 0, result.stderr
            return None
        action = Action.model_validate_json(result.stdout)
        assert result.exit_code == int(action.status != "completed"), result.stderr
        return action

    return call


@pytest.fixture
def alert_event(platform, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "dependabot_alert")
    state = json.loads(platform.read_text())
    state["alert"] = {
        "number": 7,
        "state": "open",
    }
    platform.write_text(json.dumps(state))
    return {
        "repository": {"full_name": "example/landing", "default_branch": "main"},
        "action": "created",
        "alert": {"number": 7, "state": "fixed"},
    }


@pytest.mark.parametrize("current_state", ["open", "fixed", "dismissed"])
def test_current_alert_state_selects_work(platform, invoke, model, alert_event, current_state):
    state = json.loads(platform.read_text())
    state["alert"]["state"] = current_state
    platform.write_text(json.dumps(state))
    alert_event["alert"]["state"] = "fixed" if current_state == "open" else "open"
    if current_state == "open":
        model[0].append(completion("The affected dependency has no patched version."))
    else:
        model[0].append(completion(tool="no_update", arguments={"reason": "No tracked work changed."}))
    action = invoke(alert_event)
    assert action is not None and action.status == "completed"
    assert action.mode == ("bumper" if current_state == "open" else "issuer")
    assert not json.loads(platform.read_text())["comments"]


def test_unreadable_alert_reports_error_without_publication(platform, invoke, model, alert_event):
    state = json.loads(platform.read_text())
    state["unavailable"] = True
    platform.write_text(json.dumps(state))
    invoke(alert_event, error="GitHub is unavailable")
    assert not json.loads(platform.read_text())["comments"]


@pytest.fixture
def dependency_ci(platform, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_run")
    state = json.loads(platform.read_text())
    state["target"] = {
        "number": 42,
        "state": "open",
        "user": {"login": "dependabot[bot]"},
        "head": {"repo": {"full_name": "example/landing"}},
        "base": {"repo": {"full_name": "example/landing"}},
    }
    platform.write_text(json.dumps(state))
    return {
        "repository": {"full_name": "example/landing", "default_branch": "main"},
        "workflow_run": {
            "id": 123,
            "name": "release-main",
            "event": "pull_request",
            "conclusion": "failure",
            "actor": {"login": "dependabot[bot]"},
            "head_repository": {"full_name": "example/landing"},
            "head_branch": "dependabot/example-lib-2",
            "head_sha": "candidate-head",
            "pull_requests": [{"number": 42}],
            "html_url": "https://github.com/example/landing/actions/runs/123",
        },
    }


@pytest.mark.parametrize("source", ["pull_request", "workflow_dispatch"])
@pytest.mark.parametrize("signed", [False, True])
def test_failed_dependency_pr_gets_one_bump_reply(
    tmp_path, platform, invoke, model, monkeypatch, dependency_ci, source, signed
):
    monkeypatch.setenv("LANDING_GITHUB_BUMP_ON_FAILURE", "true")
    dependency_ci["workflow_run"]["event"] = source
    dependency_ci["workflow_run"]["actor"]["login"] = "maintainer"
    if source == "workflow_dispatch":
        dependency_ci["workflow_run"]["pull_requests"] = []
        state = json.loads(platform.read_text())
        state["associated_pulls"] = [{"number": 42, "state": "open", "head": {"sha": "candidate-head"}}]
        platform.write_text(json.dumps(state))
    stamp = github.marker("bumper", "bump:42:candidate-head")
    (tmp_path / "reply.json").write_text(json.dumps({"body": stamp + "\nThe candidate is blocked upstream."}))
    model[0].extend([
        completion(
            tool="bash",
            arguments={
                "command": "gh api repos/example/landing/issues/42/comments --input reply.json",
            },
        ),
        completion("Reported the dependency candidate's blocker."),
    ])

    def event():
        if not signed:
            return dependency_ci
        from uuid import uuid4

        monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
        monkeypatch.setenv("LANDING_GITHUB_WEBHOOK_SECRET", "signing-fixture")
        body = json.dumps(dependency_ci)
        return {
            "inputs": {
                "event": body,
                "event_name": "workflow_run",
                "delivery": str(uuid4()),
                "signature": "sha256=" + hmac.new(b"signing-fixture", body.encode(), hashlib.sha256).hexdigest(),
            }
        }

    action = invoke(event(), key=None)
    assert action is not None and action.mode == "bumper" and action.status == "completed"
    delivered = json.loads(platform.read_text())
    assert len(delivered["comments"]) == 1
    assert delivered["comments"][0]["body"].endswith("The candidate is blocked upstream.")
    dependency_ci["workflow_run"]["id"] = 456
    assert invoke(event(), key=None, database=tmp_path / "next-run.sqlite3") is None
    assert json.loads(platform.read_text())["comments"] == delivered["comments"]


@pytest.mark.parametrize("condition", ["disabled", "success", "unapproved-workflow", "foreign-source"])
def test_dependency_ci_failure_skips_ineligible_candidates(
    platform, invoke, model, monkeypatch, dependency_ci, condition
):
    monkeypatch.setenv("LANDING_GITHUB_BUMP_ON_FAILURE", "false" if condition == "disabled" else "true")
    run = dependency_ci["workflow_run"]
    if condition == "success":
        run["conclusion"] = condition
    elif condition == "unapproved-workflow":
        run["name"] = "other-workflow"
    elif condition == "foreign-source":
        run["head_repository"]["full_name"] = "outside/landing"
    assert invoke(dependency_ci, key=None) is None
    assert not json.loads(platform.read_text())["comments"]


@pytest.mark.parametrize("condition", ["author", "closed", "stale-head", "fork", "multiple-prs"])
def test_dependency_ci_failure_skips_ineligible_prs(platform, invoke, model, monkeypatch, dependency_ci, condition):
    monkeypatch.setenv("LANDING_GITHUB_BUMP_ON_FAILURE", "true")
    state = json.loads(platform.read_text())
    if condition == "author":
        state["target"]["user"]["login"] = "contributor"
    elif condition == "closed":
        state["target"]["state"] = "closed"
    elif condition == "stale-head":
        state["head"] = "new-candidate-head"
    elif condition == "fork":
        state["target"]["head"]["repo"]["full_name"] = "outside/landing"
    else:
        dependency_ci["workflow_run"]["pull_requests"].append({"number": 43})
    platform.write_text(json.dumps(state))
    assert invoke(dependency_ci, key=None) is None
    assert not json.loads(platform.read_text())["comments"]


@pytest.mark.parametrize(
    ("payload", "diagnostic"),
    [({"action": "created"}, "repository.full_name"), ([], "JSON object")],
)
def test_unusable_event_payload_is_reported_as_invalid_input(tmp_path, platform, monkeypatch, payload, diagnostic):
    source = tmp_path / "event.json"
    source.write_text(json.dumps(payload))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(source))
    monkeypatch.setenv("LANDING_GITHUB_REPOSITORY", "example/landing")
    result = CliRunner().invoke(create_cli_app(), ["github", "event"])
    assert result.exit_code == 2, result.output
    assert diagnostic in result.stderr


def test_comment_actions_require_maintainer_and_use_configurable_identity(
    tmp_path, platform, invoke, model, monkeypatch
):
    config = tmp_path / "landing.yml"
    config.write_text("model: [invalid]")
    monkeypatch.setenv("LANDING_CONFIG", str(config))
    event = {
        "repository": {"full_name": "example/landing"},
        "issue": {"number": 42},
        "comment": {"body": "/landing fix Repair retry behavior.", "user": {"type": "Bot", "login": "bot"}},
    }
    event["comment"]["body"] = "Handled the task."
    assert invoke(event) is None
    assert not model[1]
    config.unlink()
    event["comment"]["body"] = "/landing fix Repair retry behavior."
    event["comment"]["user"] = {"type": "User", "login": "contributor"}
    state = json.loads(platform.read_text())
    state["permission"] = "read"
    platform.write_text(json.dumps(state))
    assert invoke(event) is None
    assert not model[1]
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
    invoke(event, repository="other/repository", error="another repository")
    event["comment"]["body"] = "/landing execute arbitrary-shell"
    invoke(event, error="Choose")


def test_agent_publishes_native_review_with_inline_comment_and_deduplicates(platform, invoke, model, monkeypatch):
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

    event = {
        "repository": {"full_name": "example/landing"},
        "sender": {"type": "User", "login": "maintainer"},
        "pull_request": {"number": 42, "head": {"sha": "candidate-head"}},
    }
    monkeypatch.setenv("INPUT_CHECKS", "true")
    action = invoke(event, key="review:42")
    assert action.status == "completed"
    assert action.decision == "block"
    published = json.loads(platform.read_text())["reviews"]
    assert len(published) == 1
    assert published[0]["commit_id"] == "candidate-head"
    assert published[0]["comments"][0]["path"] == "candidate.py"
    assert published[0]["comments"][0]["line"] == 2
    reactions = json.loads(platform.read_text())["reactions"]
    assert [item["content"] for item in reactions["repos/example/landing/issues/42"]] == ["rocket"]
    state = json.loads(platform.read_text())
    state["reviews"].append({"id": 9, "body": "A later independent review.", "state": "COMMENTED"})
    platform.write_text(json.dumps(state))
    calls = len(requests)
    assert invoke(event, key="review:42").id == action.id
    assert len(requests) == calls
    reactions = json.loads(platform.read_text())["reactions"]
    assert [item["content"] for item in reactions["repos/example/landing/issues/42"]] == ["rocket"]
    assert json.loads(platform.read_text())["reviews"] == state["reviews"]
    monkeypatch.setenv("INPUT_INSTRUCTION", "Review deployment behavior.")
    invoke(event, key="review:42", error="different request")


@pytest.mark.parametrize("foreign", [False, True])
def test_explainer_confirms_only_its_own_conversation_reply(platform, invoke, model, foreign):
    event = {
        "repository": {"full_name": "example/landing"},
        "issue": {"number": 42},
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
    if foreign:
        state = json.loads(platform.read_text())
        state["comments"].append({
            "id": 1,
            "body": github.marker("explainer", "explain") + "\n" + answer,
            "user": {"id": 999},
            "issue_url": "https://api.github.com/repos/example/landing/issues/42",
            "html_url": "https://example.test/comments/1",
        })
        platform.write_text(json.dumps(state))
        responses.popleft()
        responses.popleft()
    action = invoke(event, key="explain")
    assert action is not None
    assert action.status == ("failed" if foreign else "completed")
    assert action.result == ("Reply confirmation failed." if foreign else answer)
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
    assert [item["content"] for item in state["reactions"]["repos/example/landing/pulls/comments/18"]] == ["rocket"]
    assert invoke(event, key="comment:18").id == action.id
    assert json.loads(platform.read_text())["comments"] == state["comments"]
    event["comment"]["body"] = "Fixed in the latest commit; CI is pending."
    assert invoke(event, key="status-update") is None


@pytest.mark.parametrize("source", ["pull_request", "workflow_dispatch"])
def test_automatic_review_updates_one_status_across_candidates_and_fresh_runners(
    tmp_path, platform, invoke, model, monkeypatch, source
):
    responses, requests = model
    event = {
        "repository": {"full_name": "example/landing"},
        "sender": {"type": "User", "login": "maintainer"},
    }
    monkeypatch.setenv("GITHUB_EVENT_NAME", source)
    summary_id = None
    for index, (head, decision, result) in enumerate([
        ("first-candidate", "block", "One outstanding [retry finding](https://example.test/finding)."),
        ("second-candidate", "block", "One outstanding [retry finding](https://example.test/finding)."),
        ("second-candidate", "allow", "No blocking findings."),
    ]):
        state = json.loads(platform.read_text())
        state["head"] = head
        state["target"] = {"pull_request": {}}
        platform.write_text(json.dumps(state))
        if source == "pull_request":
            event["pull_request"] = {"number": 42, "head": {"sha": head}}
        responses.extend([completion(tool="decide", arguments={"decision": decision}), completion(result)])
        key = f"candidate-{index}"
        database = tmp_path / f"runner-{index}.sqlite3"
        action = invoke(event, key=key, number=42, database=database)
        assert (action.status, action.decision, action.result) == ("completed", decision, result)
        state = json.loads(platform.read_text())
        assert not state["reviews"]
        assert len(state["comments"]) == 1
        summary = state["comments"][0]
        summary_id = summary_id or summary["id"]
        assert summary["id"] == summary_id
        assert f"**Landing: {decision}**" in summary["body"]
        assert head in summary["body"]
        assert summary["body"].endswith(result)
        calls = len(requests)
        writes = state["status_writes"]
        for replay_database in (database, tmp_path / f"replay-{index}.sqlite3"):
            replay = invoke(event, key=key, number=42, database=replay_database)
            assert (replay.status, replay.decision, replay.result) == ("completed", decision, result)
        assert len(requests) == calls
        assert json.loads(platform.read_text())["status_writes"] == writes

    # A replaced receipt cannot roll the current status back to an earlier same-head assessment.
    invoke(event, key="candidate-1", number=42, database=tmp_path / "runner-1.sqlite3", error="confirmed publication")
    assert len(requests) == calls
    assert json.loads(platform.read_text())["comments"] == state["comments"]


@pytest.mark.parametrize("change", ["deleted", "publisher", "destination", "result", "candidate"])
def test_status_replay_requires_the_original_published_result(platform, invoke, model, change):
    event = {
        "repository": {"full_name": "example/landing"},
        "sender": {"type": "User", "login": "maintainer"},
        "pull_request": {"number": 42, "head": {"sha": "candidate-head"}},
    }
    model[0].extend([
        completion(tool="decide", arguments={"decision": "allow"}),
        completion("No blocking findings."),
    ])
    assert invoke(event).status == "completed"
    calls = len(model[1])
    state = json.loads(platform.read_text())
    receipt = state["comments"][0]
    if change == "deleted":
        state["comments"] = []
    elif change == "publisher":
        receipt["user"] = {"id": 999}
    elif change == "destination":
        receipt["issue_url"] = "https://api.github.com/repos/example/landing/issues/99"
    elif change == "result":
        receipt["body"] = receipt["body"].replace("No blocking findings.", "An edited result.")
    else:
        receipt["body"] = receipt["body"].replace("candidate-head", "another-head")
    platform.write_text(json.dumps(state))
    diagnostic = (
        "not a reply"
        if change == "destination"
        else "no longer matches"
        if change == "result"
        else "confirmed publication"
    )
    invoke(event, error=diagnostic)
    assert len(model[1]) == calls
    assert json.loads(platform.read_text())["comments"] == state["comments"]


def test_automatic_status_keeps_required_check_failure_authoritative(platform, invoke, model, monkeypatch):
    monkeypatch.setenv("INPUT_CHECKS", "false")
    model[0].extend([
        completion(tool="decide", arguments={"decision": "allow"}),
        completion("No blocking code findings."),
    ])
    action = invoke({
        "repository": {"full_name": "example/landing"},
        "sender": {"type": "User", "login": "maintainer"},
        "pull_request": {"number": 42, "head": {"sha": "candidate-head"}},
    })
    assert (action.status, action.decision) == ("completed", "block")
    summary = json.loads(platform.read_text())["comments"][0]["body"]
    assert "**Landing: block**" in summary
    assert "Required validation failed" in summary


@pytest.mark.parametrize(
    "failure", ["status_write_fails", "status_readback_mismatch", "status_head_changes", "final_head_changes"]
)
def test_automatic_status_fails_on_unconfirmed_or_stale_publication(platform, invoke, model, failure):
    async def finish(**kwargs):
        state = json.loads(platform.read_text())
        if failure == "final_head_changes":
            state["head"] = "next-candidate"
        else:
            state[failure] = True
        platform.write_text(json.dumps(state))
        return completion("No blocking findings.")

    model[0].extend([completion(tool="decide", arguments={"decision": "allow"}), finish])
    action = invoke({
        "repository": {"full_name": "example/landing"},
        "sender": {"type": "User", "login": "maintainer"},
        "pull_request": {"number": 42, "head": {"sha": "candidate-head"}},
    })
    assert action.status == "failed"
    if failure in {"status_write_fails", "final_head_changes"}:
        assert not json.loads(platform.read_text())["comments"]


def test_text_without_required_publication_is_failed_work(platform, invoke, model):
    state = json.loads(platform.read_text())
    state["reviews"].append({
        "id": 1,
        "body": github.marker("gatekeeper", "missing-review"),
        "user": {"id": 999},
        "state": "COMMENTED",
        "commit_id": "candidate-head",
        "html_url": "https://example.test/reviews/1",
    })
    platform.write_text(json.dumps(state))
    responses, _ = model
    responses.append(completion("The review is ready."))
    event = {
        "repository": {"full_name": "example/landing"},
        "sender": {"type": "User", "login": "maintainer"},
        "issue": {"number": 42, "pull_request": {}},
        "comment": {"id": 81, "body": "/landing review Inspect retry behavior.", "user": {"login": "maintainer"}},
    }
    action = invoke(event, key="missing-review")
    assert action.status == "failed"
    assert action.result == "The review is ready."
    assert action.error is not None
    assert "confirmed publication" in action.error["message"]
    assert [
        item["content"]
        for item in json.loads(platform.read_text())["reactions"]["repos/example/landing/issues/comments/81"]
    ] == ["confused"]
    assert json.loads(platform.read_text())["reviews"] == state["reviews"]


def test_stale_review_delegation_keeps_the_current_outcome_reaction(platform, invoke, model):
    state = json.loads(platform.read_text())
    state["head"] = "new-candidate"
    current = {"repos/example/landing/issues/42": [{"id": 7, "content": "rocket", "user": state["publisher"]}]}
    state["reactions"] = current
    platform.write_text(json.dumps(state))
    event = {
        "repository": {"full_name": "example/landing"},
        "sender": {"type": "User", "login": "maintainer"},
        "pull_request": {"number": 42, "head": {"sha": "candidate-head"}},
    }
    invoke(event, key="stale", error="PR head changed")
    assert json.loads(platform.read_text())["reactions"] == current
    assert not model[1]


@pytest.mark.parametrize("lookup_fails", [False, True])
def test_review_stops_queued_tools_for_superseded_or_unverifiable_head(tmp_path, platform, invoke, model, lookup_fails):
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

    event = {
        "repository": {"full_name": "example/landing"},
        "sender": {"type": "User", "login": "maintainer"},
        "pull_request": {"number": 42, "head": {"sha": "candidate-head"}},
    }
    invoke(event, key="superseded", error="PR head changed")
    assert not requests
    assert not json.loads(platform.read_text())["reviews"]
    state["head"] = "candidate-head"
    platform.write_text(json.dumps(state))
    action = invoke(event, key="superseded")
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
def test_comment_delegation_obeys_repository_and_owner_policy(
    platform, invoke, model, trust, owner, actor_id, membership, allowed
):
    state = json.loads(platform.read_text())
    state["owner"] = owner
    if owner["type"] == "Organization":
        state["permission"] = "admin" if membership is not None else "read"
    state["membership"] = membership
    platform.write_text(json.dumps(state))
    event = {
        "repository": {"full_name": "example/landing", "owner": owner},
        "issue": {"number": 42},
        "comment": {
            "body": "/landing explain Explain this failure.",
            "user": {"id": actor_id, "login": "maintainer"},
        },
    }
    responses, requests = model
    responses.append(completion("Investigated the delegated failure."))
    action = invoke(event, trust=trust)
    if allowed:
        assert action is not None
        assert requests
    else:
        assert action is None
        assert not requests


def test_unknown_permissions_fail_instead_of_skipping_or_invoking_agent(platform, invoke, model):
    state = json.loads(platform.read_text())
    state["unavailable"] = True
    platform.write_text(json.dumps(state))
    event = {
        "repository": {"full_name": "example/landing"},
        "issue": {"number": 42},
        "comment": {"body": "/landing fix Repair this issue.", "user": {"login": "maintainer"}},
    }
    invoke(event, error="GitHub is unavailable")
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
def test_untrusted_upstream_does_not_delegate_even_when_successful(
    invoke, model, monkeypatch, name, source, event_name, branch
):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_run")
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


@pytest.mark.parametrize("entry", ["comment", "workflow_dispatch"])
def test_owner_admission_uses_current_repository_ownership(platform, invoke, model, monkeypatch, entry):
    state = json.loads(platform.read_text())
    state["owner"] = {"type": "User", "id": 2}
    platform.write_text(json.dumps(state))
    event = {
        "repository": {"full_name": "example/landing", "owner": {"type": "User", "id": 1}},
        "issue": {"number": 42},
        "comment": {"body": "/landing fix Repair this issue.", "user": {"id": 1, "login": "former-owner"}},
    }
    if entry == "workflow_dispatch":
        event["sender"] = event.pop("comment")["user"]
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
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
    invoke(event, trust="owner", error="membership is not visible")
    assert not model[1]


@pytest.mark.parametrize("lost_record", [False, True])
def test_replayed_repair_does_not_claim_validation(tmp_path, platform, invoke, model, monkeypatch, lost_record):
    monkeypatch.setenv("INPUT_CHECKS", "false")
    (tmp_path / "reply.json").write_text(json.dumps({"body": github.marker("fixer", "delegation") + "\nRepaired."}))
    model[0].extend([
        completion(
            tool="bash", arguments={"command": "gh api repos/example/landing/issues/42/comments --input reply.json"}
        ),
        completion("Repaired."),
    ])
    event = {
        "repository": {"full_name": "example/landing"},
        "comment": {"user": {"login": "maintainer"}, "body": "/landing fix Repair the candidate."},
        "issue": {"number": 42},
    }
    assert invoke(event).status == "failed"
    calls = len(model[1])
    replay = invoke(event, database=tmp_path / "fresh.sqlite3" if lost_record else None)
    assert replay.status == "failed"
    assert len(model[1]) == calls
    assert len(json.loads(platform.read_text())["comments"]) == 1


@pytest.mark.parametrize("case", ["authorized", "revoked", "tampered"])
def test_signed_dispatch_uses_original_authority_and_publication(platform, model, monkeypatch, tmp_path, case):
    from fastapi.testclient import TestClient

    from landing.server import create_app
    from tests.test_server import post

    monkeypatch.setenv("LANDING_RUNNER", "gha")
    monkeypatch.chdir(tmp_path)
    for name, value in {
        "LANDING_GITHUB_WEBHOOK_SECRET": "webhook-fixture",
    }.items():
        monkeypatch.setenv(name, value)
    app = create_app()
    with TestClient(app) as client:
        original = {
            "repository": {"full_name": "example/landing", "default_branch": "main"},
            "action": "created",
            "issue": {"number": 42},
            "comment": {"id": 81, "body": "/landing explain Why did CI fail?", "user": {"login": "maintainer"}},
        }
        response = post(client, original)
        assert response.status_code == 202, response.text
        assert response.json()["workflow_run_id"] == 123
        inputs = json.loads(platform.read_text())["dispatches"][0]["inputs"]
    if case == "revoked":
        state = json.loads(platform.read_text())
        state["permission"] = "read"
        platform.write_text(json.dumps(state))
    elif case == "authorized":
        stamp = github.marker("explainer", f"webhook:{inputs['signature']}")
        model[0].extend([
            completion(
                tool="fs_write",
                arguments={
                    "path": "reply.json",
                    "content": json.dumps({"body": stamp + "\nThe CI fixture failed."}),
                },
            ),
            completion(
                tool="bash",
                arguments={"command": "gh api repos/example/landing/issues/42/comments --input reply.json"},
            ),
            completion(tool="confirm_reply", arguments={"comment_id": 1}),
            completion("Published the explanation."),
        ])
    dispatch_event = tmp_path / "dispatch.json"
    inputs = json.loads(platform.read_text())["dispatches"][0]["inputs"]
    if case == "tampered":
        inputs["event"] = inputs["event"].replace("Why did CI fail?", "Perform different work.")
    dispatch_event.write_text(json.dumps({"inputs": inputs}))
    for name, value in {
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_EVENT_PATH": str(dispatch_event),
        "GITHUB_REPOSITORY": "example/landing",
        "GITHUB_RUN_ID": "123",
        "INPUT_COMMAND": "fix",
        "LANDING_GITHUB_TRUST": "repository",
        "LANDING_DB": str(tmp_path / "runner.sqlite3"),
    }.items():
        monkeypatch.setenv(name, value)
    result = CliRunner().invoke(create_cli_app(), ["github", "event"])
    if case == "tampered":
        assert result.exit_code == 2
        assert "webhook signature" in result.stderr
        assert not model[1]
        return
    assert result.exit_code == 0, result.stderr
    if case == "revoked":
        assert not result.stdout
        assert not model[1]
        assert not json.loads(platform.read_text())["comments"]
    else:
        action = Action.model_validate_json(result.stdout)
        assert action.status == "completed", action.error
        assert action.mode == "explainer"
        assert action.instruction == "Why did CI fail?"
        assert len(json.loads(platform.read_text())["comments"]) == 1
    # A fresh runner reuses native publication evidence even if an unsigned delivery header changes.
    inputs["delivery"] = "a-different-forwarded-id"
    dispatch_event.write_text(json.dumps({"inputs": inputs}))
    monkeypatch.setenv("LANDING_GITHUB_WEBHOOK_SECRET", "webhook-fixture")
    monkeypatch.setenv("LANDING_DB", str(tmp_path / "retry.sqlite3"))
    duplicate = CliRunner().invoke(create_cli_app(), ["github", "event"])
    assert duplicate.exit_code == 0, duplicate.stderr
    assert len(json.loads(platform.read_text())["comments"]) == (1 if case == "authorized" else 0)


def test_forwarded_headers_cannot_grant_native_dispatch_authority(platform, model, monkeypatch, tmp_path):
    body = json.dumps({
        "repository": {"full_name": "example/landing"},
        "sender": {"login": "outsider"},
        "pull_request": {"number": 42, "head": {"sha": "candidate-head"}},
    })
    signature = "sha256=" + hmac.new(b"webhook-fixture", body.encode(), hashlib.sha256).hexdigest()
    source = tmp_path / "event.json"
    source.write_text(
        json.dumps({
            "inputs": {"event": body, "signature": signature, "delivery": "opaque", "event_name": "workflow_dispatch"}
        })
    )
    state = json.loads(platform.read_text())
    state["permission"] = "read"
    platform.write_text(json.dumps(state))
    for name, value in {
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_EVENT_PATH": str(source),
        "GITHUB_REPOSITORY": "example/landing",
        "LANDING_GITHUB_WEBHOOK_SECRET": "webhook-fixture",
    }.items():
        monkeypatch.setenv(name, value)
    result = CliRunner().invoke(create_cli_app(), ["github", "event"])
    assert result.exit_code == 0, result.stderr
    assert not result.stdout
    assert not model[1]


def _forwarded_comment(secret, event, event_name="issue_comment"):
    body = json.dumps(event)
    signature = "sha256=" + hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return {"inputs": {"event": body, "signature": signature, "event_name": event_name, "delivery": "forwarded"}}


def _deliver(model, stamp):
    model.extend([
        completion(
            tool="fs_write",
            arguments={"path": "reply.json", "content": json.dumps({"body": stamp + "\nRepaired."})},
        ),
        completion(
            tool="bash", arguments={"command": "gh api repos/example/landing/issues/42/comments --input reply.json"}
        ),
        completion("Repaired."),
    ])


def test_native_and_forwarded_ingress_take_the_same_admission_decision(platform, invoke, model, monkeypatch):
    event = {
        "repository": {"full_name": "example/landing"},
        "issue": {"number": 42},
        "comment": {"body": "/landing fix Repair retry behavior.", "user": {"type": "User", "login": "maintainer"}},
    }
    responses, requests = model
    _deliver(responses, github.marker("fixer", "delegation"))
    native = invoke(event)
    monkeypatch.setenv("LANDING_GITHUB_WEBHOOK_SECRET", "signing-fixture")
    envelope = _forwarded_comment("signing-fixture", event)
    body = json.dumps(event)
    signature = "sha256=" + hmac.new(b"signing-fixture", body.encode(), hashlib.sha256).hexdigest()
    assert envelope["inputs"]["signature"] == signature
    _deliver(responses, github.marker("fixer", f"webhook:{signature}"))
    before = len(requests)
    forwarded = invoke(envelope, key=None)
    assert native is not None and forwarded is not None
    assert (
        (native.mode, native.instruction, native.status)
        == (
            forwarded.mode,
            forwarded.instruction,
            forwarded.status,
        )
        == ("fixer", "Repair retry behavior.", "completed")
    )
    assert len(requests) > before
    # Each ingress keeps its own delivery identity: the native run and the signed body.
    assert github.marker("fixer", "delegation") != github.marker("fixer", f"webhook:{signature}")


def test_forwarded_ingress_rejects_an_unauthorized_caller_like_a_native_event(platform, invoke, model, monkeypatch):
    event = {
        "repository": {"full_name": "example/landing"},
        "issue": {"number": 42},
        "comment": {"body": "/landing fix Repair retry behavior.", "user": {"type": "User", "login": "contributor"}},
    }
    state = json.loads(platform.read_text())
    state["permission"] = "read"
    platform.write_text(json.dumps(state))
    monkeypatch.setenv("LANDING_GITHUB_WEBHOOK_SECRET", "signing-fixture")
    assert invoke(_forwarded_comment("signing-fixture", event)) is None
    assert invoke(event) is None
    assert not model[1]


@pytest.mark.parametrize("envelope", [{"inputs": "not-an-object"}, {"inputs": {"event": "{}"}}])
def test_forwarded_envelope_that_is_not_usable_input_exits_invalid(tmp_path, platform, monkeypatch, model, envelope):
    source = tmp_path / "event.json"
    source.write_text(json.dumps(envelope))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(source))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/landing")
    monkeypatch.setenv("LANDING_GITHUB_WEBHOOK_SECRET", "signing-fixture")
    result = CliRunner().invoke(create_cli_app(), ["github", "event"])
    assert result.exit_code == 2, result.output
    assert "JSON object" in result.stderr or "signature" in result.stderr
    assert not model[1]


def _signed_envelope(secret, body, **inputs):
    signature = "sha256=" + hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return {"inputs": {"event": body, "signature": signature, **inputs}}


@pytest.mark.parametrize("event_name", ["", "   ", 7])
def test_forwarded_delivery_never_passes_for_a_native_event(tmp_path, platform, monkeypatch, model, event_name):
    """A forwarded envelope with an unusable event name is rejected, not admitted as native work."""
    body = json.dumps({
        "repository": {"full_name": "example/landing"},
        "sender": {"login": "outsider"},
        "pull_request": {"number": 42, "head": {"sha": "candidate-head"}},
    })
    source = tmp_path / "event.json"
    source.write_text(json.dumps(_signed_envelope("signing-fixture", body, event_name=event_name)))
    state = json.loads(platform.read_text())
    state["permission"] = "read"
    platform.write_text(json.dumps(state))
    for name, value in {
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_EVENT_PATH": str(source),
        "GITHUB_REPOSITORY": "example/landing",
        "LANDING_GITHUB_WEBHOOK_SECRET": "signing-fixture",
    }.items():
        monkeypatch.setenv(name, value)
    result = CliRunner().invoke(create_cli_app(), ["github", "event"])
    assert result.exit_code == 2, result.output
    assert "event name" in result.stderr
    assert not model[1]


@pytest.mark.parametrize("inputs", ["", 0, [], ["event"]])
def test_non_object_forwarded_inputs_are_invalid_not_native(tmp_path, platform, monkeypatch, model, inputs):
    source = tmp_path / "event.json"
    source.write_text(json.dumps({"inputs": inputs}))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(source))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/landing")
    monkeypatch.setenv("LANDING_GITHUB_WEBHOOK_SECRET", "signing-fixture")
    result = CliRunner().invoke(create_cli_app(), ["github", "event"])
    assert result.exit_code == 2, result.output
    assert "JSON object" in result.stderr
    assert not model[1]


@pytest.mark.parametrize("body", ["", None])
def test_explicitly_empty_forwarded_body_is_never_treated_as_native(tmp_path, platform, monkeypatch, model, body):
    """The forwarded field's presence selects verification, so an empty value cannot fall back."""
    signature = "sha256=" + hmac.new(b"signing-fixture", (body or "").encode(), hashlib.sha256).hexdigest()
    source = tmp_path / "event.json"
    source.write_text(json.dumps({"inputs": {"event": body, "signature": signature, "event_name": "issue_comment"}}))
    for name, value in {
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_EVENT_PATH": str(source),
        "GITHUB_REPOSITORY": "example/landing",
        "LANDING_GITHUB_WEBHOOK_SECRET": "signing-fixture",
    }.items():
        monkeypatch.setenv(name, value)
    result = CliRunner().invoke(create_cli_app(), ["github", "event"])
    assert result.exit_code == 2, result.output
    assert "forwarded event body" in result.stderr
    assert not model[1]


def test_native_dispatch_inputs_do_not_select_forwarded_verification(tmp_path, platform, monkeypatch, model):
    """A native workflow_dispatch event has inputs too; only a forwarded event field selects verification."""
    event = {
        "inputs": {"command": "fix", "instruction": "Repair the workflow."},
        "repository": {"full_name": "example/landing"},
        "sender": {"type": "User", "login": "maintainer"},
    }
    source = tmp_path / "event.json"
    source.write_text(json.dumps(event))
    for name, value in {
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_EVENT_PATH": str(source),
        "GITHUB_REPOSITORY": "example/landing",
        "GITHUB_RUN_ID": "acceptance",
        "LANDING_DB": str(tmp_path / "native.sqlite3"),
    }.items():
        monkeypatch.setenv(name, value)
    model[0].append(completion("Repaired the workflow."))
    result = CliRunner().invoke(create_cli_app(), ["github", "event"])
    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout)["instruction"] == "Carry out the delegated task using repository guidance."


def test_signing_key_leaves_the_environment_before_configuration_or_event_reading(
    tmp_path, platform, monkeypatch, model
):
    """Even an unreadable event file must not leave the secret for the work this process starts."""
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(tmp_path / "missing.json"))
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/landing")
    monkeypatch.setenv("LANDING_GITHUB_WEBHOOK_SECRET", "signing-fixture")
    result = CliRunner().invoke(create_cli_app(), ["github", "event"])
    assert result.exit_code == 2, result.output
    assert "LANDING_GITHUB_WEBHOOK_SECRET" not in os.environ
    assert not model[1]


def test_executor_workflow_empty_dispatch_defaults_stay_native_maintenance(tmp_path, platform, monkeypatch):
    """The workflow declares empty event/signature/event_name/delivery defaults for maintenance."""
    from unittest.mock import AsyncMock

    from landing.adapters.github.events import NativeEvent

    event = {
        "inputs": {"event": "", "signature": "", "event_name": "", "delivery": ""},
        "repository": {"full_name": "example/landing", "default_branch": "main"},
    }
    source = tmp_path / "event.json"
    source.write_text(json.dumps(event))
    output = tmp_path / "output.txt"
    for name, value in {
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_EVENT_PATH": str(source),
        "GITHUB_REPOSITORY": "example/landing",
        "GITHUB_OUTPUT": str(output),
        "LANDING_DB": str(tmp_path / "maintenance.sqlite3"),
    }.items():
        monkeypatch.setenv(name, value)
    execute = AsyncMock(return_value=None)
    monkeypatch.setattr("landing.cli.execute_github", execute)
    result = CliRunner().invoke(create_cli_app(), ["github", "event"])
    assert result.exit_code == 0, result.output
    assert execute.await_args is not None
    ingress = execute.await_args.args[1]
    assert isinstance(ingress, NativeEvent)
    assert ingress.event_name == "workflow_dispatch"
    assert "skipped" in output.read_text()


def test_claimed_forwarded_body_with_empty_bytes_authenticates_the_exact_body(tmp_path, platform, monkeypatch, model):
    """A whitespace body is authenticated as sent, then rejected as empty; neither is a signature pass."""
    cases = {
        "whitespace-signed-as-sent": ("   ", hmac.new(b"signing-fixture", b"   ", hashlib.sha256).hexdigest(), True),
        "whitespace-signed-as-empty": ("   ", hmac.new(b"signing-fixture", b"", hashlib.sha256).hexdigest(), False),
    }
    for case, (body, digest, authentic) in cases.items():
        source = tmp_path / f"{case}.json"
        source.write_text(
            json.dumps({"inputs": {"event": body, "signature": "sha256=" + digest, "event_name": "issue_comment"}})
        )
        for name, value in {
            "GITHUB_EVENT_PATH": str(source),
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REPOSITORY": "example/landing",
            "LANDING_GITHUB_WEBHOOK_SECRET": "signing-fixture",
        }.items():
            monkeypatch.setenv(name, value)
        result = CliRunner().invoke(create_cli_app(), ["github", "event"])
        assert result.exit_code == 2, (case, result.output)
        expected = "must not be empty" if authentic else "Invalid GitHub webhook signature"
        assert expected in result.stderr, (case, result.stderr)
    assert not model[1]


def test_missing_event_file_still_reports_a_failed_step(tmp_path, platform, monkeypatch, model):
    """Output destinations are the runner's files; a broken context must not silence the report."""
    output = tmp_path / "output.txt"
    for name, value in {
        "GITHUB_EVENT_PATH": str(tmp_path / "missing.json"),
        "GITHUB_REPOSITORY": "example/landing",
        "GITHUB_OUTPUT": str(output),
    }.items():
        monkeypatch.setenv(name, value)
    result = CliRunner().invoke(create_cli_app(), ["github", "event"])
    assert result.exit_code == 2, result.output
    assert "status<<" in output.read_text()
    assert "failed" in output.read_text()
    assert not model[1]


def test_native_event_files_still_run_without_forwarded_inputs(tmp_path, platform, monkeypatch, model):
    event = {"repository": {"full_name": "example/landing"}, "sender": {"type": "Bot", "login": "team-app[bot]"}}
    source = tmp_path / "event.json"
    source.write_text(json.dumps(event))
    for name, value in {
        "GITHUB_EVENT_PATH": str(source),
        "GITHUB_EVENT_NAME": "release",
        "GITHUB_REPOSITORY": "example/landing",
        "GITHUB_RUN_ID": "acceptance",
        "LANDING_GITHUB_WEBHOOK_SECRET": "signing-fixture",
        "LANDING_DB": str(tmp_path / "native.sqlite3"),
    }.items():
        monkeypatch.setenv(name, value)
    model[0].append(completion("Explained the release."))
    result = CliRunner().invoke(create_cli_app(), ["github", "event"])
    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "completed"


@pytest.mark.parametrize(
    "envelope",
    [
        {"inputs": {"event": "{}"}},
        {"inputs": {"event": "{}", "signature": "sha256=wrong", "event_name": "issue_comment"}},
        {"inputs": "not-an-object"},
    ],
)
def test_malformed_envelope_does_not_leave_the_signing_key(tmp_path, platform, monkeypatch, model, envelope):
    source = tmp_path / "event.json"
    source.write_text(json.dumps(envelope))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(source))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/landing")
    monkeypatch.setenv("LANDING_GITHUB_WEBHOOK_SECRET", "signing-fixture")
    result = CliRunner().invoke(create_cli_app(), ["github", "event"])
    assert result.exit_code == 2, result.output
    assert "LANDING_GITHUB_WEBHOOK_SECRET" not in os.environ
    assert not model[1]


def test_routing_one_event_repeatedly_gives_the_same_delegation(monkeypatch):
    monkeypatch.setenv("LANDING_GITHUB_REPOSITORY", "example/landing")
    options = GitHubSettings()
    event = {
        "repository": {"full_name": "example/landing"},
        "issue": {"number": 42},
        "comment": {
            "body": "/landing fix Repair retry behavior.\nPreserve the public CLI.",
            "user": {"login": "writer"},
        },
    }
    first, second = route_event(options, event), route_event(options, dict(event))
    assert (first.command, first.instruction, first.number) == (
        "fix",
        "Repair retry behavior.\nPreserve the public CLI.",
        42,
    )
    assert first == second
    assert options.instruction == GitHubSettings.model_fields["instruction"].default


class StubPublication:
    """Stand in for receipts so execution reaches context construction."""

    action_id = None

    def __init__(self, *args, **kwargs):
        pass

    def find(self):
        return None

    def replay(self, request, key):
        return None

    def complete(self, action):
        return None


class StubPluginManager:
    def register(self, *args, **kwargs):
        pass

    def unregister(self, *args, **kwargs):
        pass


class StubLanding:
    """Capture the request execution would hand to the agent."""

    def __init__(self, workspace):
        self.framework = type("Framework", (), {"workspace": workspace, "plugin_manager": StubPluginManager()})()
        self.tools = []
        self.tasks = None
        self.request = None

    @contextlib.asynccontextmanager
    async def running(self):
        yield self

    async def run(self, request, **kwargs):
        self.request = request
        return Action(
            id="stub",
            mode=request.mode,
            instruction=request.instruction,
            workspace=str(self.framework.workspace),
            status="completed",
            created_at="now",
            updated_at="now",
        )


def test_execution_context_names_the_failed_run_from_the_admitted_delegation(monkeypatch, tmp_path):
    """Triage must cite the failed workflow the event names, not the dispatching run."""
    monkeypatch.setenv("LANDING_GITHUB_REPOSITORY", "example/landing")
    event = {
        "repository": {"full_name": "example/landing", "default_branch": "main"},
        "workflow_run": {
            "id": 1234,
            "event": "push",
            "head_branch": "main",
            "name": "release-main",
            "head_repository": {"full_name": "example/landing"},
        },
    }
    options = GitHubSettings(run_id="dispatch-run-900", upstream_workflow=["release-main"])
    accepted, delegation = execution.prepare_event(options, event, authenticated_event="workflow_run")
    assert accepted and delegation is not None and delegation.run_id == "1234"
    landing = StubLanding(tmp_path)
    monkeypatch.setattr(execution, "Publication", StubPublication)
    monkeypatch.setattr(execution, "pull_target", lambda *args, **kwargs: (False, 0, ""))
    asyncio.run(execution._run(cast("Runtime", landing), options, delegation, checked_revision=""))
    assert landing.request is not None
    context = next(item for item in landing.request.input if item.name == "github-context.json")
    assert json.loads(context.content)["native_run_id"] == "1234"


def test_repositories_do_not_share_model_history_in_the_same_workspace(platform, model, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LANDING_DB", str(tmp_path / "history.sqlite3"))
    monkeypatch.setenv("INPUT_COMMAND", "explain")
    monkeypatch.setenv("LANDING_GITHUB_DELIVERY_KEY", "shared-label")
    monkeypatch.setenv("LANDING_GITHUB_REPOSITORY", "example/landing")
    monkeypatch.setenv("INPUT_INSTRUCTION", "Remember private-note=123.")
    model[0].append(completion("Remembered the note."))
    assert CliRunner().invoke(create_cli_app(), ["github", "event"]).exit_code == 0

    async def inspect(**request):
        return completion("Leaked context." if "private-note=123" in str(request["messages"]) else "Isolated context.")

    monkeypatch.setenv("LANDING_GITHUB_REPOSITORY", "another/landing")
    monkeypatch.setenv("INPUT_INSTRUCTION", "Check whether a private note from another repository is visible.")
    model[0].append(inspect)
    result = CliRunner().invoke(create_cli_app(), ["github", "event"])
    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout)["result"] == "Isolated context."

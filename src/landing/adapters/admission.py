"""GitHub-native admission, usable before installing the agent runtime."""

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path


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
    if trust == "repository":
        permission = identity(f"repos/{repository}/collaborators/{user['login']}/permission", repository)
        return permission["permission"] in {"admin", "write"}
    if owner["type"] == "User":
        return user["id"] == owner["id"]
    # Organization owners have admin access to every repository. Reject known
    # non-owners before querying private membership, where 404 can hide access.
    permission = identity(f"repos/{repository}/collaborators/{user['login']}/permission", repository)
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
    first = (comment.get("body") or "").strip().split(maxsplit=1) if comment else []
    if comment and (not first or first[0] != prefix):
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
    if not permitted(repository, actor, trust, owner or {}):
        return False
    rerunner = os.getenv("GITHUB_TRIGGERING_ACTOR")
    if trust == "owner" and rerunner and rerunner != os.getenv("GITHUB_ACTOR"):
        user = identity(f"users/{rerunner}", repository)
        return permitted(repository, user, trust, owner or {})
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository", default=os.environ.get("GITHUB_REPOSITORY"), required=not os.getenv("GITHUB_REPOSITORY")
    )
    parser.add_argument("--event", type=Path, default=os.getenv("GITHUB_EVENT_PATH"))
    parser.add_argument("--trust", choices=["repository", "owner"], default="repository")
    parser.add_argument("--upstream-workflow", action="append", default=[])
    parser.add_argument("--command-prefix", default="/landing")
    args = parser.parse_args()
    event = json.loads(args.event.read_text()) if args.event else None
    allowed = admitted(
        args.repository, event, trust=args.trust, upstream=tuple(args.upstream_workflow), prefix=args.command_prefix
    )
    value = f"allowed={str(allowed).lower()}"
    print(value)
    if output := os.getenv("GITHUB_OUTPUT"):
        with Path(output).open("a") as destination:
            destination.write(value + "\n")


if __name__ == "__main__":
    main()

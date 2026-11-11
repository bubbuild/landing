"""Prepared gh access and GitHub JSON response helpers."""

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from .settings import GitHubAccess


def gh(
    args: list[str],
    repository: str | None = None,
    *,
    token: str | None = None,
    stdin: str | None = None,
    timeout: float = 120,
    cwd: Path | None = None,
) -> str:
    environment = dict(os.environ)
    if repository is None:
        environment.pop("GH_REPO", None)
    else:
        environment["GH_REPO"] = repository
    if token:
        environment["GH_TOKEN"] = token
    result = subprocess.run(  # noqa: S603 -- explicit argv with caller-prepared authentication.
        ["gh", *args],  # noqa: S607 -- caller-prepared PATH.
        env=environment,
        cwd=cwd,
        capture_output=True,
        text=True,
        input=stdin,
        timeout=timeout,
    )
    if result.returncode:
        message = result.stderr.strip() or "GitHub CLI failed."
        raise RuntimeError(message)
    return result.stdout


def identity(endpoint: str, repository: str) -> dict:
    return json.loads(gh(["api", endpoint], repository, token=GitHubAccess().admission_token))


def required(source: object, *path: str) -> Any:
    """Read a nested event field, reporting an unusable payload as invalid input."""
    value: Any = source
    for name in path:
        if not isinstance(value, dict) or name not in value:
            message = f"The event payload is missing {'.'.join(path)}."
            raise ValueError(message)
        value = value[name]
    return value


def rows(endpoint: str, repository: str) -> list[dict]:
    return [item for page in json.loads(gh(["api", endpoint, "--paginate", "--slurp"], repository)) for item in page]


def repository_at(workspace: Path) -> str:
    return gh(["repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"], cwd=workspace).strip()

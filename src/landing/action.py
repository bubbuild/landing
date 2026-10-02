"""GitHub Action entry point; environment preparation stays in the workflow."""

import os
from pathlib import Path
from uuid import uuid4

from landing.adapters.github import main as github_main


def main() -> int:
    path = Path(
        os.environ.get("INPUT_DATABASE") or Path(os.environ.get("RUNNER_TEMP", ".")) / "landing/landing.sqlite3"
    )
    args = [os.getenv("INPUT_COMMAND", "review"), "--db", str(path)]
    values = {
        "trust": os.getenv("INPUT_TRUST") or "repository",
        "repository": os.getenv("INPUT_REPOSITORY") or os.environ["GITHUB_REPOSITORY"],
        "instruction": os.getenv("INPUT_INSTRUCTION") or "Carry out the delegated task using repository guidance.",
        "number": os.getenv("INPUT_NUMBER") or "0",
        "head": os.getenv("INPUT_HEAD") or "",
        "run-id": os.getenv("INPUT_RUN_ID") or os.getenv("GITHUB_RUN_ID", ""),
        "checked-revision": os.getenv("INPUT_CHECKED_REVISION", os.getenv("GITHUB_SHA", "")),
        "command-prefix": os.getenv("INPUT_COMMAND_PREFIX") or "/landing",
        "delivery-key": os.getenv("INPUT_DELIVERY_KEY")
        or f"action:{os.environ['GITHUB_RUN_ID']}:{os.getenv('GITHUB_RUN_ATTEMPT', '1')}",
    }
    event = os.getenv("GITHUB_EVENT_PATH")
    if event:
        values["event"] = event
    for name, value in values.items():
        args.extend(["--" + name, value])
    for check in os.getenv("INPUT_CHECKS", "").splitlines():
        if check.strip():
            args.extend(["--check", check])
    for name in os.getenv("INPUT_UPSTREAM_WORKFLOW", "").splitlines():
        if name.strip():
            args.extend(["--upstream-workflow", name.strip()])
    action = github_main(args)
    outputs = {
        "id": action.id if action else "",
        "status": action.status if action else "skipped",
        "decision": action.decision or "" if action else "",
        "result": action.result or "" if action else "",
    }
    if destination := os.getenv("GITHUB_OUTPUT"):
        with Path(destination).open("a") as output:
            for name, value in outputs.items():
                delimiter = uuid4().hex
                output.write(f"{name}<<{delimiter}\n{value}\n{delimiter}\n")
    if summary := os.getenv("GITHUB_STEP_SUMMARY"):
        with Path(summary).open("a") as output:
            output.write(outputs["result"] + "\n")
    return int(action is not None and action.status != "completed")


if __name__ == "__main__":
    raise SystemExit(main())

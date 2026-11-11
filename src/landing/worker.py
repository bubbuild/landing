"""Local process entry: load accepted work and call the execution API directly."""

import asyncio
import os
import sys
from pathlib import Path

from landing.adapters.github.api import repository_at, required
from landing.adapters.github.execution import execute
from landing.adapters.github.settings import GitHubSettings
from landing.adapters.github.webhook import read_webhook
from landing.database import open_database
from landing.hooks import TerminationHooks
from landing.inbox import Inbox


async def run(database: Path, event_id: int, config_file: Path, skill_dirs: tuple[Path, ...]):
    with open_database(database) as engine:
        inputs = required(Inbox(engine).get(event_id), "inputs")
    secret = os.environ.pop("LANDING_GITHUB_WEBHOOK_SECRET", "")
    # A local worker has no native Actions authority, even if its host was launched from CI.
    for name in tuple(os.environ):
        if name.startswith("GITHUB_") and name != "GITHUB_TOKEN":
            os.environ.pop(name)
    payload = read_webhook(required(inputs, "event").encode(), required(inputs, "signature"), secret)
    workspace = Path.cwd()
    options = GitHubSettings.for_webhook(repository_at(workspace))
    return await execute(
        options,
        payload,
        database=database,
        workspace=workspace,
        config_file=config_file,
        skill_dirs=skill_dirs,
        plugins=[TerminationHooks()],
        source=required(inputs, "event_name"),
        fallback_key=f"webhook:{required(inputs, 'signature')}",
    )


def main() -> int:
    database, event_id, config_file, *roots = sys.argv[1:]
    try:
        action = asyncio.run(run(Path(database), int(event_id), Path(config_file), tuple(Path(root) for root in roots)))
    except (OSError, RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if action is None:
        return 0
    print(action.model_dump_json(indent=2))
    return int(action.status != "completed")


if __name__ == "__main__":
    raise SystemExit(main())

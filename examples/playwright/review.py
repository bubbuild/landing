"""Review a prepared preview using Landing's existing SDK and MCP configuration."""

import asyncio
from pathlib import Path

from landing.models import ActionRequest
from landing.runtime import Runtime


async def main() -> None:
    async with Runtime(Path("landing.sqlite3")).running() as landing:
        action = await landing.command(
            "review",
            ActionRequest(
                mode="gatekeeper",
                workspace=str(Path.cwd()),
                instruction="Review http://127.0.0.1:8000. Check navigation and the API reference. Report only reproduced problems with concise evidence.",
            ),
        )
        print(action.result or action.error)


if __name__ == "__main__":
    asyncio.run(main())

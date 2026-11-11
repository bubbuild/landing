"""A durable local inbox and worker, owned by Bub's native lifespan hooks."""

import asyncio
import logging
import shlex
import sys
from pathlib import Path

from bub import BubFramework, hookimpl
from bub.builtin.shell_manager import shell_manager
from bub.environment import LocalEnvironment

from landing.database import database_engine, open_database, own_database
from landing.inbox import Inbox


class LocalRunner:
    def __init__(
        self, framework: BubFramework, database: Path, workspace: Path, skill_dirs: tuple[Path, ...] = ()
    ) -> None:
        self.framework = framework
        self.database = database.expanduser().resolve()
        self.workspace = workspace.expanduser().resolve()
        self.skill_dirs = tuple(root.expanduser().resolve() for root in skill_dirs)
        self.engine = database_engine(self.database)
        self.inbox = Inbox(self.engine)
        self.pending: asyncio.Event
        self.worker: asyncio.Task | None = None

    @hookimpl
    def provide_environment(self):
        return LocalEnvironment(self.workspace)

    @hookimpl(specname="provide_lifespan")
    async def run_worker(self):
        # The inbox consumer and its child execution own separate locks; SQLite coordinates their writes.
        with own_database(self.database.with_name(self.database.name + ".worker")), open_database(self.engine):
            self.pending = asyncio.Event()
            self.worker = asyncio.create_task(self.work())
            try:
                yield
            finally:
                self.worker.cancel()
                await asyncio.gather(self.worker, return_exceptions=True)

    def submit(self, envelope: dict) -> int:
        if self.worker is None or self.worker.done():
            message = "The local worker is unavailable."
            raise RuntimeError(message)
        queued_id = self.inbox.submit(envelope)
        self.pending.set()
        return queued_id

    async def work(self) -> None:
        environment = await self.framework.get_environment("landing-worker")
        if environment is None:
            message = "The local worker has no execution environment."
            raise RuntimeError(message)
        async with shell_manager.lifespan():
            while True:
                self.pending.clear()
                event_id = self.inbox.next()
                if event_id is None:
                    await self.pending.wait()
                    continue
                try:
                    command = [
                        sys.executable,
                        "-I",
                        "-m",
                        "landing.worker",
                        str(self.database),
                        str(event_id),
                        str(self.framework.config_file),
                        *map(str, self.skill_dirs),
                    ]
                    process = await shell_manager.start(cmd=shlex.join(command), cwd=None, environment=environment)
                    await shell_manager.wait_closed(process.shell_id)
                    if process.returncode:
                        message = process.output.strip() or f"Local execution exited {process.returncode}."
                        raise RuntimeError(message)  # noqa: TRY301 -- retain failed work in the inbox.
                    self.inbox.finish(event_id)
                except Exception as exc:
                    logging.getLogger(__name__).exception("Local event %s failed", event_id)
                    self.inbox.finish(event_id, str(exc))

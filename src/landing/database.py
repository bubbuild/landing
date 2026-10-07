"""Shared SQLite lifecycle for task records and Bub tape storage."""

import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

from sqlalchemy import URL, Engine, create_engine, event

# Version 1 is the schema released in 0.2.0; raise it when a migration changes the schema.
SCHEMA_VERSION = 1
SCHEMA = """
CREATE TABLE IF NOT EXISTS actions (
    id TEXT PRIMARY KEY NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    instruction TEXT,
    input TEXT NOT NULL,
    workspace TEXT,
    checks TEXT NOT NULL,
    result TEXT,
    decision TEXT,
    error_code TEXT,
    error_message TEXT,
    retry_of TEXT REFERENCES actions(id),
    cancel_requested_at TEXT,
    idempotency_scope TEXT NOT NULL,
    idempotency_key TEXT,
    request_hash TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    UNIQUE (idempotency_scope, idempotency_key)
);
CREATE INDEX IF NOT EXISTS actions_queue ON actions(status, created_at, id);
CREATE INDEX IF NOT EXISTS actions_history ON actions(created_at, id);
CREATE TABLE IF NOT EXISTS action_events (
    id INTEGER PRIMARY KEY,
    action_id TEXT NOT NULL REFERENCES actions(id),
    type TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS action_events_history ON action_events(action_id, id);
CREATE TABLE IF NOT EXISTS tape_entries (
    id INTEGER PRIMARY KEY,
    tape TEXT NOT NULL,
    entry TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS tape_entries_history ON tape_entries(tape, id);
"""


@contextmanager
def own_database(path: Path) -> Iterator[None]:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Lock a sibling file: on macOS, flock on the database conflicts with SQLite's own fcntl locks.
    with path.with_name(path.name + ".lock").open("a+b") as owner:
        try:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            message = "Another worker owns this database. Use its HTTP API or a different --db."
            raise ValueError(message) from exc
        yield


def database_engine(path: Path) -> Engine:
    """Configure a SQLite engine without opening connections or creating files."""
    path = path.expanduser().resolve()
    engine = create_engine(URL.create("sqlite", database=str(path)), connect_args={"timeout": 5})

    @event.listens_for(engine, "connect")
    def configure(connection, record):
        cursor = connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.execute("PRAGMA journal_mode = WAL")
        finally:
            cursor.close()

    return engine


@contextmanager
def open_database(path: Path | Engine) -> Iterator[Engine]:
    engine = path if isinstance(path, Engine) else database_engine(path)
    Path(cast(str, engine.url.database)).parent.mkdir(parents=True, exist_ok=True)
    try:
        with engine.begin() as connection:
            if connection.exec_driver_sql("PRAGMA user_version").scalar_one() > SCHEMA_VERSION:
                message = "A newer Landing release created this database. Upgrade Landing to use it."
                raise ValueError(message)
            for statement in SCHEMA.split(";"):
                if statement.strip():
                    connection.exec_driver_sql(statement)
            connection.exec_driver_sql(f"PRAGMA user_version = {SCHEMA_VERSION:d}")
        yield engine
    finally:
        engine.dispose()

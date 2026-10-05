"""Shared SQLite lifecycle for task records and Bub tape storage."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import URL, Engine, create_engine, event

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
def open_database(path: Path) -> Iterator[Engine]:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(URL.create("sqlite", database=str(path)), connect_args={"timeout": 5})

    @event.listens_for(engine, "connect")
    def configure(connection, record):
        cursor = connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.execute("PRAGMA journal_mode = WAL")
        finally:
            cursor.close()

    try:
        with engine.begin() as connection:
            for statement in SCHEMA.split(";"):
                if statement.strip():
                    connection.exec_driver_sql(statement)
        yield engine
    finally:
        engine.dispose()

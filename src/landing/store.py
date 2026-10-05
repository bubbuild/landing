"""Small SQLite TapeStore, reusing Bub's query implementation and async adapter."""

from __future__ import annotations

import json
from dataclasses import asdict

from bub.store import InMemoryQueryMixin
from bub.tape import TapeEntry
from sqlalchemy import Engine


class SQLiteTapeStore(InMemoryQueryMixin):
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def list_tapes(self) -> list[str]:
        with self.engine.connect() as connection:
            return [
                row[0] for row in connection.exec_driver_sql("SELECT DISTINCT tape FROM tape_entries ORDER BY tape")
            ]

    def reset(self, tape: str) -> None:
        with self.engine.begin() as connection:
            connection.exec_driver_sql("DELETE FROM tape_entries WHERE tape = ?", (tape,))

    def read(self, tape: str) -> list[TapeEntry] | None:
        with self.engine.connect() as connection:
            rows = connection.exec_driver_sql(
                "SELECT id, entry FROM tape_entries WHERE tape = ? ORDER BY id", (tape,)
            ).fetchall()
        return [TapeEntry(**{**json.loads(row[1]), "id": row[0]}) for row in rows] or None

    def append(self, tape: str, entry: TapeEntry) -> None:
        with self.engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO tape_entries (tape, entry) VALUES (?, ?)", (tape, json.dumps(asdict(entry)))
            )

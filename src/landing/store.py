"""Small SQLite TapeStore, reusing Bub's query implementation and async adapter."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from threading import RLock

from bub.store import InMemoryQueryMixin
from bub.tape import TapeEntry


class SQLiteTapeStore(InMemoryQueryMixin):
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=5, check_same_thread=False)
        self.lock = RLock()
        self.connection.executescript("""
            PRAGMA journal_mode = WAL;
            CREATE TABLE IF NOT EXISTS tape_entries (
                id INTEGER PRIMARY KEY,
                tape TEXT NOT NULL,
                entry TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS tape_entries_history ON tape_entries(tape, id);
        """)

    def list_tapes(self) -> list[str]:
        with self.lock:
            return [row[0] for row in self.connection.execute("SELECT DISTINCT tape FROM tape_entries ORDER BY tape")]

    def reset(self, tape: str) -> None:
        with self.lock, self.connection:
            self.connection.execute("DELETE FROM tape_entries WHERE tape = ?", (tape,))

    def read(self, tape: str) -> list[TapeEntry] | None:
        with self.lock:
            rows = self.connection.execute(
                "SELECT id, entry FROM tape_entries WHERE tape = ? ORDER BY id", (tape,)
            ).fetchall()
        return [TapeEntry(**{**json.loads(row[1]), "id": row[0]}) for row in rows] or None

    def append(self, tape: str, entry: TapeEntry) -> None:
        with self.lock, self.connection:
            self.connection.execute(
                "INSERT INTO tape_entries (tape, entry) VALUES (?, ?)", (tape, json.dumps(asdict(entry)))
            )

    def close(self) -> None:
        with self.lock:
            self.connection.close()

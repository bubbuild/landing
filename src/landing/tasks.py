"""Bub task sidecar: durable admission and task records in the tape database."""

from __future__ import annotations

import builtins
import hashlib
import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from bub.tape import utc_now

from landing.models import TERMINAL, Action, ActionRequest, Decision, Event, Status

SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
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
"""


class ConflictError(ValueError):
    """An existing request or action conflicts with the operation."""


class Tasks:
    """Sidecar provider owning the relational format, not a second execution engine."""

    name = "tasks"

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=5)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)

    def close(self) -> None:
        self.connection.close()

    def get(self, action_id: str) -> Action:
        row = self.connection.execute(
            """SELECT id, mode, status, instruction, workspace, result, decision, error_code, error_message,
            retry_of, cancel_requested_at, created_at, updated_at, started_at, completed_at
            FROM actions WHERE id = ?""",
            (action_id,),
        ).fetchone()
        if row is None:
            raise KeyError(action_id)
        public = dict(row)
        error_code, error_message = public.pop("error_code"), public.pop("error_message")
        public["error"] = {"code": error_code, "message": error_message} if error_code else None
        return Action.model_validate(public)

    def request(self, action_id: str) -> ActionRequest:
        row = self.connection.execute(
            "SELECT mode, instruction, input, workspace, checks FROM actions WHERE id = ?", (action_id,)
        ).fetchone()
        if row is None:
            raise KeyError(action_id)
        return ActionRequest.model_validate({
            **dict(row),
            "input": json.loads(row["input"]),
            "checks": json.loads(row["checks"]),
        })

    def create(
        self,
        request: ActionRequest,
        *,
        key: str | None = None,
        scope: str = "local",
        retry_of: str | None = None,
        event: tuple[str, dict] | None = None,
    ) -> tuple[Action, bool]:
        digest = hashlib.sha256(
            json.dumps({"request": request.model_dump(), "retry_of": retry_of}, sort_keys=True).encode()
        ).hexdigest()
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            if key is not None:
                row = self.connection.execute(
                    "SELECT id, request_hash FROM actions WHERE idempotency_scope = ? AND idempotency_key = ?",
                    (scope, key),
                ).fetchone()
                if row:
                    if row["request_hash"] != digest:
                        message = "The idempotency key was used with a different request."
                        raise ConflictError(message)
                    return self.get(row["id"]), False
            if retry_of and self.get(retry_of).status not in TERMINAL:
                message = "An active action cannot be retried."
                raise ConflictError(message)
            action_id, now = "act_" + uuid4().hex, utc_now()
            self.connection.execute(
                """INSERT INTO actions
                (id, mode, status, instruction, input, workspace, checks, retry_of,
                 idempotency_scope, idempotency_key, request_hash, created_at, updated_at)
                VALUES (?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    action_id,
                    request.mode,
                    request.instruction,
                    json.dumps([item.model_dump() for item in request.input]),
                    request.workspace,
                    json.dumps(request.checks),
                    retry_of,
                    scope,
                    key,
                    digest,
                    now,
                    now,
                ),
            )
            self._event(action_id, "action.queued", {})
            if event:
                self._event(action_id, *event)
        return self.get(action_id), True

    def list(self, limit: int = 50, cursor: str | None = None) -> builtins.list[Action]:
        if cursor:
            self.get(cursor)
        rows = self.connection.execute(
            """SELECT id FROM actions WHERE ? IS NULL OR (created_at, id) <
            (SELECT created_at, id FROM actions WHERE id = ?) ORDER BY created_at DESC, id DESC LIMIT ?""",
            (cursor, cursor, limit),
        ).fetchall()
        return [self.get(row["id"]) for row in rows]

    def claim(self, action_id: str) -> bool:
        now = utc_now()
        with self.connection:
            changed = self.connection.execute(
                "UPDATE actions SET status = 'running', started_at = ?, updated_at = ? WHERE id = ? AND status = 'queued'",
                (now, now, action_id),
            ).rowcount
            if changed:
                self._event(action_id, "action.running", {})
        return bool(changed)

    def next(self) -> str | None:
        row = self.connection.execute(
            "SELECT id FROM actions WHERE status = 'queued' ORDER BY created_at, id LIMIT 1"
        ).fetchone()
        return row["id"] if row else None

    def finish(
        self,
        action_id: str,
        status: Status,
        *,
        result: str | None = None,
        decision: Decision | None = None,
        error: dict[str, str] | None = None,
    ) -> Action:
        now = utc_now()
        with self.connection:
            changed = self.connection.execute(
                """UPDATE actions SET status = ?, result = COALESCE(?, result), decision = COALESCE(?, decision), error_code = ?, error_message = ?,
                completed_at = ?, updated_at = ? WHERE id = ? AND status IN ('queued', 'running')""",
                (
                    status,
                    result,
                    decision,
                    error["code"] if error else None,
                    error["message"] if error else None,
                    now,
                    now,
                    action_id,
                ),
            ).rowcount
            if changed:
                self._event(action_id, "action." + status, {"decision": decision, "error": error})
        return self.get(action_id)

    def output(self, action_id: str, text: str, decision: Decision | None = None) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE actions SET result = ?, decision = ?, updated_at = ? WHERE id = ? AND status = 'running'",
                (text, decision, utc_now(), action_id),
            )

    def cancel(self, action_id: str) -> Action:
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            action = self.get(action_id)
            if action.status in TERMINAL or action.cancel_requested_at:
                return action
            now = utc_now()
            status = "cancelled" if action.status == "queued" else action.status
            self.connection.execute(
                "UPDATE actions SET cancel_requested_at = ?, updated_at = ?, status = ?, completed_at = ? WHERE id = ?",
                (now, now, status, now if status == "cancelled" else None, action_id),
            )
            self._event(action_id, "action.cancellation_requested", {})
            if status == "cancelled":
                self._event(action_id, "action.cancelled", {})
        return self.get(action_id)

    def recover(self) -> None:
        rows = self.connection.execute("SELECT id FROM actions WHERE status = 'running'").fetchall()
        for row in rows:
            self.finish(
                row["id"],
                "interrupted",
                error={
                    "code": "interrupted",
                    "message": "The previous worker stopped. Inspect existing changes before retrying.",
                },
            )

    def event(self, action_id: str, kind: str, data: dict) -> None:
        with self.connection:
            self._event(action_id, kind, data)

    def _event(self, action_id: str, kind: str, data: dict) -> None:
        self.connection.execute(
            "INSERT INTO action_events (action_id, type, data, created_at) VALUES (?, ?, ?, ?)",
            (action_id, kind, json.dumps(data), utc_now()),
        )

    def events(self, action_id: str, after: int = 0, limit: int = 50) -> builtins.list[Event]:
        self.get(action_id)
        rows = self.connection.execute(
            "SELECT id, type, data, created_at FROM action_events WHERE action_id = ? AND id > ? ORDER BY id LIMIT ?",
            (action_id, after, limit),
        ).fetchall()
        return [Event.model_validate({**dict(row), "data": json.loads(row["data"])}) for row in rows]

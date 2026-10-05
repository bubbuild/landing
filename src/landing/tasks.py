"""Bub task sidecar: durable admission and task records in the tape database."""

from __future__ import annotations

import builtins
import hashlib
import json
from uuid import uuid4

from bub.tape import utc_now
from sqlalchemy import Connection, Engine

from landing.models import TERMINAL, Action, ActionRequest, Decision, Event, Status


class ConflictError(ValueError):
    """An existing request or action conflicts with the operation."""


class Tasks:
    """Sidecar provider owning the relational format, not a second execution engine."""

    name = "tasks"

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def get(self, action_id: str) -> Action:
        with self.engine.connect() as connection:
            return self._get(connection, action_id)

    def _get(self, connection: Connection, action_id: str) -> Action:
        row = (
            connection
            .exec_driver_sql(
                """SELECT id, mode, status, instruction, workspace, result, decision, error_code, error_message,
            retry_of, cancel_requested_at, created_at, updated_at, started_at, completed_at
            FROM actions WHERE id = ?""",
                (action_id,),
            )
            .mappings()
            .first()
        )
        if row is None:
            raise KeyError(action_id)
        public = dict(row)
        error_code, error_message = public.pop("error_code"), public.pop("error_message")
        public["error"] = {"code": error_code, "message": error_message} if error_code else None
        return Action.model_validate(public)

    def request(self, action_id: str) -> ActionRequest:
        with self.engine.connect() as connection:
            row = (
                connection
                .exec_driver_sql(
                    "SELECT mode, instruction, input, workspace, checks FROM actions WHERE id = ?", (action_id,)
                )
                .mappings()
                .first()
            )
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
        with self.engine.begin() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            if key is not None:
                row = (
                    connection
                    .exec_driver_sql(
                        "SELECT id, request_hash FROM actions WHERE idempotency_scope = ? AND idempotency_key = ?",
                        (scope, key),
                    )
                    .mappings()
                    .first()
                )
                if row:
                    if row["request_hash"] != digest:
                        message = "The idempotency key was used with a different request."
                        raise ConflictError(message)
                    return self._get(connection, row["id"]), False
            if retry_of and self._get(connection, retry_of).status not in TERMINAL:
                message = "An active action cannot be retried."
                raise ConflictError(message)
            action_id, now = "act_" + uuid4().hex, utc_now()
            connection.exec_driver_sql(
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
            self._event(connection, action_id, "action.queued", {})
            if event:
                self._event(connection, action_id, *event)
        return self.get(action_id), True

    def list(self, limit: int = 50, cursor: str | None = None) -> builtins.list[Action]:
        with self.engine.connect() as connection:
            if cursor:
                self._get(connection, cursor)
            rows = (
                connection
                .exec_driver_sql(
                    """SELECT id FROM actions WHERE ? IS NULL OR (created_at, id) <
                (SELECT created_at, id FROM actions WHERE id = ?) ORDER BY created_at DESC, id DESC LIMIT ?""",
                    (cursor, cursor, limit),
                )
                .mappings()
                .all()
            )
            return [self._get(connection, row["id"]) for row in rows]

    def claim(self, action_id: str) -> bool:
        now = utc_now()
        with self.engine.begin() as connection:
            changed = connection.exec_driver_sql(
                "UPDATE actions SET status = 'running', started_at = ?, updated_at = ? WHERE id = ? AND status = 'queued'",
                (now, now, action_id),
            ).rowcount
            if changed:
                self._event(connection, action_id, "action.running", {})
        return bool(changed)

    def next(self) -> str | None:
        with self.engine.connect() as connection:
            row = (
                connection
                .exec_driver_sql("SELECT id FROM actions WHERE status = 'queued' ORDER BY created_at, id LIMIT 1")
                .mappings()
                .first()
            )
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
        with self.engine.begin() as connection:
            changed = connection.exec_driver_sql(
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
                self._event(connection, action_id, "action." + status, {"decision": decision, "error": error})
        return self.get(action_id)

    def output(self, action_id: str, text: str, decision: Decision | None = None) -> None:
        with self.engine.begin() as connection:
            connection.exec_driver_sql(
                "UPDATE actions SET result = ?, decision = ?, updated_at = ? WHERE id = ? AND status = 'running'",
                (text, decision, utc_now(), action_id),
            )

    def cancel(self, action_id: str) -> Action:
        with self.engine.begin() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            action = self._get(connection, action_id)
            if action.status in TERMINAL or action.cancel_requested_at:
                return action
            now = utc_now()
            status = "cancelled" if action.status == "queued" else action.status
            connection.exec_driver_sql(
                "UPDATE actions SET cancel_requested_at = ?, updated_at = ?, status = ?, completed_at = ? WHERE id = ?",
                (now, now, status, now if status == "cancelled" else None, action_id),
            )
            self._event(connection, action_id, "action.cancellation_requested", {})
            if status == "cancelled":
                self._event(connection, action_id, "action.cancelled", {})
        return self.get(action_id)

    def recover(self) -> None:
        with self.engine.connect() as connection:
            rows = connection.exec_driver_sql("SELECT id FROM actions WHERE status = 'running'").mappings().all()
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
        with self.engine.begin() as connection:
            self._event(connection, action_id, kind, data)

    def _event(self, connection: Connection, action_id: str, kind: str, data: dict) -> None:
        connection.exec_driver_sql(
            "INSERT INTO action_events (action_id, type, data, created_at) VALUES (?, ?, ?, ?)",
            (action_id, kind, json.dumps(data), utc_now()),
        )

    def events(self, action_id: str, after: int = 0, limit: int = 50) -> builtins.list[Event]:
        with self.engine.connect() as connection:
            self._get(connection, action_id)
            rows = (
                connection
                .exec_driver_sql(
                    "SELECT id, type, data, created_at FROM action_events WHERE action_id = ? AND id > ? ORDER BY id LIMIT ?",
                    (action_id, after, limit),
                )
                .mappings()
                .all()
            )
            return [Event.model_validate({**dict(row), "data": json.loads(row["data"])}) for row in rows]

    def find(self, scope: str, key: str) -> Action | None:
        with self.engine.connect() as connection:
            action_id = connection.exec_driver_sql(
                "SELECT id FROM actions WHERE idempotency_scope = ? AND idempotency_key = ?", (scope, key)
            ).scalar()
            return self._get(connection, action_id) if action_id else None

    def event_data(self, action_id: str, kind: str) -> dict | None:
        with self.engine.connect() as connection:
            data = connection.exec_driver_sql(
                "SELECT data FROM action_events WHERE action_id = ? AND type = ? ORDER BY id DESC LIMIT 1",
                (action_id, kind),
            ).scalar()
            return json.loads(data) if data is not None else None

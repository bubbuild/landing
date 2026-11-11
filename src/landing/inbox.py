"""Persistence for local intake; task execution and results belong to Runtime."""

import json

from sqlalchemy import Engine


class Inbox:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def submit(self, message: dict) -> int:
        with self.engine.begin() as connection:
            return connection.exec_driver_sql(
                "INSERT INTO inbox (message) VALUES (?)", (json.dumps(message),)
            ).lastrowid

    def next(self) -> int | None:
        with self.engine.connect() as connection:
            return connection.exec_driver_sql("SELECT id FROM inbox WHERE error IS NULL ORDER BY id LIMIT 1").scalar()

    def get(self, event_id: int) -> dict:
        with self.engine.connect() as connection:
            return json.loads(
                connection.exec_driver_sql("SELECT message FROM inbox WHERE id = ?", (event_id,)).scalar_one()
            )

    def finish(self, event_id: int, error: str | None = None) -> None:
        with self.engine.begin() as connection:
            if error is None:
                connection.exec_driver_sql("DELETE FROM inbox WHERE id = ?", (event_id,))
            else:
                connection.exec_driver_sql("UPDATE inbox SET error = ? WHERE id = ?", (error, event_id))

from contextlib import closing

import pytest
from bub.store import TapeQuery
from bub.tape import TapeEntry

from landing.models import ActionRequest
from landing.store import SQLiteTapeStore
from landing.tasks import ConflictError, Tasks


def test_atomic_idempotent_admission_and_explicit_retry(tmp_path):
    path = tmp_path / "landing.sqlite3"
    request = ActionRequest(mode="issuer", instruction="Identify the problem.")
    with closing(Tasks(path)) as tasks:
        action, created = tasks.create(request, key="delivery-1", scope="webhook")
        assert created
        assert tasks.create(request, key="delivery-1", scope="webhook") == (action, False)
        with pytest.raises(ConflictError, match="different request"):
            tasks.create(
                ActionRequest(mode="issuer", instruction="Another problem."), key="delivery-1", scope="webhook"
            )
        with pytest.raises(ConflictError, match="active action"):
            tasks.create(request, retry_of=action.id)
        assert len(tasks.list()) == 1
        assert len(tasks.events(action.id)) == 1
        assert tasks.claim(action.id)
        assert not tasks.claim(action.id)
        tasks.finish(action.id, "failed", error={"code": "example", "message": "The model failed."})
        retry, _ = tasks.create(request, retry_of=action.id)
        assert retry.id != action.id
        assert retry.retry_of == action.id
        assert tasks.request(retry.id) == request
    with closing(Tasks(path)) as tasks:
        assert len(tasks.list()) == 2
        assert tasks.list(limit=1, cursor=retry.id)[0].id == action.id


def test_queued_cancellation_is_terminal_and_cannot_be_claimed(tmp_path):
    with closing(Tasks(tmp_path / "landing.sqlite3")) as tasks:
        action, _ = tasks.create(ActionRequest(mode="explainer", instruction="Explain the failure."))
        cancelled = tasks.cancel(action.id)
        assert cancelled.status == "cancelled"
        assert cancelled.completed_at
        assert not tasks.claim(action.id)
        assert tasks.cancel(action.id) == cancelled
        assert tasks.next() is None


def test_sqlite_store_reuses_bub_queries_and_preserves_task_records(tmp_path):
    path = tmp_path / "landing.sqlite3"
    with closing(Tasks(path)) as tasks, closing(SQLiteTapeStore(path)) as store:
        action, _ = tasks.create(ActionRequest(mode="explainer", instruction="Explain the failure."))
        store.append("model", TapeEntry.message({"role": "user", "content": "Before the anchor."}))
        store.append("model", TapeEntry.anchor("review"))
        store.append("model", TapeEntry.message({"role": "assistant", "content": "Evidence after the anchor."}))
        query = TapeQuery(tape="model", store=store).after_anchor("review").kinds("message")
        entries = list(query.all())
        assert len(entries) == 1
        assert entries[0].payload["content"] == "Evidence after the anchor."
        assert entries[0].id > 0
        store.reset("model")
        assert store.read("model") is None
        assert tasks.get(action.id).status == "queued"

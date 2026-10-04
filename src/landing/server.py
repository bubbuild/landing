"""Resource HTTP API and webhook admission over the same Bub runtime."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import sqlite3
from collections.abc import Iterable, Mapping
from contextlib import asynccontextmanager
from importlib.metadata import version
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.exception_handlers import http_exception_handler
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from scalar_fastapi import AgentScalarConfig, add_scalar_reference
from starlette.datastructures import URL

from landing.adapters.github import repository_context
from landing.models import MAX_REQUEST_BYTES, TERMINAL, Action, ActionRequest, Event
from landing.runtime import Runtime
from landing.tasks import ConflictError


def create_app(  # noqa: C901 -- route definitions share an application lifespan.
    path: Path,
    *,
    workspaces: Mapping[str, Path] | None = None,
    token: str | None = None,
    base_url: str | None = None,
    github_repository: str | None = None,
    skill_dirs: Iterable[Path] = (),
) -> FastAPI:
    public_url = URL(base_url) if base_url else None
    if public_url and (
        public_url.scheme not in {"http", "https"}
        or not public_url.hostname
        or public_url.username is not None
        or public_url.query
        or public_url.fragment
        or public_url.path not in {"", "/"}
    ):
        message = "BASE_URL must be an HTTP(S) origin without credentials, a path, query, or fragment."
        raise ValueError(message)

    @asynccontextmanager
    async def lifespan(app: FastAPI):

        runtime = Runtime(
            path,
            workspaces=workspaces or {"default": Path.cwd()},
            skill_dirs=skill_dirs,
        )
        async with runtime.running():
            app.state.runtime = runtime
            worker = asyncio.create_task(runtime.worker())
            app.state.worker = worker
            try:
                yield
            finally:
                worker.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await worker

    app = FastAPI(title="Landing", version=version("landing"), lifespan=lifespan, docs_url=None, redoc_url=None)
    add_scalar_reference(app, route="/docs", telemetry=False, agent=AgentScalarConfig(disabled=True))

    async def authenticate(credentials: Annotated[HTTPAuthorizationCredentials, Depends(HTTPBearer())]):
        if token is None or not hmac.compare_digest(credentials.credentials.encode(), token.encode()):
            raise HTTPException(401, "A valid bearer token is required.", headers={"WWW-Authenticate": "Bearer"})

    api = APIRouter(dependencies=[Depends(authenticate)] if token else [])

    @app.middleware("http")
    async def limit_request_size(request: Request, call_next):
        if len(await request.body()) > MAX_REQUEST_BYTES:
            return await http_exception_handler(request, HTTPException(413, "The request exceeds 16 MiB."))
        return await call_next(request)

    @app.exception_handler(KeyError)
    async def not_found(request, exc):
        return await http_exception_handler(request, HTTPException(404, "The action was not found."))

    @app.exception_handler(ConflictError)
    async def conflict(request, exc):
        return await http_exception_handler(request, HTTPException(409, str(exc)))

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return await http_exception_handler(request, HTTPException(422, str(exc)))

    @app.exception_handler(sqlite3.Error)
    async def storage_error(request, exc):
        return await http_exception_handler(request, HTTPException(503, "The database is unavailable."))

    def accept(
        runtime: Runtime, body: ActionRequest, response: Response, key: str | None, retry_of: str | None = None
    ) -> Action:
        if app.state.worker.done():
            raise HTTPException(503, "The worker is unavailable.")
        runtime.workspace(body)
        if github_repository:
            context = repository_context(github_repository)
            if context not in body.input:
                body = body.model_copy(update={"input": [*body.input, context]})
        action, created = runtime.tasks.create(body, key=key, scope="server", retry_of=retry_of)
        response.status_code = 201 if created else 200
        response.headers["Location"] = f"/v1/actions/{action.id}"
        return action

    def next_link(request: Request, **params) -> str:
        url = request.url.include_query_params(**params)
        if public_url:
            url = url.replace(scheme=public_url.scheme, netloc=public_url.netloc)
        return f'<{url}>; rel="next"'

    @api.post("/v1/actions", response_model=Action, status_code=201)
    async def create(
        body: ActionRequest,
        request: Request,
        response: Response,
        idempotency_key: Annotated[str | None, Header(min_length=1, max_length=256)] = None,
    ):
        return accept(request.app.state.runtime, body, response, idempotency_key)

    @api.get("/v1/actions", response_model=list[Action])
    async def list_actions(
        request: Request, response: Response, limit: Annotated[int, Query(ge=1, le=100)] = 50, cursor: str | None = None
    ):
        tasks = request.app.state.runtime.tasks
        items = tasks.list(limit + 1, cursor)
        if len(items) > limit:
            response.headers["Link"] = next_link(request, cursor=items[limit - 1].id, limit=limit)
        return items[:limit]

    @api.get("/v1/actions/{action_id}", response_model=Action)
    async def view(action_id: str, request: Request):
        return request.app.state.runtime.tasks.get(action_id)

    @api.get("/v1/actions/{action_id}/events", response_model=list[Event])
    async def events(
        action_id: str,
        request: Request,
        response: Response,
        after: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ):
        items = request.app.state.runtime.tasks.events(action_id, after, limit + 1)
        if len(items) > limit:
            response.headers["Link"] = next_link(request, after=items[limit - 1].id, limit=limit)
        return items[:limit]

    @api.post("/v1/actions/{action_id}/cancellation", response_model=Action, status_code=202)
    async def cancel(action_id: str, request: Request, response: Response):
        action = request.app.state.runtime.cancel(action_id)
        response.status_code = 200 if action.status in TERMINAL else 202
        return action

    @api.post("/v1/actions/{action_id}/retries", response_model=Action, status_code=201)
    async def retry(
        action_id: str,
        request: Request,
        response: Response,
        idempotency_key: Annotated[str | None, Header(min_length=1, max_length=256)] = None,
    ):
        runtime = request.app.state.runtime
        return accept(runtime, runtime.tasks.request(action_id), response, idempotency_key, action_id)

    app.include_router(api)

    @app.get("/healthz")
    async def health():
        return {"status": "ok"}

    @app.get("/up", include_in_schema=False)
    async def ready():
        if app.state.worker.done():
            raise HTTPException(503, "The worker is unavailable.")
        app.state.runtime.tasks.connection.execute("SELECT id FROM actions LIMIT 1").fetchone()
        return {"status": "ok"}

    return app

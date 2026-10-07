"""Resource HTTP API and webhook admission over the same Bub runtime."""

from __future__ import annotations

import hmac
from collections.abc import Iterable, Mapping
from importlib.metadata import version
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.exception_handlers import http_exception_handler
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from scalar_fastapi import AgentScalarConfig, add_scalar_reference
from sqlalchemy.exc import SQLAlchemyError
from starlette.datastructures import URL, Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from landing.models import MAX_REQUEST_BYTES, TERMINAL, Action, ActionRequest, Event
from landing.runtime import Runtime
from landing.tasks import ConflictError


class RequestLimit:
    """Stop reading a request body at the admission limit instead of buffering it first."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = Headers(scope=scope).get("content-length", "")
        received = 0

        async def limited() -> Message:
            nonlocal received
            if declared.isdigit() and int(declared) > MAX_REQUEST_BYTES:
                raise HTTPException(413, "The request exceeds 16 MiB.")
            message = await receive()
            received += len(message.get("body", b""))
            # FastAPI re-raises HTTPException from body parsing, so the route's handlers answer it.
            if received > MAX_REQUEST_BYTES:
                raise HTTPException(413, "The request exceeds 16 MiB.")
            return message

        await self.app(scope, limited, send)


def create_app(  # noqa: C901 -- route definitions share an application lifespan.
    path: Path | Runtime,
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

    runtime = (
        path
        if isinstance(path, Runtime)
        else Runtime(path, workspaces=workspaces or {"default": Path.cwd()}, skill_dirs=skill_dirs)
    )
    app = FastAPI(title="Landing", version=version("landing"), lifespan=runtime.lifespan, docs_url=None, redoc_url=None)
    add_scalar_reference(app, route="/docs", telemetry=False, agent=AgentScalarConfig(disabled=True))

    async def authenticate(credentials: Annotated[HTTPAuthorizationCredentials, Depends(HTTPBearer())]):
        if token is None or not hmac.compare_digest(credentials.credentials.encode(), token.encode()):
            raise HTTPException(401, "A valid bearer token is required.", headers={"WWW-Authenticate": "Bearer"})

    api = APIRouter(dependencies=[Depends(authenticate)] if token else [])

    app.add_middleware(RequestLimit)

    @app.exception_handler(KeyError)
    async def not_found(request, exc):
        return await http_exception_handler(request, HTTPException(404, "The action was not found."))

    @app.exception_handler(ConflictError)
    async def conflict(request, exc):
        return await http_exception_handler(request, HTTPException(409, str(exc)))

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return await http_exception_handler(request, HTTPException(422, str(exc)))

    @app.exception_handler(SQLAlchemyError)
    async def storage_error(request, exc):
        return await http_exception_handler(request, HTTPException(503, "The database is unavailable."))

    def accept(body: ActionRequest, response: Response, key: str | None, retry_of: str | None = None) -> Action:
        if github_repository:
            from landing.adapters.github import repository_context

            context = repository_context(github_repository)
            if context not in body.input:
                body = body.model_copy(update={"input": [*body.input, context]})
        try:
            action, created = runtime.submit(body, key=key, scope="server", retry_of=retry_of)
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
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
        response: Response,
        idempotency_key: Annotated[str | None, Header(min_length=1, max_length=256)] = None,
    ):
        return accept(body, response, idempotency_key)

    @api.get("/v1/actions", response_model=list[Action])
    async def list_actions(
        request: Request, response: Response, limit: Annotated[int, Query(ge=1, le=100)] = 50, cursor: str | None = None
    ):
        tasks = runtime.tasks
        items = tasks.list(limit + 1, cursor)
        if len(items) > limit:
            response.headers["Link"] = next_link(request, cursor=items[limit - 1].id, limit=limit)
        return items[:limit]

    @api.get("/v1/actions/{action_id}", response_model=Action)
    async def view(action_id: str):
        return runtime.tasks.get(action_id)

    @api.get("/v1/actions/{action_id}/events", response_model=list[Event])
    async def events(
        action_id: str,
        request: Request,
        response: Response,
        after: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ):
        items = runtime.tasks.events(action_id, after, limit + 1)
        if len(items) > limit:
            response.headers["Link"] = next_link(request, after=items[limit - 1].id, limit=limit)
        return items[:limit]

    @api.post("/v1/actions/{action_id}/cancellation", response_model=Action, status_code=202)
    async def cancel(action_id: str, response: Response):
        action = runtime.cancel(action_id)
        response.status_code = 200 if action.status in TERMINAL else 202
        return action

    @api.post("/v1/actions/{action_id}/retries", response_model=Action, status_code=201)
    async def retry(
        action_id: str,
        response: Response,
        idempotency_key: Annotated[str | None, Header(min_length=1, max_length=256)] = None,
    ):
        return accept(runtime.tasks.request(action_id), response, idempotency_key, action_id)

    app.include_router(api)

    @app.get("/healthz")
    async def health():
        return {"status": "ok"}

    @app.get("/up", include_in_schema=False)
    async def ready():
        worker = runtime.worker_task
        if worker is None or worker.done() or worker.cancelling():
            raise HTTPException(503, "The worker is unavailable.")
        runtime.tasks.list(limit=1)
        return {"status": "ok"}

    return app

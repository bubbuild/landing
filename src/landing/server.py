"""Webhook admission with local and GitHub Actions execution locations."""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from subprocess import SubprocessError
from typing import Annotated, Literal

from bub import BubFramework
from fastapi import FastAPI, Header, HTTPException, Request, Response
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.exc import SQLAlchemyError

from landing.adapters.github.webhook import WebhookSettings, dispatch, read_webhook, supported_event
from landing.local import LocalRunner
from landing.settings import DEFAULT_DATABASE, ConfigurationFile


class ServerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LANDING_", env_ignore_empty=True)
    runner: Literal["local", "gha"] = "local"
    workspace: Path = Field(default_factory=Path.cwd)
    db: Path = DEFAULT_DATABASE


def create_app(  # noqa: C901 -- routes share the selected execution lifespan.
    *, options: ServerSettings | None = None, skill_dirs: tuple[Path, ...] = ()
) -> FastAPI:
    options, settings = options or ServerSettings(), WebhookSettings()
    local = None
    framework = None
    # A GHA relay only verifies and dispatches; execution resources belong to the runner it starts.
    if options.runner == "local":
        framework = BubFramework(config_file=ConfigurationFile().config_file.expanduser())
        local = LocalRunner(framework, options.db, options.workspace, skill_dirs)
        framework.plugin_manager.register(local)

    @asynccontextmanager
    async def lifespan(app):
        if framework is None:
            yield
            return
        async with framework.running():
            yield

    app = FastAPI(title="Landing", openapi_url=None, lifespan=lifespan)

    @app.post("/webhooks/github", status_code=202)
    async def webhook(
        request: Request,
        x_github_delivery: Annotated[str, Header()],
        x_github_event: Annotated[str, Header()],
        x_hub_signature_256: Annotated[str, Header()],
    ):
        body = await request.body()
        try:
            event = read_webhook(body, x_hub_signature_256, settings.webhook_secret)
            if not supported_event(x_github_event, event, settings.workflow, settings.command_prefix):
                return Response(status_code=204)
            inputs = {
                "event": body.decode(),
                "signature": x_hub_signature_256,
                "event_name": x_github_event,
                "delivery": x_github_delivery,
            }
            if local is not None:
                return {"queued": local.submit({"inputs": inputs})}
            return await asyncio.to_thread(dispatch, settings, inputs, event)
        except PermissionError as exc:
            raise HTTPException(401, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except (OSError, RuntimeError, SubprocessError, SQLAlchemyError) as exc:
            logging.getLogger(__name__).exception("Cannot accept GitHub delivery %s", x_github_delivery)
            raise HTTPException(
                503 if local is not None else 502, "Cannot accept work; inspect receiver logs before redelivery."
            ) from exc

    @app.get("/up")
    async def health():
        if local is not None and (local.worker is None or local.worker.done()):
            raise HTTPException(503, "The local worker is unavailable.")
        return {"status": "ok"}

    return app

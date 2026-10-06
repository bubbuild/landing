"""Typer commands for execution, inspection, and hosting."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import shlex
import sys
import time
from contextlib import nullcontext
from importlib.metadata import version
from pathlib import Path
from typing import Annotated, cast

import typer
from bub import BubFramework
from pydantic import AliasChoices, Field
from pydantic_settings import SettingsConfigDict
from typer.core import TyperCommand

from landing.commands import COMMANDS
from landing.database import open_database, own_database
from landing.hooks import LandingHooks
from landing.models import TERMINAL, Action, ActionRequest, FileInput, Input
from landing.runtime import Runtime
from landing.settings import ConfigurationFile, FileSettings
from landing.tasks import Tasks

DEFAULT_DATABASE = Path("~/.local/share/landing/landing.sqlite3")


class ExecutionSettings(FileSettings):
    """CLI and service configuration, independent of SDK model settings."""

    model_config = SettingsConfigDict(
        env_prefix="LANDING_", env_ignore_empty=True, populate_by_name=True, extra="ignore", hide_input_in_errors=True
    )
    db: Path | None = None
    token: str | None = Field(default=None, repr=False)
    github_repository: str | None = None
    base_url: str | None = Field(default=None, validation_alias=AliasChoices("LANDING_BASE_URL", "BASE_URL"))
    replicate: bool = False


JsonOutput = Annotated[bool, typer.Option("--json", help="Print records as JSON.")]
Limit = Annotated[int, typer.Option(min=1, max=100, help="Maximum records to return.")]
ActionId = Annotated[str, typer.Argument(metavar="ID")]
actions = typer.Typer(help="Inspect and control recorded actions.", no_args_is_help=True)


class Command(TyperCommand):
    """Present execution errors using Typer's parsed command context."""

    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except typer.Exit:
            raise
        except (ValueError, OSError, KeyError, RuntimeError) as exc:
            code = 1 if isinstance(exc, RuntimeError) else 2
            message = str(exc)
            if code == 2 and ctx.params.get("json_output", False):
                typer.echo(json.dumps({"error": {"code": "invalid_request", "message": message}}))
            typer.echo(message, err=True)
            raise typer.Exit(code) from exc


def execution_settings(ctx: typer.Context) -> ExecutionSettings:
    parameters = ctx.find_root().params
    return ExecutionSettings(**{name: value for name, value in parameters.items() if value is not None})


def host_runtime(ctx: typer.Context) -> Runtime | None:
    if (framework := ctx.find_object(BubFramework)) and isinstance(
        hooks := framework.plugin_manager.get_plugin("builtin"), LandingHooks
    ):
        return cast(Runtime | None, hooks._agent)
    return None


def execution_runtime(ctx: typer.Context, settings: ExecutionSettings, *, database: Path | None = None) -> Runtime:
    return host_runtime(ctx) or Runtime(
        database or settings.db or DEFAULT_DATABASE,
        skill_dirs=ctx.find_root().params.get("skill_dir") or (),
        framework=ctx.find_object(BubFramework),
    )


def show_version(value: bool) -> None:
    if value:
        typer.echo(version("landing"))
        raise typer.Exit()


def configure(
    db: Annotated[Path | None, typer.Option(help="Local SQLite path; overrides LANDING_DB.")] = None,
    skill_dir: Annotated[list[Path] | None, typer.Option(help="Additional trusted skill root (repeatable).")] = None,
    github_repository: Annotated[str | None, typer.Option(help="Repository context for the prepared gh CLI.")] = None,
    version: Annotated[
        bool,
        typer.Option("--version", callback=show_version, is_eager=True, help="Print the installed version and exit."),
    ] = False,
) -> None:
    """Configure this invocation without starting execution resources."""


def create_cli_app() -> typer.Typer:
    framework = BubFramework(config_file=ConfigurationFile().config_file.expanduser())
    framework.plugin_manager.register(LandingHooks(framework), name="builtin")
    app = typer.Typer(
        name="landing",
        help="Explain CI failures, delegate fixes, and review evidence.",
        callback=configure,
        no_args_is_help=True,
        rich_markup_mode=None,
        context_settings={"obj": framework, "help_option_names": ["-h", "--help"]},
    )
    framework.plugin_manager.hook.register_cli_commands(app=app)
    return app


def display(value, *, json_output: bool = False, output: Path | None = None) -> None:
    if json_output:
        data = [item.model_dump() for item in value] if isinstance(value, list) else value.model_dump()
        text = json.dumps(data, indent=2)
    elif isinstance(value, list):
        text = "ID\tMODE\tSTATUS\tUPDATED\tINSTRUCTION\n" + "\n".join(
            f"{item.id}\t{item.mode}\t{item.status}\t{item.updated_at}\t{(item.instruction or '')[:60]}"
            for item in value
        )
    else:
        text = value.result or f"{value.id}: {value.status}"
        if value.error:
            text += "\n" + value.error["message"]
    typer.echo(text)
    if output:
        output.write_text(text + "\n", encoding="utf-8")


async def delegate_action(ctx: typer.Context, request: ActionRequest | str) -> Action:
    settings = execution_settings(ctx)
    runtime = execution_runtime(ctx, settings)
    async with runtime.running():
        retry_of = None
        if isinstance(request, str):
            retry_of = request
            request = runtime.tasks.request(request)
        request = request.model_copy(update={"workspace": runtime.workspace_name(runtime.workspace(request.workspace))})
        if repository := settings.github_repository:
            from landing.adapters.github import repository_context

            context = repository_context(repository)
            if context not in request.input:
                request = request.model_copy(update={"input": [*request.input, context]})
        if retry_of:
            return await runtime.run(request, retry_of=retry_of)
        return await runtime.command(cast(str, ctx.info_name), request)


def delegate(
    ctx: typer.Context,
    instruction: Annotated[str | None, typer.Argument(help="Work to delegate; alternatively supply --input.")] = None,
    input_files: Annotated[
        list[str] | None, typer.Option("--input", metavar="FILE", help="UTF-8 evidence; - reads stdin.")
    ] = None,
    workspace: Annotated[str | None, typer.Option(help="Execution directory.")] = None,
    check: Annotated[
        list[str] | None, typer.Option(metavar="COMMAND", help="Required validation (repeatable).")
    ] = None,
    json_output: JsonOutput = False,
    output: Annotated[Path | None, typer.Option(help="Also write the result to this file.")] = None,
) -> None:
    if (input_files or []).count("-") > 1:
        message = "stdin can only be used once."
        raise ValueError(message)
    inputs: list[Input] = [
        FileInput(
            name="stdin" if name == "-" else Path(name).name,
            media_type=mimetypes.guess_type(name)[0] or "text/plain",
            content=sys.stdin.read() if name == "-" else Path(name).read_text(encoding="utf-8"),
        )
        for name in input_files or []
    ]
    request = ActionRequest(
        mode=COMMANDS[cast(str, ctx.info_name)],
        instruction=instruction,
        input=inputs,
        workspace=workspace,
        checks=check or [],
    )
    action = asyncio.run(delegate_action(ctx, request))
    display(action, json_output=json_output, output=output)
    raise typer.Exit(action.exit_code())


def action_database(ctx: typer.Context) -> Path:
    if runtime := host_runtime(ctx):
        return runtime.path
    return (execution_settings(ctx).db or DEFAULT_DATABASE).expanduser()


@actions.command("list", cls=Command)
def list_actions(
    ctx: typer.Context, limit: Limit = 50, cursor: str | None = None, json_output: JsonOutput = False
) -> None:
    with open_database(action_database(ctx)) as engine:
        display(Tasks(engine).list(limit, cursor), json_output=json_output)


@actions.command(cls=Command)
def logs(
    ctx: typer.Context,
    action_id: ActionId,
    after: Annotated[int, typer.Option(min=0, help="Return events after this cursor.")] = 0,
    limit: Limit = 50,
    json_output: JsonOutput = False,
) -> None:
    with open_database(action_database(ctx)) as engine:
        display(Tasks(engine).events(action_id, after, limit), json_output=True)


def inspect_action(ctx: typer.Context, action_id: ActionId, json_output: JsonOutput = False) -> None:
    with (
        own_database(action_database(ctx)) if ctx.info_name == "cancel" else nullcontext(),
        open_database(action_database(ctx)) as engine,
    ):
        tasks = Tasks(engine)
        action = tasks.get(action_id)
        if ctx.info_name == "cancel":
            if action.status == "running":
                message = "This action needs recovery by its executing host."
                raise ValueError(message)
            action = tasks.cancel(action_id)
        display(action, json_output=json_output)


actions.command("view", cls=Command, help="Read an action's status and result.")(inspect_action)
actions.command("cancel", cls=Command, help="Cancel queued work while its host is stopped.")(inspect_action)


@actions.command(cls=Command)
def watch(
    ctx: typer.Context,
    action_id: ActionId,
    exit_status: Annotated[bool, typer.Option(help="Use the action's completion and gate exit code.")] = False,
    json_output: JsonOutput = False,
) -> None:
    """Wait for an action to finish."""
    with open_database(action_database(ctx)) as engine:
        tasks = Tasks(engine)
        while (action := tasks.get(action_id)).status not in TERMINAL:
            time.sleep(1)
        display(action, json_output=json_output)
        raise typer.Exit(action.exit_code() if exit_status else 0)


@actions.command(cls=Command)
def retry(ctx: typer.Context, action_id: ActionId, json_output: JsonOutput = False) -> None:
    """Retry a terminal action using its original request."""
    action = asyncio.run(delegate_action(ctx, action_id))
    display(action, json_output=json_output)
    raise typer.Exit(action.exit_code())


def serve(
    ctx: typer.Context,
    host: Annotated[str, typer.Option(help="Listen address.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(min=1, max=65535, help="Listen port.")] = 8080,
    workspace: Annotated[
        list[str] | None, typer.Option(metavar="NAME=PATH", help="Register a workspace (repeatable).")
    ] = None,
) -> None:
    """Run the HTTP service with registered workspaces."""
    import uvicorn

    from landing.server import create_app

    settings = execution_settings(ctx)
    if host not in {"127.0.0.1", "localhost", "::1"} and not settings.token:
        message = "Set LANDING_TOKEN before listening beyond localhost."
        raise ValueError(message)
    embedded = host_runtime(ctx)
    runtime = embedded or execution_runtime(ctx, settings)
    workspaces = dict(runtime.workspaces or {"default": runtime.framework.workspace})
    for value in workspace or []:
        name, separator, path = value.partition("=")
        if not separator or not name or not path:
            message = "Register a workspace as NAME=PATH."
            raise ValueError(message)
        workspaces[name] = Path(path).expanduser().resolve()
    runtime.workspaces = workspaces
    app = create_app(
        runtime,
        token=settings.token,
        base_url=settings.base_url,
        github_repository=settings.github_repository,
    )
    if settings.replicate:
        if embedded:
            message = "Use an external supervisor for an existing SDK host."
            raise ValueError(message)
        command = [sys.executable, "-m", "landing", "--github-repository", settings.github_repository or ""]
        command.extend(["serve", "--host", host, "--port", str(port)])
        for name, path in workspaces.items():
            command.extend(["--workspace", f"{name}={path}"])
        os.execvpe(  # noqa: S606 -- replace the CLI with its native process supervisor.
            "litestream",  # noqa: S607 -- use Litestream from the prepared host's PATH.
            ["litestream", "replicate", "-restore-if-db-not-exists", "-exec", shlex.join(command)],
            {
                **os.environ,
                "LANDING_REPLICATE": "false",
                "LANDING_DB": str(runtime.path),
                "LANDING_SKILL_DIRS": json.dumps([str(root) for root in runtime.skill_roots]),
            },
        )
    uvicorn.run(app, host=host, port=port)


def main(argv: list[str] | None = None) -> int:
    try:
        create_cli_app()(args=argv, prog_name="landing")
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0

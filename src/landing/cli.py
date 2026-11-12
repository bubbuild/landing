"""Typer commands for execution, inspection, and hosting."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import sys
import time
from importlib.metadata import version
from pathlib import Path
from typing import Annotated, Literal, cast
from uuid import uuid4

import typer
from bub import Settings as BubSettings
from bub import config, ensure_config
from pydantic_settings import SettingsConfigDict
from typer.core import TyperCommand

from landing.adapters.github.events import Ingress, NativeEvent
from landing.adapters.github.execution import execute as execute_github
from landing.adapters.github.execution import with_repository_context
from landing.adapters.github.settings import GitHubEnvironment, GitHubSettings
from landing.adapters.github.webhook import claims_forwarded_delivery, forwarded_inputs, read_forwarded
from landing.database import open_database, own_database
from landing.hooks import TerminationHooks
from landing.models import COMMANDS, TERMINAL, Action, ActionRequest, FileInput, Input
from landing.runtime import Runtime
from landing.settings import DEFAULT_DATABASE, load_configuration
from landing.tasks import NotFoundError, Tasks


@config()
class ExecutionSettings(BubSettings):
    """CLI and service configuration, independent of SDK model settings."""

    model_config = SettingsConfigDict(
        env_prefix="LANDING_",
        env_ignore_empty=True,
        populate_by_name=True,
        extra="ignore",
        hide_input_in_errors=True,
        validate_assignment=True,
    )
    db: Path | None = None
    github_repository: str | None = None


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
        except (ValueError, OSError, NotFoundError, RuntimeError) as exc:
            code = 1 if isinstance(exc, RuntimeError) else 2
            message = str(exc)
            if code == 2 and ctx.params.get("json_output", False):
                typer.echo(json.dumps({"error": {"code": "invalid_request", "message": message}}))
            typer.echo(message, err=True)
            raise typer.Exit(code) from exc


def execution_settings(ctx: typer.Context) -> ExecutionSettings:
    load_configuration()
    settings = ensure_config(ExecutionSettings)
    for name, value in ctx.find_root().params.items():
        if name in ExecutionSettings.model_fields and value is not None:
            setattr(settings, name, value)
    return settings


def execution_runtime(ctx: typer.Context, settings: ExecutionSettings, *, database: Path | None = None) -> Runtime:
    return Runtime(
        database or settings.db or DEFAULT_DATABASE,
        skill_dirs=ctx.find_root().params.get("skill_dir") or (),
        plugins=[TerminationHooks()],
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
    app = typer.Typer(
        name="landing",
        help="Explain CI failures, delegate fixes, and review evidence.",
        callback=configure,
        no_args_is_help=True,
        rich_markup_mode=None,
        context_settings={"help_option_names": ["-h", "--help"]},
    )
    for name, help_text in {
        "triage": "Identify a problem and its acceptance criteria.",
        "fix": "Repair the delegated problem and validate changes.",
        "bump": "Update the requested dependencies and validate changes.",
        "review": "Evaluate a candidate against independent evidence.",
        "explain": "Explain the supplied question or evidence.",
    }.items():
        app.command(name=name, cls=Command, help=help_text)(delegate)
    app.add_typer(actions, name="action")
    app.command(cls=Command)(serve)
    github = typer.Typer(help="Handle GitHub events with prepared credentials.", no_args_is_help=True)
    github.command("event", cls=Command)(github_event)
    app.add_typer(github, name="github")
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
        request = request.model_copy(update={"workspace": str(runtime.workspace(request.workspace))})
        if repository := settings.github_repository:
            request = with_repository_context(request, repository)
        # Each delegation uses its own action session; unrelated CLI calls must not share model history.
        return await runtime.run(request, retry_of=retry_of, scope="cli")


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


@actions.command(cls=Command)
def view(ctx: typer.Context, action_id: ActionId, json_output: JsonOutput = False) -> None:
    """Read an action's status and result."""
    with open_database(action_database(ctx)) as engine:
        display(Tasks(engine).get(action_id), json_output=json_output)


@actions.command(cls=Command)
def cancel(ctx: typer.Context, action_id: ActionId, json_output: JsonOutput = False) -> None:
    """Cancel queued work while its host is stopped."""
    with own_database(action_database(ctx)), open_database(action_database(ctx)) as engine:
        tasks = Tasks(engine)
        if tasks.get(action_id).status == "running":
            message = "This action needs recovery by its executing host."
            raise ValueError(message)
        display(tasks.cancel(action_id), json_output=json_output)


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
    runner: Annotated[
        Literal["local", "gha"] | None, typer.Option(help="Execution location; default LANDING_RUNNER or local.")
    ] = None,
    workspace: Annotated[Path | None, typer.Option(help="Prepared local checkout; default current directory.")] = None,
    host: Annotated[str, typer.Option(help="Listen address.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(min=1, max=65535, help="Listen port.")] = 8080,
) -> None:
    """Receive GitHub work for a local worker or GitHub Actions."""
    import uvicorn

    from landing.server import ServerSettings, create_app

    execution = execution_settings(ctx)
    options = ServerSettings()
    if runner is not None:
        options.runner = runner
    if workspace is not None:
        options.workspace = workspace
    if execution.db is not None:
        options.db = execution.db
    uvicorn.run(
        create_app(options=options, skill_dirs=tuple(ctx.find_root().params.get("skill_dir") or ())),
        host=host,
        port=port,
    )


def write_outputs(context: GitHubEnvironment | None, action: Action | None, *, error: str | None = None) -> None:
    """Write native Action outputs and the step summary when available.

    A step that fails before an action exists still records ``failed``, so a downstream job can
    tell a broken runner apart from an event that was intentionally skipped.
    """
    outputs = {
        "id": action.id if action else "",
        "status": "failed" if error is not None else action.status if action else "skipped",
        "decision": action.decision or "" if action else "",
        "result": error if error is not None else action.result or "" if action else "",
    }
    # Output destinations are the runner's own files, read from the environment so a failure to
    # parse the full event context cannot suppress the report as well.
    output_path = context.output if context is not None else os.environ.get("GITHUB_OUTPUT")
    summary_path = context.step_summary if context is not None else os.environ.get("GITHUB_STEP_SUMMARY")
    if output_path:
        with Path(output_path).open("a") as output:
            for name, value in outputs.items():
                delimiter = uuid4().hex
                output.write(f"{name}<<{delimiter}\n{value}\n{delimiter}\n")
    if summary_path:
        with Path(summary_path).open("a") as output:
            output.write(outputs["result"] + "\n")


def native_event(context: GitHubEnvironment) -> dict | None:
    """Read the event file this runner was given, or None when it has none."""
    if not context.event_path:
        return None
    payload = json.loads(context.event_path.read_text())
    if not isinstance(payload, dict):
        message = "The event file must contain a JSON object."
        raise ValueError(message)  # noqa: TRY004 -- invalid serialized input, not a Python argument type.
    return payload


def github_event(ctx: typer.Context) -> None:
    """Execute the native Actions event or a signed webhook envelope."""
    ingress: Ingress
    context: GitHubEnvironment | None = None
    # Remove the signing key before reading configuration or the event file, so no later failure
    # can leave it in the environment of the work this process starts.
    secret = os.environ.pop("LANDING_GITHUB_WEBHOOK_SECRET", "")
    try:
        context = GitHubEnvironment()
        # Load operator policy before integration settings resolve, so the Action uses the same source.
        settings = execution_settings(ctx)
        options = GitHubSettings()
        payload = native_event(context)
        if payload and claims_forwarded_delivery(payload):
            # A dispatched envelope is re-verified here: this runner process is a new trust boundary.
            ingress = read_forwarded(forwarded_inputs(payload), secret)
            options = GitHubSettings.for_webhook(options.repository)
        else:
            ingress = NativeEvent(payload, context.event_name, context.run_id, context.run_attempt)
        action = asyncio.run(
            execute_github(
                options,
                ingress,
                database=settings.db or context.runner_temp / "landing/landing.sqlite3",
                skill_dirs=ctx.find_root().params.get("skill_dir") or (),
                plugins=[TerminationHooks()],
                context=context,
            )
        )
    except (ValueError, OSError, RuntimeError) as exc:
        # The step fails either way; record why so its outputs are not reported as skipped.
        write_outputs(context, None, error=str(exc) or type(exc).__name__)
        raise
    write_outputs(context, action)
    if action is not None:
        typer.echo(action.model_dump_json(indent=2))
        raise typer.Exit(action.step_exit_code())


def main(argv: list[str] | None = None) -> int:
    try:
        create_cli_app()(args=argv, prog_name="landing")
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0

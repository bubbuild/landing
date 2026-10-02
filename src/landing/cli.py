"""Typer commands; local and remote calls share action contracts."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import sys
from contextlib import closing
from functools import partial
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, NoReturn

import httpx
import typer
from pydantic import ValidationError

from landing.commands import COMMANDS
from landing.models import TERMINAL, Action, ActionRequest, FileInput
from landing.tasks import Tasks

app = typer.Typer(
    help="Explain CI failures, delegate fixes, and review evidence.",
    no_args_is_help=True,
    rich_markup_mode=None,
    context_settings={"help_option_names": ["-h", "--help"]},
)
actions = typer.Typer(help="Inspect and control recorded actions.", no_args_is_help=True)
app.add_typer(actions, name="action")

JsonOutput = Annotated[bool, typer.Option("--json", help="Print records as JSON.")]
Limit = Annotated[int, typer.Option(min=1, max=100, help="Maximum records to return.")]
ActionId = Annotated[str, typer.Argument(metavar="ID")]


def show_version(value: bool) -> None:
    if value:
        typer.echo(version("landing"))
        raise typer.Exit()


@app.callback()
def configure(
    ctx: typer.Context,
    db: Annotated[Path | None, typer.Option(help="Local SQLite path; overrides LANDING_DB.")] = None,
    server: Annotated[str | None, typer.Option(envvar="LANDING_SERVER", help="Remote Landing server URL.")] = None,
    skill_dir: Annotated[list[Path] | None, typer.Option(help="Additional trusted skill root (repeatable).")] = None,
    github_repository: Annotated[
        str | None,
        typer.Option(envvar="LANDING_GITHUB_REPOSITORY", help="Repository context for the prepared gh CLI."),
    ] = None,
    version: Annotated[
        bool,
        typer.Option("--version", callback=show_version, is_eager=True, help="Print the installed version and exit."),
    ] = False,
) -> None:
    ctx.ensure_object(dict)
    ctx.obj.update(db=db, server=server, skill_dir=skill_dir or [], github_repository=github_repository)


def delegate(
    ctx: typer.Context,
    instruction: Annotated[str | None, typer.Argument(help="Work to delegate; alternatively supply --input.")] = None,
    input_files: Annotated[
        list[str] | None, typer.Option("--input", metavar="FILE", help="UTF-8 evidence; - reads stdin.")
    ] = None,
    workspace: Annotated[str | None, typer.Option(help="Local directory or registered remote workspace.")] = None,
    check: Annotated[
        list[str] | None, typer.Option(metavar="COMMAND", help="Required validation (repeatable).")
    ] = None,
    json_output: JsonOutput = False,
    output: Annotated[Path | None, typer.Option(help="Also write the result to this file.")] = None,
    detach: Annotated[bool, typer.Option(help="Return on remote admission.")] = False,
) -> NoReturn:
    execute(
        ctx,
        command=ctx.info_name,
        instruction=instruction,
        input=input_files or [],
        workspace=workspace,
        check=check or [],
        json=json_output,
        output=output,
        detach=detach,
    )


for name, help_text in {
    "triage": "Identify a problem and its acceptance criteria.",
    "fix": "Repair the delegated problem and validate changes.",
    "review": "Evaluate a candidate against independent evidence.",
    "explain": "Explain the supplied question or evidence.",
}.items():
    app.command(name=name, help=help_text)(delegate)


@actions.command("list")
def list_actions(
    ctx: typer.Context, limit: Limit = 50, cursor: str | None = None, json_output: JsonOutput = False
) -> NoReturn:
    execute(ctx, command="action", operation="list", limit=limit, cursor=cursor, json=json_output)


@actions.command("logs")
def logs(
    ctx: typer.Context,
    action_id: ActionId,
    after: Annotated[int, typer.Option(min=0, help="Return events after this cursor.")] = 0,
    limit: Limit = 50,
    json_output: JsonOutput = False,
) -> NoReturn:
    execute(ctx, command="action", operation="logs", id=action_id, after=after, limit=limit, json=json_output)


def inspect_action(ctx: typer.Context, action_id: ActionId, json_output: JsonOutput = False) -> NoReturn:
    execute(ctx, command="action", operation=ctx.info_name, id=action_id, json=json_output)


actions.command("view", help="Read an action's status and result.")(inspect_action)
actions.command("cancel", help="Cancel queued or running work.")(inspect_action)


@actions.command()
def watch(
    ctx: typer.Context,
    action_id: ActionId,
    exit_status: Annotated[bool, typer.Option(help="Use the action's completion and gate exit code.")] = False,
    json_output: JsonOutput = False,
) -> NoReturn:
    """Wait for an action to finish."""
    execute(ctx, command="action", operation="watch", id=action_id, exit_status=exit_status, json=json_output)


@actions.command()
def retry(
    ctx: typer.Context,
    action_id: ActionId,
    detach: Annotated[bool, typer.Option(help="Return on remote admission.")] = False,
    json_output: JsonOutput = False,
) -> NoReturn:
    """Retry a terminal action using its original request."""
    execute(ctx, command="action", operation="retry", id=action_id, detach=detach, json=json_output)


def database(args) -> Path:
    return (args.db or Path(os.getenv("LANDING_DB", "~/.local/share/landing/landing.sqlite3"))).expanduser()


def build_request(args) -> ActionRequest:
    if args.input.count("-") > 1:
        message = "stdin can only be used once."
        raise ValueError(message)
    inputs = []
    for name in args.input:
        content = sys.stdin.read() if name == "-" else Path(name).read_text(encoding="utf-8")
        inputs.append(
            FileInput(
                name="stdin" if name == "-" else Path(name).name,
                media_type=mimetypes.guess_type(name)[0] or "text/plain",
                content=content,
            )
        )
    workspace = args.workspace
    if not args.server:
        workspace = str(Path(workspace or ".").expanduser().resolve())
    return ActionRequest(
        mode=COMMANDS[args.command],
        instruction=args.instruction,
        input=inputs,
        workspace=workspace,
        checks=args.check,
    )


def display(value, args) -> None:
    data = [item.model_dump() for item in value] if isinstance(value, list) else value.model_dump()
    if args.json or getattr(args, "operation", None) == "logs":
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
    print(text)
    if output := getattr(args, "output", None):
        output.write_text(text + "\n", encoding="utf-8")


async def wait(action_id: str, get, *, remote: bool) -> Action:
    try:
        while True:
            action = await get(action_id)
            if action.status in TERMINAL:
                return action
            await asyncio.sleep(0.2)
    except asyncio.CancelledError:
        if remote:
            print(
                f"Stopped waiting for {action_id}; remote work continues. Use 'landing action cancel {action_id}' with the same --server.",
                file=sys.stderr,
            )
        raise


async def call_api(client: httpx.AsyncClient, method: str, path: str, **kwargs):
    response = await client.request(method, path, **kwargs)
    if response.is_error:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        print(detail, file=sys.stderr)
        response.raise_for_status()
    return response.json()


async def remote(args) -> int:
    headers = {"Authorization": "Bearer " + os.environ["LANDING_TOKEN"]} if os.getenv("LANDING_TOKEN") else {}
    async with httpx.AsyncClient(base_url=args.server.rstrip("/"), headers=headers, timeout=30) as client:
        call = partial(call_api, client)

        async def get(action_id):
            return Action.model_validate(await call("GET", f"/v1/actions/{action_id}"))

        if args.command in COMMANDS:
            action = Action.model_validate(await call("POST", "/v1/actions", json=build_request(args).model_dump()))
        elif args.operation == "list":
            params = {"limit": args.limit, **({"cursor": args.cursor} if args.cursor else {})}
            display([Action.model_validate(item) for item in await call("GET", "/v1/actions", params=params)], args)
            return 0
        elif args.operation == "logs":
            from landing.models import Event

            data = await call("GET", f"/v1/actions/{args.id}/events", params={"after": args.after, "limit": args.limit})
            display([Event.model_validate(item) for item in data], args)
            return 0
        elif args.operation == "retry":
            action = Action.model_validate(await call("POST", f"/v1/actions/{args.id}/retries"))
        elif args.operation == "cancel":
            display(Action.model_validate(await call("POST", f"/v1/actions/{args.id}/cancellation")), args)
            return 0
        else:
            action = await get(args.id)
            if args.operation == "view":
                display(action, args)
                return 0
        if not getattr(args, "detach", False):
            action = await wait(action.id, get, remote=True)
        display(action, args)
        checks_status = args.command in COMMANDS or args.operation == "retry" or getattr(args, "exit_status", False)
        return action.exit_code() if checks_status and not getattr(args, "detach", False) else 0


async def local(args) -> int:
    from landing.runtime import Runtime

    if args.command == "action" and args.operation != "retry":
        with closing(Tasks(database(args))) as tasks:
            if args.operation == "list":
                display(tasks.list(args.limit, args.cursor), args)
            elif args.operation == "logs":
                display(tasks.events(args.id, args.after, args.limit), args)
            elif args.operation == "cancel":
                display(tasks.cancel(args.id), args)
            elif args.operation == "view":
                display(tasks.get(args.id), args)
            else:

                async def get(action_id):
                    return tasks.get(action_id)

                action = await wait(args.id, get, remote=False)
                display(action, args)
                return action.exit_code() if args.exit_status else 0
        return 0
    from landing.adapters.github import repository_context

    runtime = Runtime(
        database(args),
        skill_dirs=args.skill_dir,
    )
    async with runtime.running():
        request = build_request(args) if args.command in COMMANDS else runtime.tasks.request(args.id)
        if args.github_repository and repository_context(args.github_repository) not in request.input:
            request = request.model_copy(update={"input": [*request.input, repository_context(args.github_repository)]})
        if args.command in COMMANDS:
            action = await runtime.command(args.command, request)
        else:
            action = await runtime.run(request, retry_of=args.id if args.command == "action" else None)
        display(action, args)
        return action.exit_code()


def validate_args(args) -> None:
    if args.server and (args.db is not None or args.command == "serve"):
        message = "--server cannot be combined with --db or serve."
        raise ValueError(message)
    if args.server and args.skill_dir:
        message = "--skill-dir configures local execution or serve; configure skills on the remote server."
        raise ValueError(message)
    if getattr(args, "detach", False) and not args.server:
        message = "--detach requires --server."
        raise ValueError(message)


@app.command()
def serve(
    ctx: typer.Context,
    host: Annotated[str, typer.Option(help="Listen address.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(min=1, max=65535, help="Listen port.")] = 8080,
    workspace: Annotated[
        list[str] | None, typer.Option(metavar="NAME=PATH", help="Register a workspace (repeatable).")
    ] = None,
) -> NoReturn:
    """Run the HTTP service with registered workspaces."""
    execute(ctx, command="serve", host=host, port=port, workspace=workspace or [], json=False)


def start_server(args) -> None:
    import uvicorn

    from landing.server import create_app

    workspaces = {"default": Path.cwd()}
    for value in args.workspace:
        name, separator, path = value.partition("=")
        if not separator or not name or not path:
            message = "Register a workspace as NAME=PATH."
            raise ValueError(message)
        workspaces[name] = Path(path).expanduser().resolve()
    token = os.getenv("LANDING_TOKEN")
    if args.host not in {"127.0.0.1", "localhost", "::1"} and not token:
        message = "Set LANDING_TOKEN before listening beyond localhost."
        raise ValueError(message)
    uvicorn.run(
        create_app(
            database(args),
            workspaces=workspaces,
            token=token,
            base_url=os.getenv("BASE_URL"),
            github_repository=args.github_repository,
            skill_dirs=args.skill_dir,
        ),
        host=args.host,
        port=args.port,
    )


def execute(ctx: typer.Context, **parameters) -> NoReturn:
    args = SimpleNamespace(**ctx.obj, **parameters)
    try:
        validate_args(args)
        if args.command == "serve":
            ctx.obj.get("start_server", start_server)(args)
            code = 0
        else:
            code = asyncio.run(remote(args) if args.server else local(args))
    except (ValueError, ValidationError, OSError, KeyError) as exc:
        if args.json:
            print(json.dumps({"error": {"code": "invalid_request", "message": str(exc)}}))
        typer.echo(str(exc), err=True)
        code = 2
    except httpx.HTTPError:
        code = 1
    raise typer.Exit(code)


def main(argv: list[str] | None = None) -> int:
    try:
        app(args=argv, prog_name="landing")
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0

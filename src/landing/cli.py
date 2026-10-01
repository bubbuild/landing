"""Conventional argparse commands; local and remote calls share action contracts."""

from __future__ import annotations

import argparse
import asyncio
import json
import mimetypes
import os
import sys
from contextlib import closing
from functools import partial
from pathlib import Path
from typing import NoReturn

import httpx
from pydantic import ValidationError

from landing.models import MODES, TERMINAL, Action, ActionRequest, FileInput
from landing.tasks import Tasks


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ValueError(message)


def parser() -> Parser:
    app = Parser(prog="landing", description="Development actions powered by Bub.")
    app.add_argument("--db", type=Path, help="SQLite database path (local calls only)")
    app.add_argument("--server", default=os.getenv("LANDING_SERVER"), help="Remote Landing server URL")
    app.add_argument(
        "--github-repository", default=os.getenv("LANDING_GITHUB_REPOSITORY"), help="Enable scoped gh tools"
    )
    commands = app.add_subparsers(dest="command", required=True)
    for mode in MODES:
        cmd = commands.add_parser(mode)
        cmd.add_argument("instruction", nargs="?")
        cmd.add_argument("--input", action="append", default=[], metavar="FILE")
        cmd.add_argument("--workspace")
        cmd.add_argument("--check", action="append", default=[], metavar="COMMAND", help="Required validation command")
        cmd.add_argument("--json", action="store_true")
        cmd.add_argument("--output", type=Path)
        cmd.add_argument("--detach", action="store_true")
    actions = commands.add_parser("action").add_subparsers(dest="operation", required=True)
    for operation in ("list", "view", "logs", "watch", "cancel", "retry"):
        cmd = actions.add_parser(operation)
        if operation != "list":
            cmd.add_argument("id")
        if operation in {"list", "logs"}:
            cmd.add_argument("--limit", type=int, default=50)
        if operation == "list":
            cmd.add_argument("--cursor")
        if operation == "logs":
            cmd.add_argument("--after", type=int, default=0)
        if operation == "watch":
            cmd.add_argument("--exit-status", action="store_true")
        if operation == "retry":
            cmd.add_argument("--detach", action="store_true")
        cmd.add_argument("--json", action="store_true")
    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    serve.add_argument("--workspace", action="append", default=[], metavar="NAME=PATH")
    return app


def database(args) -> Path:
    return args.db or Path(os.getenv("LANDING_DB", "~/.local/share/landing/landing.sqlite3")).expanduser()


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
        mode=args.command, instruction=args.instruction, input=inputs, workspace=workspace, checks=args.check
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

        if args.command in MODES:
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
        checks_status = args.command in MODES or args.operation == "retry" or getattr(args, "exit_status", False)
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
    from landing.adapters.github import github_tool

    runtime = Runtime(database(args), tools=[github_tool(args.github_repository)] if args.github_repository else [])
    async with runtime.running():
        request = build_request(args) if args.command in MODES else runtime.tasks.request(args.id)
        action = await runtime.run(request, retry_of=args.id if args.command == "action" else None)
        display(action, args)
        return action.exit_code()


def validate_args(args) -> None:
    if args.server and (args.db is not None or args.command == "serve"):
        message = "--server cannot be combined with --db or serve."
        raise ValueError(message)
    if getattr(args, "detach", False) and not args.server:
        message = "--detach requires --server."
        raise ValueError(message)
    if hasattr(args, "limit") and not 1 <= args.limit <= 100:
        message = "--limit must be between 1 and 100."
        raise ValueError(message)
    if getattr(args, "after", 0) < 0:
        message = "--after must be nonnegative."
        raise ValueError(message)


def serve(args) -> None:
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
        ),
        host=args.host,
        port=args.port,
    )


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    try:
        args = parser().parse_args(arguments)
        validate_args(args)
        if args.command == "serve":
            serve(args)
            return 0
        return asyncio.run(remote(args) if args.server else local(args))
    except (ValueError, ValidationError, OSError, KeyError) as exc:
        if "--json" in arguments:
            print(json.dumps({"error": {"code": "invalid_request", "message": str(exc)}}))
        print(str(exc), file=sys.stderr)
        return 2
    except httpx.HTTPError:
        return 1
    except KeyboardInterrupt:
        return 130

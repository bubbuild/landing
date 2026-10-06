# Python API

Use one `Runtime` to execute actions, stream events, or submit background work. It shares action semantics with CLI and HTTP. Enter its lifecycle before calling it; leaving the context stops owned execution and releases the database. The same Runtime can enter a new lifecycle and resume its stored work.

This reference covers the source checkout, including `submit()` and `lifespan()`, which are unavailable in release `0.2.0`. Install that checkout into your application with `uv add /path/to/landing` and configure the [model](configuration.md#model).

## Delegate an action

```python
from pathlib import Path

from landing.models import ActionRequest
from landing.runtime import Runtime


async def review():
    async with Runtime(Path("landing.sqlite3")).running() as landing:
        return await landing.command("review", ActionRequest(
            mode="gatekeeper",
            instruction="Review the candidate against the acceptance criteria.",
            workspace=str(Path.cwd()),
            checks=["make acceptance"],
        ))
```

Commands select persisted modes: `triage` selects issuer, `fix` selects fixer, `review` selects gatekeeper, and `explain` selects explainer. `Runtime.run()` admits a request with its explicit mode. Read the returned action's status, decision, result, and error; `exit_code()` applies ordinary CLI semantics.

Pass `workspaces={"candidate": Path("/srv/candidate")}` to select registered workspace names instead of filesystem paths.

## Submit background work

For Landing's complete HTTP API, use `create_app()` or [run the server](../guides/server.md). To add background execution to your own ASGI application, pass `Runtime.lifespan` as its lifespan callback:

```python
from pathlib import Path

from fastapi import FastAPI
from landing.runtime import Runtime

landing = Runtime(Path("landing.sqlite3"))
app = FastAPI(lifespan=landing.lifespan)
```

Call `action, created = landing.submit(request)` from your handlers to persist work and return a receipt. Optional `scope` and `key` deduplicate deliveries; `retry_of` retries terminal work. Read `landing.tasks.get(action.id)` when needed and call `landing.cancel(action.id)` to cancel. Caller disconnection leaves accepted work running; application shutdown interrupts active work and preserves the queue.

For another host, enter `landing.running(background=True)`. Ordinary `running()` executes only delegated calls and leaves existing queued work untouched. Submission requires a live background host.

## Stream events

`landing.run_stream()` accepts `session_id`, text or content-part `prompt`, optional mutable `state`, per-call `model`, `allowed_tools`, `allowed_skills`, and `reasoning_effort`.

```python
from contextlib import aclosing
from pathlib import Path

from landing.runtime import Runtime


async def explain():
    async with Runtime(Path("landing.sqlite3")).running() as landing:
        stream = await landing.run_stream(
            session_id="release-question",
            prompt=',explain "Explain the failed release check."',
            allowed_tools=["fs.read", "bash", "skill"],
            allowed_skills=["landing-explainer", "release-investigation"],
        )
        async with aclosing(stream):
            async for event in stream:
                if event.kind == "text":
                    print(event.data["delta"], end="")
        return stream.error, stream.usage
```

Include the corresponding `landing-{mode}` skill in a per-call allow list to retain its default method. Prepare supplementary skills such as `release-investigation` in a discovered root.

Consume the stream fully or close it when leaving early. Closing unfinished work requests durable cancellation. Events include `text`, `reasoning`, `tool_call`, `tool_result`, `usage`, `error`, and `final`; `final` ends a model step, not necessarily the task. Read errors and usage from the stream, and validation or publication failures from the action record.

Use `,triage`, `,fix`, `,review`, or `,explain` to delegate work. `,mode` reads selection; `,mode gatekeeper` selects it without creating a task or calling the model. Selection survives restarts and is isolated by workspace and session. Content parts stay evidence rather than dispatching commands. Explicit `state` bypasses state loading; supply its execution environment to override local execution. Per-call tools and skills only narrow [mode limits](configuration.md#mode-capabilities); callers serialize turns within a session.

## Hook integration

Pass an existing Bub framework to handle Landing commands through your application's message pipeline:

```python
from pathlib import Path

from bub import BubFramework
from bub.channels.message import ChannelMessage
from landing.runtime import Runtime


async def handle():
    framework = BubFramework()
    landing = Runtime(Path("landing.sqlite3"), framework=framework)
    async with framework.running():
        return await framework.process_inbound(ChannelMessage(
            session_id="release-question",
            channel="cli",
            content=',explain "Explain the failed release check."',
        ))
```

Landing registers resources and task execution with the framework. The message host stops its tasks before leaving the framework context; host hooks handle state, rendering, and delivery. Landing supplies task guidance. Set `framework.workspace` before entering its lifecycle to select the message host's default workspace. Mode and history remain isolated by workspace and session. Tools and checks use the environment supplied in state, or execute locally in the selected workspace. Host environment hooks receive the framework's workspace; Bub caches their environment per session and closes it at shutdown.

## Skills and additional tools

`Runtime(path, skill_dirs=[...])` and `create_app(path, skill_dirs=[...])` add trusted skill directories. Use `$skill-name` in instructions to select a prepared skill. [Configuration](configuration.md#skills) defines precedence.

Pass Bub `Tool` instances with `Runtime(path, tools=[...])`, then select them per mode. Authorization belongs in each tool and its execution environment.

Configured [MCP servers](../guides/mcp.md) make their tools available during work, subject to mode and per-call limits. Landing opens and closes these connections automatically.

For stored outcomes and restart behavior, see [Action records](http.md#action-records) and [Recovery](../guides/recovery.md).

## Public objects

::: landing.models.ActionRequest

::: landing.models.Action

::: landing.runtime.Runtime
    options:
      members: [running, lifespan, run, run_stream, command, submit, cancel]

::: landing.server.create_app

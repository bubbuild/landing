# Python API

Delegate actions from Python with the same requests and results as CLI and HTTP. Enter `Runtime.running()` while executing work; leaving the context closes active work and releases the database for another worker.

Add Landing to your application's environment with `uv add "landing==0.1.2"`. The isolated `uv tool install` path provides the CLI; embedding uses the package in your application's environment. Configure the model as described in [Configuration](configuration.md#model).

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

Pass `workspaces={"candidate": Path("/srv/candidate")}` to select registered names. `create_app()` accepts the same mapping, token, public origin, skills, and GitHub context for the [HTTP service](http.md).

## Streaming SDK

`landing.agent.run_stream()` accepts `session_id`, text or content-part `prompt`, optional mutable `state`, per-call `model`, `allowed_tools`, `allowed_skills`, and `reasoning_effort`.

```python
from contextlib import aclosing
from pathlib import Path

from landing.runtime import Runtime


async def explain():
    async with Runtime(Path("landing.sqlite3")).running() as landing:
        stream = await landing.agent.run_stream(
            session_id="release-question",
            prompt=',explain "Explain the failed release check."',
            allowed_tools=["fs.read", "bash", "skill"],
            allowed_skills=["release-investigation"],
        )
        async with aclosing(stream):
            async for event in stream:
                if event.kind == "text":
                    print(event.data["delta"], end="")
        return stream.error, stream.usage
```

Await the stream, consume it fully, and close it when leaving early. Closing unfinished work requests durable cancellation. Events are native `text`, `reasoning`, `tool_call`, `tool_result`, `usage`, `error`, and `final`; a final event ends a model step, not necessarily the whole task. Errors and usage remain on the stream. Validation and publication errors also persist in the action record. Native error kinds are retained; other execution failures use `unknown`.

Model failure logs retain available call metadata and validation error types without argument contents. These diagnostics do not establish the provider as the cause.

Use `,triage`, `,fix`, `,review`, or `,explain` to delegate work. `,mode` reads selection; `,mode gatekeeper` selects it without creating a task or calling the model. Selection survives restarts and is isolated by workspace and session. Content parts stay evidence rather than dispatching commands. Explicit `state` takes precedence over saved state. Per-call tools and skills only narrow [mode limits](configuration.md#mode-capabilities); callers serialize turns within a session.

## Hook integration

Pass an existing Bub framework to handle Landing commands through your application's message pipeline:

```python
from pathlib import Path

from bub import BubFramework
from bub.channels.message import ChannelMessage
from landing.runtime import Runtime


async def handle():
    framework = BubFramework()
    async with Runtime(Path("landing.sqlite3"), framework=framework).running() as landing:
        return await framework.process_inbound(ChannelMessage(
            session_id="release-question",
            channel="cli",
            content=',explain "Explain the failed release check."',
        ))
```

Your application's hooks continue to handle state, prompts, rendering, and delivery. Direct SDK calls return events for your application to display. Both paths save action records and use the selected task workspace unless the host provides its own execution environment.

## Skills and additional tools

`Runtime(path, skill_dirs=[...])` and `create_app(path, skill_dirs=[...])` add trusted skill directories. Use `$skill-name` in instructions to select a prepared skill. [Configuration](configuration.md#skills) defines precedence.

Pass Bub `Tool` instances with `Runtime(path, tools=[...])`, then select them per mode. Authorization belongs in each tool and its execution environment.

Configured [MCP servers](../guides/mcp.md) make their tools available during work, subject to mode and per-call limits. Landing opens and closes these connections automatically.

## History and recovery

Action records survive restarts. Resetting model history does not remove them; completed tasks do not replay automatically. Inspect interrupted work before retrying. See [Action records](http.md#action-records) and [Recovery](../guides/recovery.md).

## Public objects

::: landing.models.ActionRequest

::: landing.models.Action

::: landing.runtime.Runtime
    options:
      members: [running, run, command]

::: landing.server.create_app

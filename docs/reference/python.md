# Python API

Embed Landing with the same action contract and executor used by CLI and HTTP. The caller owns the database and runtime lifecycle; enter `Runtime.running()` while executing work.

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

`landing.agent.run_stream()` accepts native Bub 0.5.0 options: `session_id`, text or content-part `prompt`, optional mutable `state`, per-call `model`, `allowed_tools`, `allowed_skills`, and `reasoning_effort`.

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

The four commands and `mode` are native agent tools. `,mode` reads selection; `,mode gatekeeper` selects it without creating a task. Selection persists in the workspace tape and is isolated by session. Content parts stay evidence rather than dispatching commands. Explicit `state` bypasses hook-based state loading. Per-call tools and skills only narrow [mode limits](configuration.md#mode-capabilities); callers serialize turns within a session.

## Hook integration

Pass an existing Bub framework to register Landing's business hooks alongside host hooks:

```python
from pathlib import Path

from bub import BubFramework
from bub.channels.message import ChannelMessage
from landing.runtime import Runtime


async def handle():
    framework = BubFramework()
    framework.load_builtin_hooks()
    async with Runtime(Path("landing.sqlite3"), framework=framework).running() as landing:
        return await framework.process_inbound(ChannelMessage(
            session_id="release-question",
            channel="cli",
            content=',explain "Explain the failed release check."',
        ))
```

The message pipeline retains state, prompt, rendering, and dispatch hooks. Direct SDK calls return events without rendering or dispatching. Both paths share durable tasks and execution. The runtime binds the task workspace; host-provided native environments remain authoritative. Outbound channels belong to the host.

## Skills and additional tools

`Runtime(path, skill_dirs=[...])` and `create_app(path, skill_dirs=[...])` add trusted roots with native discovery, skill loading, and `$skill-name` expansion. [Configuration](configuration.md#skills) defines precedence.

Pass Bub `Tool` instances with `Runtime(path, tools=[...])`, then select them per mode. Authorization belongs in each tool and its execution environment.

## Runtime design

All four modes share one Bub 0.5.0 agent loop. Landing registers hooks explicitly for mode state, prompts, a task sidecar, and execution. Standalone Landing does not discover external plugins or packaged channel skills.

The sidecar owns `actions` and `action_events`. SQLite tape storage in the same database reuses Bub's query and async adapter. Resetting model history does not remove task records; completed tasks do not replay. See [Action records](http.md#action-records) and [Recovery](../guides/recovery.md).

## Public objects

::: landing.models.ActionRequest

::: landing.models.Action

::: landing.runtime.Runtime
    options:
      members: [running, run, command]

::: landing.server.create_app

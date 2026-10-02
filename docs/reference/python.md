# Python API

Embed Landing to use the same commands, mode settings, SQLite tasks, and executor as the CLI and service. Use the streaming SDK for agent integration or `Runtime.run()` for a request-to-action call. Enter `Runtime.running()` while executing work; the caller owns the database and runtime lifecycle.

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

Commands select the persisted mode: `triage` → `issuer`, `fix` → `fixer`, `review` → `gatekeeper`, and `explain` → `explainer`. `Runtime.run(ActionRequest(...))` directly admits the same task with its explicit mode. Inspect the returned action's status, decision, result, and error separately. Use `action.exit_code()` for ordinary CLI acceptance semantics.

`Runtime(path, workspaces={"candidate": Path("/srv/candidate")})` selects registered names instead of arbitrary local paths. `create_app()` accepts the same mapping, a bearer token, a public origin, and optional GitHub repository context. See the [HTTP contract](http.md).

## Streaming SDK

`landing.agent.run_stream()` accepts the Bub 0.5.0 SDK options: `session_id`, text or content-part `prompt`, optional mutable `state`, per-call `model`, `allowed_tools`, `allowed_skills`, and `reasoning_effort`. Await it before iterating and close the stream when leaving early. Closing unfinished work requests durable cancellation.

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

These are native SDK events: `text`, `reasoning`, `tool_call`, `tool_result`, `usage`, `error`, and `final`. A `final` event ends a model step; consume the complete stream to finish the task. Native model errors and usage remain available on the stream. Landing's validation or publication failures also produce an error and persist in the action record.

The four action commands and `mode` are native agent tools available through comma command dispatch. `,mode` reads the session's current mode; `,mode gatekeeper` selects a mode for subsequent ordinary prompts without running a task. Each action command selects its mode before executing. Selection persists in the workspace's SQLite tape and is isolated by session. Content parts remain evidence and do not dispatch comma commands.

Supplying `state` bypasses hook-based state loading, as in the native SDK. The runtime binds the current agent and selected task workspace; a supplied native `Environment` remains authoritative. Per-call tools and skills can only narrow the selected mode's [configured capabilities](configuration.md#mode-capabilities). The caller serializes turns within a session.

## Hook integration

Register Landing's business hooks in an existing Bub framework by passing it to `Runtime`. Existing host hooks remain registered; Landing adds mode state, task sidecars, prompts, and the shared executor. It does not discover external plugins.

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

The framework message pipeline retains its build-prompt, state, rendering, and dispatch hooks. Direct SDK calls return stream events and do not render or dispatch messages. Both paths share task admission and execution. Host-provided environments and outbound channels remain the embedding application's responsibility.

## Skills and additional tools

`Runtime(path, skill_dirs=[Path("/srv/team-skills")])` and `create_app(path, skill_dirs=[...])` add trusted skill roots. Discovery uses the selected workspace's `.agents/skills`, explicit roots, configured `skill_dirs`, and `~/.agents/skills`. Native skill discovery, the `skill` tool, and `$skill-name` expansion load permitted instructions.

Pass Bub `Tool` instances to `Runtime(path, tools=[...])` to register additional capabilities, then select them independently in each mode's `allowed_tools`. Enforce authorization in the tool and execution environment. Instructions and skill lists are not an operating-system boundary.

## Runtime design

Landing is powered by the Bub 0.5.0 SDK. All four modes share one native agent loop. Landing registers its hooks explicitly, composes repository guidance with business prompts, and forwards SDK tool and skill selection. Standalone Landing does not discover installed plugins or packaged channel skills. End users configure and call Landing directly.

The task sidecar owns the `actions` and `action_events` SQLite tables. An inline tape store keeps model execution entries in the same database and reuses Bub's query implementation and async adapter. Model history resets do not remove task records. Completed work is not automatically replayed. See [Action records](http.md#action-records) and [Replication and recovery](../guides/recovery.md).

## Public objects

::: landing.models.ActionRequest

::: landing.models.Action

::: landing.runtime.Runtime
    options:
      members: [running, run, command]

::: landing.server.create_app

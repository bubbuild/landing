# Python API

Embed Landing when an application needs to delegate the same work as the CLI without HTTP. The public entry points are the request and action models, `Runtime.running()`, `Runtime.run()`, and `create_app()`.

```python
from pathlib import Path

from landing.models import ActionRequest
from landing.runtime import Runtime


async def review():
    async with Runtime(Path("landing.sqlite3")).running() as landing:
        return await landing.run(ActionRequest(
            mode="gatekeeper",
            instruction="Review the candidate against the acceptance criteria.",
            workspace=str(Path.cwd()),
            checks=["make acceptance"],
        ))
```

Inspect the returned action's status, decision, result, and error separately. Use `action.exit_code()` for the ordinary CLI acceptance semantics.

`Runtime(path, workspaces={"candidate": Path("/srv/candidate")})` selects registered names instead of arbitrary local paths. `create_app()` accepts the same mapping, a bearer token, a public origin, and an optional GitHub repository. See the [HTTP contract](http.md) for service behavior.

`Runtime(path, skill_dirs=[Path("/srv/team-skills")])` and `create_app(path, skill_dirs=[...])` add trusted skill roots. Discovery uses the selected workspace's `.agents/skills`, these explicit roots in order, configured `skill_dirs`, and `~/.agents/skills`. The SDK's native skill catalogue, `skill` tool, and `$skill-name` expansion load skill instructions without adding plugins or changing mode permissions.

## Additional tools

An embedding application can pass Bub `Tool` instances to `Runtime(path, tools=[...])` for authorized operations. These additional tools are available to every mode. Enforce the capabilities and authorization you need in their implementation; project instructions are not an access-control mechanism.

## Runtime design

Landing is powered by the Bub 0.5.0 SDK. All four modes share one agent runtime; mode guidance and code-owned permissions determine the task behavior. Landing explicitly registers its SDK hooks and uses the SDK's skill discovery for project, user, and configured roots. Installed Bub plugins and packaged channel skills are not discovered. End users configure and call Landing directly.

The task sidecar owns the `actions` and `action_events` SQLite tables. An inline tape store keeps model execution entries in the same database and reuses Bub's query implementation and async adapter. Model history resets do not remove task records. Completed work is not automatically replayed. See [HTTP reference](http.md#action-records) for lifecycle and [Replication and recovery](../guides/recovery.md) for storage boundaries.

## Public objects

::: landing.models.ActionRequest

::: landing.models.Action

::: landing.runtime.Runtime
    options:
      members: [running, run]

::: landing.server.create_app

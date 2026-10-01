# Landing

Landing uses one Bub agent to carry out development actions for people, from a terminal, a CI job, or an HTTP request.

| Mode | User-facing behavior |
| --- | --- |
| `issuer` | Identify problems and explain what needs attention. |
| `fixer` | Change the workspace, validate the change, and explain it. |
| `gatekeeper` | Evaluate evidence and decide whether a candidate can proceed. |
| `explainer` | Explain a question or failure using evidence. |

Every mode returns a plain-text result. A gatekeeper also records `allow`, `block`, or `inconclusive`; a missing decision is inconclusive.

## Install and configure

Python 3.12 or later and a POSIX host are required.

```bash
uv sync
export BUB_MODEL="openai:gpt-4.1"
export BUB_API_KEY="your-provider-api-key"
export LANDING_DB="$PWD/.ci-state/landing.sqlite3"
```

Model configuration uses Bub's existing `BUB_*` settings, including `BUB_API_BASE` and `BUB_MODEL_TIMEOUT_SECONDS`. Landing imposes no step budget; Bub's SDK default allows the agent to finish naturally. Landing pins Bub 0.5.0 and explicitly registers its SDK hooks. Installed Bub plugins and ambient skills are not discovered or loaded.

## Use from a terminal or CI

```bash
uv run landing issuer "Identify the problems in this report." --input test-output.txt
uv run landing fixer "Fix the CLI argument regression." --check "uv run pytest tests/test_cli.py"
uv run landing gatekeeper "Review this checkout against the acceptance criteria." \
  --input acceptance.txt --check "uv run pytest" --json --output review.json
uv run landing explainer "Explain the failed validation and the next step." --input test-output.txt
```

Commands wait for completion. `--input` snapshots UTF-8 files into the request; `--input -` reads stdin. `--workspace` selects a local directory and defaults to the current directory. The workspace's `AGENTS.md` is included in the agent's instructions.

`--check` specifies a required shell command. Gatekeeper runs these checks before evaluating evidence, and any failed check forces `block`. Fixer runs them after the agent finishes, and a failed check marks the action failed while keeping the explanation and changes for inspection. Checks have a five-minute timeout each. Gatekeeper, issuer, and explainer receive read tools; fixer also receives Bub's file-editing and shell tools.

| Exit code | Meaning |
| --- | --- |
| `0` | Completed; a gatekeeper must also decide `allow`. |
| `1` | Failed, cancelled, interrupted, or a gatekeeper decided `block` or `inconclusive`. |
| `2` | Invalid arguments, input, or configuration. |
| `130` | Waiting was interrupted with Ctrl-C. |

```bash
uv run landing action list
uv run landing action view act_example --json
uv run landing action logs act_example --after 0
uv run landing action watch act_example --exit-status
uv run landing action cancel act_example
uv run landing action retry act_example
```

Retries create a new action referencing the original and reuse its request snapshot. They do not revert existing workspace changes. Only terminal actions can be retried.

Landing's test suite exercises these commands through the real Bub SDK with deterministic model responses. The **Landing dogfood** workflow also runs the checkout's actual CLI with a real model during development. See the dogfood setup below.

### Continuous dogfood

Native CI runs tests, typing, lint, documentation and container recovery independently.
After those jobs finish, **Landing feedback** evaluates the current PR and posts one maintained
mode reply with the inspected revision. Its model recommendation is advisory; human review and
native checks decide whether to accept the candidate. Default-branch results go to issuer, which
uses `gh` to maintain existing CI issues, investigate recurrence, and record recovery evidence.

**Landing duty** handles maintainer comments, completed release workflows and daily maintenance.
A bot comment never delegates new work. Put a command on its own line:

```text
@landing issuer Track the repeated documentation failure and its acceptance criteria.
@landing fixer Fix this issue and retain a regression test.
@landing gatekeeper Review this candidate against the issue and independent checks.
@landing explainer Explain the failed job and the next discriminating check.
```

Fixer works in a separate Git worktree. Required tests, typing and documentation checks run
before code commits, pushes and opens or updates a `landing/fix-<issue>` candidate PR through gh.
Failed checks preserve the changes and reply to the original work item without publishing a PR.
It never merges. Workflow-token publication explicitly dispatches the existing **Main** workflow
through `gh workflow run`, carrying the PR number and candidate head; this runs native checks
and gatekeeper even though GitHub suppresses ordinary PR events from `GITHUB_TOKEN`.
The repository must allow Actions to create PRs. Comment, dispatch and schedule workflows
start handling events after their definitions are on the default branch.

Mode prompts require evidence, issue reuse, reproduction, the right repair layer and concrete
acceptance criteria. The gh tool enforces repository scope and mode capabilities: all modes can
read issues, PRs and runs; issuer can maintain issues. Arbitrary API writes, merging, approving and
credential operations are unavailable. GitHub publishing credentials are withheld from the model's
shell in the dogfood runner; the explicit gh tool uses the ordinary credential source.
These are tool restrictions, not an operating-system sandbox for trusted workspaces.

Configure the real model with repository variables and a secret. The DeepSeek example disables thinking:

```bash
/usr/bin/gh variable set LANDING_MODEL --repo PsiACE/landing --body "deepseek:deepseek-v4-pro"
/usr/bin/gh variable set LANDING_API_BASE --repo PsiACE/landing --body "https://api.deepseek.com"
/usr/bin/gh variable set LANDING_COMPLETION_ARGS --repo PsiACE/landing --body '{"reasoning_effort":"none"}'
/usr/bin/gh secret set LANDING_API_KEY --repo PsiACE/landing
```

The ordinary CLI can enable the same scoped gh tool without Actions:

```bash
uv run landing --github-repository PsiACE/landing issuer \
  "Investigate the supplied evidence, reuse an existing issue or create an actionable one." \
  --input report.txt
```

The minimal event runner also works locally. It uses `/usr/bin/gh`'s active login, prepares an
isolated worktree, records the action and delivery in SQLite, and publishes the candidate or reply:

```bash
uv run python -m landing.adapters.github fixer --repository PsiACE/landing --number 2 \
  --instruction "Fix the issue and add a regression test." \
  --delivery-key "issue-2-maintainer-request-1" --db ./evidence/landing.sqlite3 \
  --check "uv run pytest tests" --check "uv run ty check"
```

Reusing the same database and delivery key reads the existing action. Delivery retries check
existing platform comments and do not rerun the model. A new instruction requires a new key.
Worktrees must remain available while inspecting or retrying saved changes. CI invocations use
separate databases and retain evidence for 30 days; they are not a shared distributed queue.
Long-lived feedback belongs in issues and regression tests, rather than expiring artifacts alone.

Each action has a ten-minute execution limit and a 120-second model request timeout
in dogfood CI. Missing configuration fails the delegated job; automatic feedback skips until a
model is configured. Fork PRs run native CI without model credentials. No automatic repair loop
or model-driven merge is installed.

During review, use actual issues and PRs to record useful advice, mistaken assumptions, rejected
fix locations, human edits and post-merge results. A merged PR alone does not prove deployment
recovery. Preserve meaningful failures as regression tests or executable documentation; use those
cases to compare prompt and tool changes. Do not tune against a model's own score.

The standalone `scripts/dogfood.sh` remains usable with the ordinary CLI outside GitHub. It saves
checks, logs, patches and SQLite, and runs an explainer after failure without changing the original
exit status. All modes share the same Bub SDK and task sidecar.

## Serve HTTP requests and webhooks

```bash
export LANDING_TOKEN="your-server-token"
uv run landing --db /var/lib/landing/landing.sqlite3 serve \
  --host 127.0.0.1 --port 8080 --workspace candidate=/srv/landing/candidate
```

A remote workspace is a registered name referring to a checkout on the server. Preparing that checkout belongs to the caller or its platform adapter. Each database has one worker; actions execute serially. Another process can query or cancel local work, but a second worker cannot open the same database. Server-side tools and check commands use the host environment, so this endpoint is intended for trusted callers and prepared workspaces. Binding beyond localhost requires `LANDING_TOKEN`.

```bash
export LANDING_SERVER="http://127.0.0.1:8080"
uv run landing gatekeeper "Review the prepared checkout." \
  --workspace candidate --check "uv run pytest" --json
uv run landing explainer "Explain this failure." --workspace candidate --input test-output.txt --detach
uv run landing action watch act_example --exit-status
```

`--detach` is available for remote creation and retry. Ctrl-C during a remote wait leaves the server action running; `action cancel` requests cancellation. Ctrl-C during a local command cancels its action and closes owned shell processes.

An HTTP caller can submit the same action contract as a webhook request:

```bash
curl -i http://127.0.0.1:8080/v1/actions \
  -H "Authorization: Bearer $LANDING_TOKEN" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: delivery-example-1" \
  -d '{"mode":"explainer","instruction":"Explain the failed validation.","workspace":"candidate","input":[{"type":"text","text":"The CLI exited with code 0 for invalid arguments."}]}'
```

Admission commits the request and queued event before returning `201 Created` with `Location`. Reusing an idempotency key with the same request returns the existing action with `200`; different content returns `409`. JSON requests are limited to 16 MiB. Errors use `application/problem+json`.

| Method and path | Behavior |
| --- | --- |
| `POST /v1/actions` | Durably accept an action. |
| `GET /v1/actions?limit=50&cursor=act_example` | Read history, newest first. |
| `GET /v1/actions/{id}` | Read status, result, decision, and error. |
| `GET /v1/actions/{id}/events?after=0&limit=50` | Read lifecycle, tool references, and validation records. |
| `POST /v1/actions/{id}/cancellation` | Cancel queued work or request active cancellation. |
| `POST /v1/actions/{id}/retries` | Create an explicit retry; accepts `Idempotency-Key`. |
| `GET /healthz` | Check HTTP liveness without authentication. |
| `GET /up` | Check the worker and SQLite connection without authentication. |
| `GET /openapi.json` | Read the generated API schema. |

Collections are JSON arrays; pagination uses a `Link` header with `rel="next"`. Cancellation returns `202` while active work is stopping, and `200` for terminal work. The generic webhook uses this action contract. GitHub Actions events use the small gh adapter; provider-native webhook signatures and payloads remain caller-side adaptation. To enable gh tools on an HTTP service, supply `--github-repository` before `serve` or set `LANDING_GITHUB_REPOSITORY`; credentials use gh’s normal login or `GH_TOKEN`.

## Run the container

```bash
export LANDING_TOKEN="your-server-token"
export BUB_MODEL="openai:gpt-4.1"
export BUB_API_KEY="your-provider-api-key"
docker compose up --build -d
export LANDING_SERVER="http://127.0.0.1:8080"
uv run landing explainer "Explain the latest validation failure." --workspace default
```

The image serves HTTP on port 80 as UID 1000. `/up` checks the worker and database; Docker also uses it for its healthcheck. The database is `/storage/landing.sqlite3`, and the default workspace is `/storage/workspace`. The image includes Git and uv for preparing and validating checkouts. Register additional workspaces with the ordinary `serve --workspace NAME=PATH` option. Each database still has one worker, regardless of the container's CPU allocation.

Litestream 0.5.17 is included in the image. Server startup restores a missing database from the configured replica before starting Landing; an existing database is preserved. Litestream supervises the server, forwards shutdown signals, and attempts a final sync after Landing stops. It replicates the entire database, including actions, events, idempotency keys, and Bub tapes. Replication is asynchronous; `/up` reports application readiness, while Litestream logs and its optional heartbeat configuration report backup health.

Compose defaults to `file:///replica/landing` in a separate named `replica` volume. This allows recovery after losing the primary volume, but both volumes remain on the same host. For a backup outside that host, set a unique replica URL for this installation and provide your storage credentials:

```bash
export LITESTREAM_REPLICA_URL="s3://example-backups/landing/production"
export AWS_REGION="us-east-1"
export AWS_ACCESS_KEY_ID="your-storage-access-key"
export AWS_SECRET_ACCESS_KEY="your-storage-secret-key"
docker compose up -d
```

The bundled `/etc/litestream.yml` keeps snapshots for seven days and enables a local control socket at `/run/landing/litestream.sock`. For S3-compatible endpoints or other provider settings, mount a standard Litestream config and set `LITESTREAM_CONFIG` to its path. That config must replicate the same `LANDING_DB` used by Landing. A bare server container requires `LITESTREAM_REPLICA_URL` or an explicit config; it fails startup when neither is configured. CLI commands use the normal entry point and do not start replication automatically.

```bash
docker compose exec landing litestream sync -wait \
  -socket /run/landing/litestream.sock /storage/landing.sqlite3
```

To recover a lost database, stop the old container, retain its replica, and start the service with a fresh primary volume and the same replica configuration. Restored active actions become `interrupted`; queued actions resume. Restoring SQLite does not restore workspace files, so prepare the same registered checkouts before restarting the service. Single-use CI containers can explicitly back up a closed database with `litestream replicate -once -force-snapshot DB_PATH REPLICA_URL` using the image's binary.

### Deploy with ONCE

The image follows [ONCE's application contract](https://github.com/basecamp/once#making-a-once-compatible-application): port 80, `/up`, and persistent application data under `/storage`. ONCE supplies `BASE_URL`; Landing uses it for public pagination links. TLS terminates at ONCE's proxy.

After publishing your image to your own registry, deploy it with the standard ONCE CLI:

```bash
once deploy registry.example.com/team/landing:0.0.0 --host landing.example.com \
  --env "LANDING_TOKEN=$LANDING_TOKEN" \
  --env "BUB_MODEL=$BUB_MODEL" \
  --env "BUB_API_KEY=$BUB_API_KEY" \
  --env "LITESTREAM_REPLICA_URL=$LITESTREAM_REPLICA_URL" \
  --env "AWS_REGION=$AWS_REGION" \
  --env "AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID" \
  --env "AWS_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY"
```

Enable ONCE's full-volume backups in its settings to cover workspace files as well as SQLite. The image uses ONCE's default paused-container backup behavior; it has no pre-backup hook claiming mutable workspaces are safe to copy live. Litestream's SQLite replica and ONCE's full-volume backups serve different recovery needs. See [Litestream's container guide](https://litestream.io/guides/docker/) and [replication command](https://litestream.io/reference/replicate/) for the upstream lifecycle and restore behavior.

## Embed the SDK

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
            checks=["uv run pytest"],
        ))
```

An embedding application can pass Bub `Tool` instances to `Runtime(..., tools=[...])` for authorized platform operations. These tools are available to every mode. There is no second agent loop or separate SDK transport.

The mounted `tasks` sidecar owns ordinary `actions` and `action_events` tables. An inline SQLite tape store saves Bub entries in the same database and reuses Bub's query implementation and async adapter. Model tape resets do not delete task rows. Interrupted active actions become `interrupted` on restart; queued actions remain available to the server worker. Completed work is never automatically replayed. Model history, request snapshots, events, and results remain in SQLite; workspace files and requested output files are normal user artifacts.

## Develop

```bash
make check
make test
make docs-test
uv build
```

Tests replace only external model requests: Bub's agent loop, native tool execution, tape merging, SQLite persistence, real shell validation, and the local HTTP transport execute normally. Model quality and downstream platform delivery require separate real-task acceptance.

Keep behavior tests for user-visible CLI, HTTP and workflow outcomes, and regression tests for
actual mistakes that can recur. Prefer end-to-end acceptance for platform wiring. Tests should
survive an implementation rewrite that preserves the user's experience; avoid assertions about
helper structure, internal step counts or argument order. Straightforward glue can be inspected
directly and accepted through an actual workflow run.

The container job builds the image and checks health, authentication, graceful shutdown, a paused-volume restore, and recovery onto an empty primary volume from a real Litestream file replica. Run it locally with Docker or Podman:

```bash
docker build -t landing:local .
uv run python tests/container_smoke.py --image landing:local
```

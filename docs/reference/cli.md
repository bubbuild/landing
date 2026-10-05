# CLI reference

```text
landing [--db PATH | --server URL] [--github-repository OWNER/REPO] [--skill-dir PATH] COMMAND
```

Put global options before the subcommand. Local execution requires Python 3.12 or later and a POSIX host. Install the CLI with `uv tool install "landing==0.2.0"`; from a Landing source checkout, use `uv run landing`. See [Local use](../guides/local.md) for a task example.

`--help` and `-h` show options; `--version` prints the installed version. Use `--install-completion` to install shell completion or `--show-completion` to print it. Commands are noninteractive, write results to stdout, and diagnostics to stderr. `--json` returns structured records.

## Global options

| Option | Contract |
| --- | --- |
| `--db PATH` | Local SQLite path, overriding `LANDING_DB`; cannot combine with `--server`. |
| `--server URL` | Remote service, default `LANDING_SERVER`. |
| `--github-repository OWNER/REPO` | Prepared gh context, default `LANDING_GITHUB_REPOSITORY`; configured on the executing host. |
| `--skill-dir PATH` | Repeatable trusted skill root for local execution or `serve`; remote clients cannot set it. |

[Configuration](configuration.md) defines environment defaults and skill precedence.

## Delegate an action

```text
landing {triage,fix,review,explain} [INSTRUCTION] [OPTIONS]
```

A nonblank instruction or at least one input is required. Results are text by default. Commands wait for completion unless remote admission uses `--detach`.

| Option | Contract |
| --- | --- |
| `--input FILE` | Repeatable UTF-8 snapshot; `-` reads stdin once. Paths refer to the caller's filesystem. |
| `--workspace VALUE` | Local directory, default current directory; remote registered name, default `default`. |
| `--check COMMAND` | Repeatable required command for fix or review, with a five-minute timeout per check. |
| `--json` | Print an action record. |
| `--output PATH` | Also save the displayed result; the parent directory must exist. |
| `--detach` | Return on remote admission; requires a server. |

Commands select modes: `triage` selects issuer, `fix` selects fixer, `review` selects gatekeeper, and `explain` selects explainer. [Mode capabilities](configuration.md#mode-capabilities) filter tools and skills; they are not a sandbox. Review guidance asks the agent to leave the candidate unchanged.

Review checks run before evaluation; failure forces `block`. Fix checks run after the agent finishes; failure marks the action failed and preserves edits and any explanation. Empty completions fail.

## Inspect and control actions

| Command | Contract |
| --- | --- |
| `action list` | Newest first; `--limit 1..100` defaults to 50, with optional `--cursor ID`. |
| `action view ID` | Read status, result, decision, and error. |
| `action logs ID` | JSON events; `--after N` defaults to 0, with the same limit bounds. |
| `action watch ID` | Wait; `--exit-status` applies completion and gate exit semantics. |
| `action cancel ID` | Cancel queued work or request active cancellation. |
| `action retry ID` | Create a new action from a terminal task's request; remote `--detach` is supported. |

All accept `--json`. Retry does not revert workspace changes. Read and cancellation commands can access SQLite while its worker runs; execution has a single owner.

## Serve

```text
landing [GLOBAL OPTIONS] serve [--host HOST] [--port PORT] [--workspace NAME=PATH]
```

Host defaults to `127.0.0.1`, port to `8080`. Workspace registration is repeatable; `default` initially points to the current directory. `serve` and `github` cannot combine with `--server`. Listening beyond localhost requires `LANDING_TOKEN`. See [Run the server](../guides/server.md).

## Exit codes and interruption

| Code | Meaning |
| --- | --- |
| `0` | Completed, with `allow` for review; successful inspection or detached admission. `watch` applies action semantics only with `--exit-status`. |
| `1` | Failed, cancelled, interrupted, review `block` or `inconclusive`, or remote request failure. |
| `2` | Invalid arguments, input, local configuration, or missing local action. |
| `130` | Waiting interrupted with Ctrl-C. |

Local creation cancels on Ctrl-C and closes owned shell processes. Remote waiting stops without cancelling the task. [GitHub event delivery](../guides/github.md#publish-native-results) uses advisory decision semantics.

Help and syntax diagnostics remain plain in captured output. Syntax errors use stderr and exit 2. Valid commands encountering invalid task input or local configuration also return 2 and, with `--json`, emit an `error` object on stdout. Remote connection and timeout failures report on stderr with exit 1; HTTP status errors report the server's detail.

## GitHub events

```bash
landing --db landing.sqlite3 github event --repository example/service --event event.json --delivery-key comment:123
```

`github event` reads a GitHub event, checks caller authority, and executes the selected command. It uses the prepared gh identity and current workspace, returns an action record as JSON, and treats review decisions as advisory. `--help` lists its options. Use repeatable `--check` and `--upstream-workflow` options for local calls. An unrelated or unauthorized event exits successfully without an action record.

In GitHub Actions, the command reads `GITHUB_EVENT_PATH` and the Action's `INPUT_*` environment. Multiline `INPUT_CHECKS` and `INPUT_UPSTREAM_WORKFLOW` supply checks and allowed workflow names. Explicit options take precedence. The database uses `--db`, then `INPUT_DATABASE`, then `LANDING_DB`, then `RUNNER_TEMP/landing/landing.sqlite3`. Outside a workflow, supply `--delivery-key`. Native output and summary files receive the same values as the composite Action.

## GitHub Action

`bubbuild/landing@0.2.0` executes work from GitHub workflow events. [Get started](../get-started.md) shows project preparation and one Action invocation with built-in admission.

| Input | Contract |
| --- | --- |
| `command` | `review` (default), `fix`, `triage`, or `explain`; comment events route their own command. |
| `instruction` | Task and acceptance criteria; defaults to an evidence-based review request. |
| `repository` | Workflow repository; cross-repository targets fail. |
| `trust` | `repository` (default) or `owner`. |
| `upstream-workflow` | Allowed `workflow_run` name; empty rejects these events. |
| `number` | Issue or PR number; supplied by the event when available. |
| `head` | Candidate head, distinct from the checked revision; PR events supply it. |
| `checked-revision` | Actual checked commit, default `github.sha`. |
| `run-id` | Native run to inspect, default the current run. |
| `delivery-key` | Stable delivery identity; default run ID and attempt. |
| `command-prefix` | Comment prefix, default `/landing`. |
| `checks` | Required check commands, one per line. |
| `database` | SQLite path, default `RUNNER_TEMP/landing/landing.sqlite3`. |

Outputs are `id`, `status`, `decision`, and `result`. Unrelated or unauthorized events return `status: skipped` without task creation, model use, delegated tools or checks, or publication. Permission lookup errors fail the Action. Execution or required publication failure also fails the step; gate recommendations remain advisory. Automatic unchanged issuer follow-up can finish without a public update. See [GitHub integration](../guides/github.md) for trust and publication.

For automatic feedback, set GitHub's step-level `continue-on-error: true`; this is a workflow property, not an Action input. `steps.landing.outcome` remains `failure` even though its conclusion becomes `success`. Use the outcome to warn and retain logs and artifacts. Omit this property for strict delegations.

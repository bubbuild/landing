# CLI reference

```text
landing [--db PATH] [--github-repository OWNER/REPO] [--skill-dir PATH] COMMAND
```

Put global options before the subcommand. Local execution requires Python 3.12 or later and a POSIX host. [Local use](../guides/local.md) covers installation and a task with the latest release.

`--help` and `-h` show options; `--version` prints the installed version. Use `--install-completion` to install shell completion or `--show-completion` to print it. Commands are noninteractive, write results to stdout, and diagnostics to stderr. `--json` returns structured records.

## Global options

| Option | Contract |
| --- | --- |
| `--db PATH` | SQLite path, overriding `LANDING_DB`. |
| `--github-repository OWNER/REPO` | Prepared gh context, default `LANDING_GITHUB_REPOSITORY`; configured on the executing host. |
| `--skill-dir PATH` | Repeatable trusted skill root for local or runner execution. |

[Configuration](configuration.md) defines environment defaults and skill precedence.

## Delegate an action

```text
landing {triage,fix,review,explain,bump} [INSTRUCTION] [OPTIONS]
```

A nonblank instruction or at least one input is required. Results are text by default. Commands execute the task and return its result. The [HTTP service](http.md) admits GitHub webhooks and dispatches them to Actions.

| Option | Contract |
| --- | --- |
| `--input FILE` | Repeatable UTF-8 snapshot; `-` reads stdin once. Paths refer to the caller's filesystem. |
| `--workspace VALUE` | Execution directory, default current directory. |
| `--check COMMAND` | Repeatable required command for fix, bump, or review, with a five-minute timeout per check. |
| `--json` | Print an action record. |
| `--output PATH` | Also save the displayed result; the parent directory must exist. |

Commands select modes: `triage` selects issuer, `fix` selects fixer, `review` selects gatekeeper, `explain` selects explainer, and `bump` selects bumper. [Mode capabilities](configuration.md#mode-capabilities) filter tools and skills; they are not a sandbox. Review guidance asks the agent to leave the candidate unchanged.

Review checks run before evaluation; failure forces `block`. Fix and bump checks run after the agent finishes; failure marks the action failed and preserves edits and any explanation. Empty completions fail.

Bump accepts dependency targets, policy and configuration locations through ordinary instructions or inputs. See [local dependency updates](../guides/local.md#delegate-a-dependency-update) and [GitHub updates](../guides/github.md#help-dependency-updates-after-ci-fails) for preparation and repair behavior.

## Inspect and control actions

| Command | Contract |
| --- | --- |
| `action list` | Newest first; `--limit 1..100` defaults to 50, with optional `--cursor ID`. |
| `action view ID` | Read status, result, decision, and error. |
| `action logs ID` | JSON events; `--after N` defaults to 0, with the same limit bounds. |
| `action watch ID` | Wait; `--exit-status` applies completion and gate exit semantics. |
| `action cancel ID` | Cancel queued work while its executing host is stopped. |
| `action retry ID` | Execute a new action from a terminal task's request. |

All accept `--json`. Retry does not revert workspace changes. Inspection can read SQLite while its worker runs; only explicit `watch` polls for completion. Use Ctrl-C to cancel local execution, or cancel the GitHub Actions run for remote execution. Each database has one execution owner.

## Serve

```text
landing [GLOBAL OPTIONS] serve [--runner local|gha] [--workspace PATH] [--host HOST] [--port PORT]
```

Host defaults to `127.0.0.1`, port to `8080`. Execution defaults to `local`; `--runner gha` selects stateless Actions placement. Local mode uses `--db` for its inbox and history, runs in `--workspace` or the current directory, and needs prepared model/tools/publication access. Both modes require a webhook secret. See [Run the server](../guides/server.md).

## Exit codes and interruption

| Code | Meaning |
| --- | --- |
| `0` | Completed, with `allow` for review; successful inspection. `watch` applies action semantics only with `--exit-status`. |
| `1` | Failed, cancelled, interrupted, review `block` or `inconclusive`. |
| `2` | Invalid arguments, input, local configuration, or missing local action. |
| `130` | Execution or observation interrupted with Ctrl-C. |

Local creation cancels on Ctrl-C and closes owned shell processes. Stopping `action watch` leaves the executing task running. [GitHub event delivery](../guides/github.md#publish-native-results) uses advisory decision semantics.

Help and syntax diagnostics remain plain in captured output. Syntax errors use stderr and exit 2. Valid commands encountering invalid task input or local configuration also return 2 and, with `--json`, emit an `error` object on stdout.

## GitHub events

`landing github event` is the GitHub CLI and Actions entry point. The local service calls the same execution API directly. It reads `GITHUB_EVENT_PATH` and [integration configuration](configuration.md#github-workflows-and-runner), admits the caller, routes the event, and verifies native publication. It returns an action record as JSON and treats review decisions as advisory. Unauthorized or unrelated events exit successfully without an action; lookup failures fail visibly.

A forwarded `workflow_dispatch` carries GitHub's original body in `inputs.event`, original signature in `inputs.signature`, and original headers in `inputs.event_name` and `inputs.delivery`. The runner treats the delivery as forwarded when any of those four inputs is non-empty, verifies the original HMAC over the exact body text with `LANDING_GITHUB_WEBHOOK_SECRET`, removes the key, and checks the original caller. Forwarded inputs do not set integration policy. Local workers read accepted input from the inbox and call the execution API without native Actions authority. Native Actions events use the same execution and publication path without wrapping.

The executor workflow declares those four inputs with empty defaults and documents leaving them empty for maintenance. An envelope whose forwarded inputs are all empty is therefore native work, so a scheduled or manual maintenance dispatch runs normally instead of being rejected as an unverifiable delivery. A claimed delivery with an empty, non-string, or badly signed body fails as invalid input; it never falls back to native authority.

For local event reproduction, set `GITHUB_EVENT_PATH`, `LANDING_GITHUB_REPOSITORY`, and `LANDING_GITHUB_DELIVERY_KEY` before invoking the command. Ordinary local tasks still use `landing explain`, `triage`, `fix`, `review`, or `bump` with their CLI options. The duplicated `github event` option surface is removed.

Native `dependabot_alert` payloads resolve the current alert through GitHub: open selects bump; fixed or dismissed selects triage. Alert lookup errors fail the invocation.

## GitHub Action

The `bubbuild/landing` Action prepares an isolated Landing installation, invokes `github event`, and exports `id`, `status`, `decision`, and `result`. [Get started](../get-started.md) shows native CI use; [server setup](../guides/server.md) covers webhook placement.

| Input | Contract |
| --- | --- |
| `command` | `review` (default), `fix`, `triage`, `explain`, or `bump`; comments and signed events route their own command. |
| `instruction` | Task and acceptance criteria; defaults to carrying out the selected action using repository guidance. |
| `checks` | Required check commands, one per line. |

Repository policy and task references use `LANDING_GITHUB_*` environment settings rather than additional forwarded inputs. Model settings and execution tools belong to the prepared runner. `LANDING_DB` selects a nondefault runner database.

Unauthorized or unrelated events return `status: skipped` without model work, delegated tools, checks, or publication. A step that fails after the runner's output destinations are readable writes `status: failed` with the error as `result`, so a broken runner is not mistaken for a skipped event. `GITHUB_OUTPUT` and `GITHUB_STEP_SUMMARY` are read from the environment for that report, so a failure to parse the event context does not also suppress it; a completely absent runner environment is outside the guarantee. Permission lookup, execution, and required publication failures fail the step; gate recommendations remain advisory. For optional feedback, GitHub's step-level `continue-on-error: true` keeps that step from failing native CI while preserving `steps.landing.outcome`. It is not an Action input.

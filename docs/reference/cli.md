# CLI reference

```text
landing [--db PATH | --server URL] [--github-repository OWNER/REPO] [--skill-dir PATH] COMMAND
```

Put global options before the subcommand. Local execution requires Python 3.12 or later and a POSIX host. From a source checkout, prefix commands with `uv run`.

## Global options

| Option | Meaning |
| --- | --- |
| `--db PATH` | Local SQLite path; overrides `LANDING_DB`. Cannot combine with `--server`. |
| `--server URL` | Remote service; defaults to `LANDING_SERVER`. |
| `--github-repository OWNER/REPO` | Supply GitHub repository context locally or on `serve`; defaults to `LANDING_GITHUB_REPOSITORY`. A remote client cannot configure the server's environment. |
| `--skill-dir PATH` | Repeatable additional trusted skill root for local execution or `serve`. Remote clients must configure skills on the server. Project skills take precedence over these roots, followed by configured roots and `~/.agents/skills`. |

See [Configuration](configuration.md) for environment defaults and authorization.

## Delegate an action

```text
landing {triage,fix,review,explain} [INSTRUCTION] [OPTIONS]
```

Provide a nonblank instruction or at least one input. Results are plain text by default; `--json` returns an action record. Commands wait for completion unless remote creation uses `--detach`.

| Option | Meaning |
| --- | --- |
| `--input FILE` | Repeatable UTF-8 file snapshot; `-` reads stdin once. Files need not exist in the target workspace. |
| `--workspace VALUE` | Local directory, default current directory; remote registered name, default `default`. |
| `--check COMMAND` | Repeatable required shell command; only fix and review. Each has a five-minute timeout. |
| `--json` | Print the full action record. |
| `--output PATH` | Also write the displayed result to a local file; its parent directory must exist. |
| `--detach` | Return on remote admission; requires `--server` or `LANDING_SERVER`. |

Commands select modes: `triage` → `issuer`, `fix` → `fixer`, `review` → `gatekeeper`, and `explain` → `explainer`. Each mode can independently configure `allowed_tools` and `allowed_skills`; unset lists allow the native SDK defaults. Review guidance asks the agent to leave the candidate unchanged. These settings filter the agent loop, not operating-system access. See [Mode capabilities](configuration.md#mode-capabilities). Gatekeeper checks execute before evaluation, and a failed check forces `block`. Fixer checks execute after the agent finishes; a failure marks the action failed and preserves changes and any returned explanation. An empty completion is a failure.

## Inspect and control actions

| Command | Options and behavior |
| --- | --- |
| `action list` | `--limit 1..100` (default 50), `--cursor ID`; newest first. |
| `action view ID` | Read status, result, decision, and error. |
| `action logs ID` | `--after N` (default 0), `--limit 1..100` (default 50); JSON events. |
| `action watch ID` | Wait; `--exit-status` applies the action's completion and decision exit code. |
| `action cancel ID` | Cancel queued work or request active cancellation. |
| `action retry ID` | Create a new action referencing a terminal action; remote `--detach` is supported. |

All commands accept `--json`. Retries use the original request snapshot and do not revert existing workspace changes. Local view, logs, list, and cancellation can access the database while its worker runs; execution uses a single owner.

## Serve

```text
landing [GLOBAL OPTIONS] serve [--host HOST] [--port PORT] [--workspace NAME=PATH]
```

Host defaults to `127.0.0.1`, port to `8080`. Workspace registration is repeatable; `default` initially points to the current directory. `serve` cannot combine with `--server`. Listening beyond localhost requires `LANDING_TOKEN`.

## Exit codes and interruption

| Code | Meaning |
| --- | --- |
| `0` | Completed; gatekeeper must also decide `allow`. Inspection commands return zero on success; `watch` requires `--exit-status` to apply action semantics. Detached admission returns zero on success. |
| `1` | Failed, cancelled, interrupted, a gatekeeper decided `block` or `inconclusive`, or a remote HTTP request failed. |
| `2` | Invalid arguments, input, local configuration, or a missing local action. |
| `130` | Waiting was interrupted with Ctrl-C. |

Ctrl-C during local creation cancels its action and closes owned shell processes. Ctrl-C during remote waiting leaves the action running; request cancellation explicitly. The [GitHub event runner](../guides/github.md) has separate advisory decision semantics.

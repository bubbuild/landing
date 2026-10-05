# Use Landing locally

Run a task against your own checkout with Python 3.12 or later, uv, and a POSIX host. Install Landing as an isolated tool, then prepare your project dependencies and checks:

```bash
uv tool install "landing==0.2.0"
export LANDING_MODEL="openai:gpt-4.1"
export LANDING_API_KEY="your-provider-api-key"
```

Run commands from your project checkout. If `landing` is not on your PATH after installation, run `uv tool update-shell` and restart your shell. For another provider or endpoint, see [Configuration](../reference/configuration.md#model).

## Explain a failure

Use an existing UTF-8 failure log and select the project workspace:

```bash
landing explain "Explain the failure and suggest the next useful check." --workspace ../project --input check.log
```

Read whether the answer establishes a cause or proposes a hypothesis. Use `triage` when you need reproduction evidence and acceptance criteria for a work item. Without a configured platform capability, results stay in the terminal.

## Delegate a fix

Prepare a disposable checkout or branch you can inspect. State the expected user behavior and supply the check that establishes it:

```bash
landing fix "Fix the missing-name behavior: print usage to stderr and exit 2. Preserve valid greetings." --workspace ../candidate --input check.log --check "python acceptance.py" --json --output fix.json
```

Replace the instruction and check with your actual problem. Fix changes the workspace, then Landing runs the required check. A successful action returns `status: completed`; failed execution or validation preserves partial changes and any explanation. Inspect the diff and the recorded check before accepting it. Ordinary local execution does not automatically commit or publish.

## Review the candidate

```bash
landing review "Review the candidate against the issue's acceptance criteria." --workspace ../candidate --check "python acceptance.py" --json --output review.json
```

Landing runs the check before review. A failed check forces `block`; missing evidence can produce `inconclusive`. The command exits zero only for a completed review with `allow`. Review guidance asks the agent to leave the candidate unchanged; configured tool lists filter capabilities but are not a sandbox.

## Inspect and continue

```bash
landing action list
landing action view act_example --json
landing action logs act_example
```

Replace `act_example` with an ID from the list. Use the same database as the task; `--db` or `LANDING_DB` selects it. Workspace edits remain in the selected checkout. See [CLI reference](../reference/cli.md) for piping input, cancellation, retries, and exit codes.

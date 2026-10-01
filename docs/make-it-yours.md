# Make Landing work for you

Start with the built-in modes, then adjust the parts your team needs. You choose the model, execution environment, instructions, evidence, and acceptance checks. The CLI and HTTP interface share the same action contract, so adopting a server does not require a different way to describe work.

## Choose a model

Configure the provider model, API key, and optional endpoint in the environment where Landing runs. See [Configuration](reference/configuration.md) for the exact variables. These settings do not require another application or plugin setup.

```bash
export BUB_MODEL="openai:gpt-4.1"
export BUB_API_KEY="your-provider-api-key"
```

Landing sends the task and relevant tool results to that provider. Choose a provider and execution environment suitable for the material you give it. Local storage and self-hosting do not change the provider's data handling.

## Write project instructions

Landing reads the selected workspace's `AGENTS.md` as project instructions. Put durable conventions there and supply task-specific requirements in the instruction or `--input` files. For example:

```markdown
# Project instructions

- Read docs/cli-contract.md before changing command behavior.
- Use python acceptance.py to verify the public interface.
- Compare benchmark results using the same workload and environment.
- Record the deployed revision when investigating runtime failures.
- Report missing evidence rather than treating it as a successful check.
```

These instructions guide the agent; they do not add permissions or enforce an operating-system boundary. Built-in mode guidance lives in `src/landing/prompts.py` if you need to change it in your own distribution.

## Use your existing checks

```bash
uv run landing fixer "Resolve the reported compatibility problem." --input acceptance.txt --check "make acceptance" --check "make benchmark"
```

Checks run in the target workspace using the host environment. You provide the language runtimes, project dependencies, credentials, and baseline data they need. A command's exit status determines whether that check passed. For a benchmark, the command must enforce your performance criterion; a report-only command returning zero does not prove that performance is acceptable.

## Connect your tools

Supply snapshots with `--input` before building a connector. The optional GitHub tool uses `gh`, and the [GitHub event runner](guides/github.md) adds replies and candidate publication. Other platforms can translate their events into the [HTTP action contract](reference/http.md) and deliver results through their own interfaces. Provider-specific signatures belong at that translation boundary.

For an embedding application, the [Python API](reference/python.md) accepts additional tools. You own their authorization and capabilities; those tools are made available to every mode.

## Run Landing where you work

Use a local command for an individual task, a [CI step](guides/ci.md) for a prepared checkout, or a [service](guides/server.md) for shared callers. Keep your existing checks and human review. Decide whether a gatekeeper recommendation should be advisory or affect the job's exit status.

Landing keeps task records and execution history in SQLite. You choose the database location and its retention; workspace files are separate. Containers include [replication and recovery](guides/recovery.md). Each database has one worker, with serial action execution.

## Change the behavior

Landing's source is licensed under Apache-2.0. You can inspect and modify the runtime, mode prompts, and adapters, or embed the public API. No hosted Landing account or GitHub App installation is needed for the documented CLI and service paths. See [Develop and dogfood](development.md) for checks and contribution guidance, and [Python API](reference/python.md#runtime-design) for the internal SDK design.

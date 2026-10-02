# Make Landing work for you

Start with the built-in modes, then adjust the parts your team needs. You choose the model, execution environment, instructions, evidence, and acceptance checks. The CLI and HTTP interface share the same action contract, so adopting a server does not require a different way to describe work.

## Choose a model

Configure the provider model, API key, and optional endpoint in the environment where Landing runs. You can also save these settings in `~/.landing/config.yml`; an existing configuration remains available as a fallback. See [Configuration](reference/configuration.md) for the exact variables. These settings do not require another application or plugin setup.

```bash
export LANDING_MODEL="openai:gpt-4.1"
export LANDING_API_KEY="your-provider-api-key"
```

Landing sends the task and relevant tool results to that provider. Choose a provider and execution environment suitable for the material you give it. Local storage and self-hosting do not change the provider's data handling.

## Write project instructions

Landing reads the selected workspace's root `AGENTS.md` as project instructions. Its guidance asks the agent to read more specific `AGENTS.md` files along the paths it works on. Put durable conventions there and supply task-specific requirements in the instruction or `--input` files. For example:

```markdown
# Project instructions

- Read docs/cli-contract.md before changing command behavior.
- Use python acceptance.py to verify the public interface.
- Compare benchmark results using the same workload and environment.
- Record the deployed revision when investigating runtime failures.
- Report missing evidence rather than treating it as a successful check.
```

These instructions guide the agent; they do not add permissions or enforce an operating-system boundary. Built-in mode guidance lives in `src/landing/prompts.py` if you need to change it in your own distribution.

## Use skills

Landing discovers skills in the selected workspace's `.agents/skills` and your `~/.agents/skills`. Each skill has a directory matching its name and a `SKILL.md` with `name` and `description` YAML front matter. The agent can list and load applicable skills through its read-only `skill` tool. Name a skill as `$deployment-check` in your instruction to include its content directly.

```bash
uv run landing explain 'Use $deployment-check to explain the failed deployment.' --workspace ./candidate --input deployment.log
```

Save trusted skill roots as `skill_dirs` in your Landing YAML configuration or set `LANDING_SKILL_DIRS` to a JSON list. Add roots for an individual command with repeatable global `--skill-dir` options. Repository skills take precedence over explicit roots, configured roots, and your home skills, in that order. Skills are instructions and resources; they do not install plugins or expand a mode's configured tool set. See [Skills configuration](reference/configuration.md#skills).

```bash
uv run landing --skill-dir ~/.local/share/landing/team-skills explain "Explain the deployment against our team conventions." --workspace ./candidate --input deployment.log
```

For skills kept in GitHub, use your normal gh login to prepare a local checkout, then select its skill root. Update that checkout through your normal repository workflow; Landing does not fetch or execute remote skill repositories automatically.

```bash
/usr/bin/gh repo clone example/team-skills ~/.local/share/landing/team-skills
uv run landing --skill-dir ~/.local/share/landing/team-skills/.agents/skills explain 'Use $deployment-check to explain the failed deployment.' --workspace ./candidate --input deployment.log
```

Configure skill roots on the host that executes the action. A remote CLI caller cannot supply local directories to a server. Repository instructions and skills should come from a checkout you trust, particularly when delegating fixer work.

## Use your existing checks

```bash
uv run landing fix "Resolve the reported compatibility problem." --input acceptance.txt --check "make acceptance" --check "make benchmark"
```

Checks run in the target workspace using the host environment. You provide the language runtimes, project dependencies, credentials, and baseline data they need. A command's exit status determines whether that check passed. For a benchmark, the command must enforce your performance criterion; a report-only command returning zero does not prove that performance is acceptable.

## Connect your tools

Supply snapshots with `--input` before building a connector. The [GitHub integration](guides/github.md) supplies event context and checks publication receipts; the agent uses your prepared `gh` CLI. Other platforms can translate their events into the [HTTP action contract](reference/http.md) and deliver results through their own interfaces. Provider-specific signatures belong at that translation boundary.

For an embedding application, the [Python API](reference/python.md) accepts additional tools. You own their authorization and capabilities; register them once, then select the tools and skills available to each mode in [configuration](reference/configuration.md#mode-capabilities).

## Run Landing where you work

Use a local command for an individual task, a [CI step](guides/ci.md) for a prepared checkout, or a [service](guides/server.md) for shared callers. Keep your existing checks and human review. Decide whether a gatekeeper recommendation should be advisory or affect the job's exit status.

Landing keeps task records and execution history in SQLite. You choose the database location and its retention; workspace files are separate. Containers include [replication and recovery](guides/recovery.md). Each database has one worker, with serial action execution.

## Change the behavior

Landing's source is licensed under Apache-2.0. You can inspect and modify the runtime, mode prompts, and adapters, or embed the public API. No hosted Landing account or GitHub App installation is needed for the documented CLI and service paths. See [Develop and dogfood](development.md) for checks and contribution guidance, and [Python API](reference/python.md#runtime-design) for the internal SDK design.

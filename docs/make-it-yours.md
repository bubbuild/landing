# Make Landing work for you

You choose the model, environment, instructions, tools, and acceptance checks. Start with the built-in commands and adapt the parts your team needs. CLI, CI, and HTTP calls share the same action contract.

## Choose the model and environment

Configure the model where work executes:

```bash
export LANDING_MODEL="openai:gpt-4.1"
export LANDING_API_KEY="your-provider-api-key"
```

You can save settings in `~/.landing/config.yml`; see [Configuration](reference/configuration.md) for endpoints, precedence, and compatibility with existing settings. Model requests go to the selected provider, whose data handling applies.

Prepare checkouts, language runtimes, dependencies, credentials, and baseline data using your existing setup process. A remote client uses the server's prepared environment. Landing does not install project tools or skills for you.

## Provide project guidance

Landing reads the selected workspace's root `AGENTS.md`. Its guidance asks the agent to read more specific instructions along affected paths. Put durable project conventions there, and give task-specific requirements in the instruction or input files.

```markdown
# Project instructions

Use python acceptance.py to verify CLI behavior. Compare benchmarks with the same workload and environment. When investigating a deployed failure, record the deployed revision and the relevant logs.
```

Instructions guide the agent; they do not grant permissions or enforce an operating-system boundary.

## Prepare skills

Skills live in a named directory with a `SKILL.md` containing `name` and `description` YAML front matter. Landing discovers the workspace's `.agents/skills`, additional trusted roots, and `~/.agents/skills`. The agent can load a permitted skill through its `skill` tool; `$skill-name` includes it directly in an instruction.

```bash
landing explain 'Use $deployment-check to explain the failed deployment.' --workspace ./candidate --input deployment.log
```

For a skill repository on GitHub, prepare it with your normal gh login:

```bash
gh repo clone example/team-skills ~/.local/share/landing/team-skills
landing --skill-dir ~/.local/share/landing/team-skills/.agents/skills explain 'Use $deployment-check to explain the failure.' --workspace ./candidate --input deployment.log
```

Configure roots on the executing host. Use `skill_dirs` or `LANDING_SKILL_DIRS` for saved settings and repeatable global `--skill-dir` options for individual calls. [Skills configuration](reference/configuration.md#skills) defines discovery precedence. Skills do not install plugins or expand configured tools.

## Select tools and checks

Each mode can independently select its allowed tools and skills through [mode capabilities](reference/configuration.md#mode-capabilities). A Python embedding can register additional native tools. Enforce authorization in those tools and the execution environment; a shell tool can expose more than its name suggests.

Use your existing checks for the behavior you need to establish:

```bash
landing fix "Resolve the reported compatibility problem." --input acceptance.txt --check "make acceptance" --check "make benchmark"
```

Checks execute in the task workspace. Exit status establishes pass or failure, so a benchmark command must enforce your performance criterion rather than merely print a report.

## Connect and extend

Supply focused evidence with `--input`, use the prepared gh CLI for [GitHub](guides/github.md), or translate another platform's events into the [HTTP contract](reference/http.md). Provider signatures belong at that translation boundary. Use a [service](guides/server.md) for shared callers.

Landing's Apache-2.0 source lets you change prompts, runtime behavior, and adapters, or embed the [Python API](reference/python.md). Actions and model history stay in SQLite; workspace files remain separate. Choose retention and [recovery](guides/recovery.md) for both.

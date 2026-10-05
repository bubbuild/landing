# AGENTS.md

Landing delegates development work through CLI, HTTP, Python, and a GitHub Action. Explain, triage, fix, and review select four modes in one agent. SQLite stores accepted work and model history; users prepare their own execution environment and publishing identity.

## Development

Use Python 3.12 or later and the repository's uv environment. Install with `uv sync --frozen` from the repository root. For implementation changes you make, run:

```bash
uv run ty check
uv run pre-commit run --all-files
```

Run affected behavior tests, for example `uv run python -m pytest tests/test_cli.py`. Run `make docs-test` for documentation changes and `uv build` for packaging changes. These checks apply to changes you author; reviews reuse applicable recorded results and run focused probes for unresolved behavior. [Develop and dogfood](docs/development.md) covers the CI matrix, container recovery, and workflow preparation.

## Design

Read the affected contract in `docs/reference/`. Keep action semantics consistent across entry points while preserving intentional transport and delivery differences. Use one Bub 0.5.0 SDK runtime with native state, hooks, tools, and skill discovery. Declare owned tools with `@tool`, adapter commands with Typer, and component settings with Pydantic Settings. Integrations own their configuration and platform rules; the prepared environment owns extension installation.

Repair the owning layer. Prefer public upstream APIs, existing dependencies, and the standard library. Extract helpers only for cohesive responsibilities with matching contracts and reasons to change. Avoid forwarding wrappers, unnecessary adapter splits, and compatibility branches for unreleased intermediate designs. Preserve released public behavior and stored data unless the task authorizes a change. Keep patches focused and lockfile changes intentional.

## Tests

Tests verify supported user behavior or demonstrated mistakes likely to recur; they should survive an implementation rewrite. Avoid assertions on helper structure, internal fields, prompt wording, and upstream argument translation. A changed file does not itself require a new test. Use the smallest counterexample that reaches the claimed operation. Local fixtures do not establish real-provider compatibility, external delivery, or backup recovery. Reuse applicable recorded checks; repeat them for changed behavior or unresolved questions. See [test philosophy](docs/development.md#test-what-people-observe).

## Writing and instructions

Use English in repository files and GitHub contributions. Write direct, present-tense prose; keep paragraphs and commands on one line unless their format requires newlines. Comments explain non-obvious intent. Public documentation describes user-visible behavior; implementation details belong in development and dogfood documentation.

Use friendly-python and piglet for Python; documentation-writer and humanizer for documentation. Default prompts own shared investigation principles and each mode's delivery responsibilities. Adapters own platform presentation and publication rules. Keep project development rules here and Landing-specific review contracts in [.agents/skills/landing-review/SKILL.md](.agents/skills/landing-review/SKILL.md). Keep reusable system prompt prefixes stable and task evidence in task inputs. Avoid duplicating instructions.

## Contributions

Use task-named branches, Conventional Commit titles, and the applicable contribution template. Verify changes you author before publishing and record relevant validation in the PR description. With a workflow token, start candidate CI explicitly: `gh workflow run main.yml --ref BRANCH -f number=PR_NUMBER -f head=CANDIDATE_SHA`. For review, load the Landing review skill; its project contracts complement the mode and adapter guidance. Do not merge or change credentials without explicit delegation. Maintainers own contributor relationships and decisions.

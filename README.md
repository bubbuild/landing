# Landing

Explain CI failures, delegate fixes, and review the evidence. Use your existing checks. Keep acceptance in your team's hands.

**With Landing, you own and control the workflow.** Choose your model, run it locally, in CI, or as a service, and adapt the instructions and tools to your team. Start with the built-in workflow; the Apache-2.0 source lets you change how it works.

## Start with a task

| Your task | Command | Result |
| --- | --- | --- |
| Understand a question or failure | `explain` | An explanation grounded in evidence and a useful next step. |
| Make a problem actionable | `triage` | Expected behavior, reproduction evidence, and acceptance criteria. |
| Repair a known problem | `fix` | Workspace changes, validation, and an explanation. |
| Evaluate a candidate | `review` | Evidence-based findings and `allow`, `block`, or `inconclusive`. |

Choose the action you need. Each command selects its work mode; the actions can run independently. Results are ordinary text. Native checks and human review remain part of acceptance.

## Try it locally

From a source checkout, with Python 3.12 or later, uv, and a POSIX host:

```bash
uv sync
export LANDING_MODEL="openai:gpt-4.1"
export LANDING_API_KEY="your-provider-api-key"
uv run landing explain "Explain this failure and the next useful check." --input check.log
```

Use an existing UTF-8 log for `check.log`. These are Landing's current model settings; no separate agent application or plugin setup is required.

Follow [From a failed check to a reviewed fix](docs/get-started.md) for a complete, executable example in a disposable workspace. It includes an acceptance check, a delegated repair, and independent review.

## Fit it into your team

Stable results need the system around the code: internal documentation, work items, infrastructure, tests, benchmarks, and observability. Provide the relevant context, define acceptance, verify independently, and retain feedback where your team works. See [Working with Landing](docs/working-with-landing.md) for the method and diagram.

Landing keeps actions and execution history in SQLite. Workspace files stay in your workspace; model requests go to your selected provider. GitHub replies and candidate PRs use an optional `gh` adapter. The CLI and generic HTTP interface work without that adapter.

| Where to go | What it covers |
| --- | --- |
| [Why Landing](docs/index.md) | Adoption paths and the work Landing helps with. |
| [Make Landing work for you](docs/make-it-yours.md) | Models, project instructions, checks, tools, and source changes. |
| [CI](docs/guides/ci.md) / [GitHub](docs/guides/github.md) | Reusable GitHub Action, shell integration, native reviews, and candidate publication. |
| [Server](docs/guides/server.md) | Shared execution and normalized webhook requests. |
| [Deployment](docs/guides/deploy.md) / [Recovery](docs/guides/recovery.md) | Containers, ONCE, Litestream, and storage recovery. |
| [CLI](docs/reference/cli.md) / [Configuration](docs/reference/configuration.md) / [HTTP](docs/reference/http.md) | Commands, settings, and request contracts. |
| [Python API](docs/reference/python.md) | Embedding and runtime design. |
| [Develop and dogfood](docs/development.md) | Checks, meaningful tests, and continuous real-task feedback. |

[Contributions](CONTRIBUTING.md) are welcome. Licensed under [Apache-2.0](LICENSE). Powered by Bub.

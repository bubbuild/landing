# Landing

Explain CI failures, delegate fixes, and review the evidence. Use your existing checks. Keep acceptance in your team's hands.

**With Landing, you own and control the workflow.** Start with the built-in workflow, choose your model, and adapt the instructions and tools to your team. Run it locally, in CI, or as a service. The Apache-2.0 source gives you a path to change the behavior as well as the configuration.

## Start with work you already need to do

You may already use a review bot to find problems in a change, or a coding agent to implement a request. Landing helps with the work between a finding and an accepted result: explaining the evidence, making a problem actionable, proposing a repair, and evaluating the candidate against your checks.

| What you need | Start here | What to look for |
| --- | --- | --- |
| Understand a failed check | [Get started](get-started.md) with `explain` | A sourced explanation and a useful next check. |
| Turn a finding into work | [Working with Landing](working-with-landing.md#make-a-problem-actionable) with `triage` | Expected behavior, reproduction evidence, and acceptance criteria. |
| Resolve a known problem | [Get started](get-started.md#delegate-the-fix) with `fix` | A candidate change with actual validation results. |
| Evaluate a candidate | [Working with Landing](working-with-landing.md#review-the-evidence) with `review` | Specific findings and a recommendation for the inspected revision. |

Choose a command for the task you need. It selects the corresponding mode; each action works independently. Results are ordinary text that people can read in a terminal, an issue, or a PR. A gatekeeper also records an `allow`, `block`, or `inconclusive` recommendation.

## Fit Landing into your workflow

Try one local task before adding automation. Keep your current review tools and CI checks, and decide where another explanation or delegated fix would help. The [first-task tutorial](get-started.md) takes a failing check through explanation, repair, and independent review in a disposable workspace.

For stable results, provide the system around the code: internal documentation, work items, infrastructure context, tests, benchmarks, and observability. [Working with Landing](working-with-landing.md) explains how to turn that context into bounded tasks and acceptance evidence.

## You choose how it runs

| Your choice | Where to go |
| --- | --- |
| Model, project instructions, checks, and tools | [Make Landing work for you](make-it-yours.md) |
| A shell step in your existing CI | [Use Landing in CI](guides/ci.md) |
| Replies and candidate PRs through `gh` | [Use Landing with GitHub](guides/github.md) |
| A shared service or webhook caller | [Run the server](guides/server.md) |
| A container with database replication | [Deploy Landing](guides/deploy.md) |
| Commands, configuration, and request contracts | [Reference](reference/cli.md) |

Actions, request snapshots, results, and execution history are stored in SQLite. Workspace files stay in the workspace. Model requests go to your chosen provider; that provider's data handling still applies. GitHub delivery is an optional adapter, and the core CLI and HTTP contract can be used without it.

Landing's own development uses the same capabilities with native GitHub checks, work items, and human review. See [Develop and dogfood](development.md) for that continuous feedback process.

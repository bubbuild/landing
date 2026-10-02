# Landing

Landing explains CI failures, fixes delegated problems, and publishes reviews where your team works. It uses your existing tools and checks.

With Landing, you own and control the workflow. Choose the model, prepare the execution environment, and adapt instructions and skills to your project. Run it in CI, locally, or as a service. Landing is open source under Apache-2.0 and requires no hosted Landing account.

## Start in CI

[Set up your first PR review](docs/get-started.md). Configure a model, add the GitHub Action to your prepared workflow, and open a PR. Landing publishes a native review with code comments when it finds an actionable problem. Your native checks and team decide what to accept.

```text
Configure CI -> Open a PR -> Read the review -> Decide what to accept
```

The Action uses your authenticated `gh`, project tools, and skills. It checks who can delegate before starting the agent. See [CI integration](docs/guides/ci.md) for other CI systems and [GitHub integration](docs/guides/github.md) for `/landing` commands, publishing identities, and trust policy.

## Use it locally

From a source checkout, with Python 3.12 or later, uv, and a POSIX host:

```bash
uv sync
export LANDING_MODEL="openai:gpt-4.1"
export LANDING_API_KEY="your-provider-api-key"
uv run landing explain "Explain this failure and the next useful check." --input check.log
```

Use a saved UTF-8 log for `check.log`. The commands are `explain`, `triage`, `fix`, and `review`; each works independently. [Local use](docs/guides/local.md) shows how to delegate work and inspect the result.

## Work with your team

Reliable results draw on your documentation, work items, infrastructure, tests, benchmarks, and observability. [Working with Landing](docs/working-with-landing.md) explains how to connect that context and retain feedback. Automate the engineering work you can delegate, and use the time saved to stay involved with contributors and users. Community Over Code.

[Make Landing work for you](docs/make-it-yours.md) covers project instructions, skills, and tools. [Run the server](docs/guides/server.md) when you need shared execution. Actions and model history are stored in SQLite; workspace files stay in your workspace, and model requests go to your chosen provider.

[Contributions](CONTRIBUTING.md) are welcome. Licensed under [Apache-2.0](LICENSE). Powered by [Bub](https://bub.build/) and [tape.systems](https://tape.systems/).

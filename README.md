# Landing

Landing explains CI failures, fixes delegated problems, triages issues, and publishes reviews where your team works. It uses your existing tools and checks.

With Landing, you own and control the workflow. Choose the model, prepare the execution environment, and adapt instructions and skills to your project. Run it in CI, locally, or as a service. Landing is open source under Apache-2.0 and requires no hosted Landing account.

## Start in CI

[Set up your first PR review](https://getlanding.dev/get-started/). Configure a model, add the GitHub Action to your prepared workflow, and open a PR. Landing publishes a native review with code comments when it finds an actionable problem. Your native checks and team decide what to accept.

```text
Configure CI -> Open a PR -> Read the review -> Decide what to accept
```

Set the `LANDING_MODEL` repository variable and `LANDING_API_KEY` secret, and grant the review job `contents: read` and `pull-requests: write`. After trusted-caller admission, checkout, and successful native checks, add this step to your existing workflow:

```yaml
- uses: bubbuild/landing@0.1.2
  continue-on-error: true
  env:
    GH_TOKEN: ${{ github.token }}
    LANDING_MODEL: ${{ vars.LANDING_MODEL }}
    LANDING_API_KEY: ${{ secrets.LANDING_API_KEY }}
  with:
    command: review
    instruction: Native checks passed for this checkout. Review this PR using repository guidance and publish actionable findings inline.
```

`continue-on-error` keeps automatic feedback advisory; inspect the Action logs if no review appears. The Action uses your authenticated `gh`, project tools, and skills. It checks who can delegate before starting the agent. See [CI integration](https://getlanding.dev/guides/ci/) for other CI systems and [GitHub integration](https://getlanding.dev/guides/github/) for `/landing` commands, publishing identities, and trust policy.

To build the same review and delegated-fix experience, ask your agent to read [Landing's workflows](https://github.com/bubbuild/landing/tree/main/.github/workflows) and [development guide](https://getlanding.dev/development/), then adapt the CI setup to your repository's tools and checks.

## Use it locally

With Python 3.12 or later, [uv](https://docs.astral.sh/uv/), and a POSIX host:

```bash
uv tool install "landing==0.1.2"
export LANDING_MODEL="openai:gpt-4.1"
export LANDING_API_KEY="your-provider-api-key"
landing explain "Explain this failure and the next useful check." --input check.log
```

Run from your project checkout and use a saved UTF-8 log for `check.log`. The commands are `explain`, `triage`, `fix`, and `review`; each works independently. [Local use](https://getlanding.dev/guides/local/) shows how to delegate work and inspect the result.

## Work with your team

Reliable results draw on your documentation, work items, infrastructure, tests, benchmarks, and observability. [Working with Landing](https://getlanding.dev/working-with-landing/) explains how to connect that context and retain feedback. Automate the engineering work you can delegate, and use the time saved to stay involved with contributors and users. Community Over Code.

[Make Landing work for you](https://getlanding.dev/make-it-yours/) covers project instructions, skills, and tools. For shared execution, [run the server](https://getlanding.dev/guides/server/) or [deploy the container](https://getlanding.dev/guides/deploy/) from `ghcr.io/bubbuild/landing:0.1.2`. Actions and model history are stored in SQLite; workspace files stay in your workspace, and model requests go to your chosen provider.

[Contributions](https://github.com/bubbuild/landing/blob/main/CONTRIBUTING.md) are welcome. Licensed under [Apache-2.0](https://github.com/bubbuild/landing/blob/main/LICENSE). Powered by [Bub](https://bub.build/) and [tape.systems](https://tape.systems/).

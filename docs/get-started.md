# Your first PR review

Add Landing to an existing GitHub Actions workflow and verify that it publishes review feedback on a PR. Start with a same-repository branch whose author has write access. Fork PRs keep their native checks without receiving model or publication credentials.

You need a model API key, a repository where you can configure Actions, and a working native check command. The example uses a Python project with uv; replace its setup and check steps with your project's existing ones. Prepare a trusted workflow source before granting it credentials.

## Configure the model

In repository settings, add the `LANDING_MODEL` variable and `LANDING_API_KEY` secret. You can also use your normal gh login:

```bash
gh variable set LANDING_MODEL --repo example/team-project --body "openai:gpt-4.1"
gh secret set LANDING_API_KEY --repo example/team-project
```

Use your actual repository and provider model. The secret command prompts for the key. See [Configuration](reference/configuration.md#model) for custom endpoints.

## Add the workflow

Save the following as `.github/workflows/review.yml`. The example pins Landing to release `0.2.0`; you can pin it to a reviewed commit instead. Your workflow prepares the project and runs native checks, then calls Landing once. The Action checks the event and caller before starting the agent.

```yaml
name: Review
on:
  pull_request:
permissions:
  contents: read
  pull-requests: write
concurrency:
  group: landing-review-${{ github.repository_id }}-${{ github.event.pull_request.number }}
  cancel-in-progress: true
jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          persist-credentials: false
      - uses: astral-sh/setup-uv@v6
      - name: Run native checks
        run: uv sync --locked && uv run pytest
      - uses: bubbuild/landing@0.2.0
        if: github.event.pull_request.head.repo.full_name == github.repository
        id: landing
        timeout-minutes: 20
        continue-on-error: true
        env:
          GH_TOKEN: ${{ github.token }}
          LANDING_MODEL: ${{ vars.LANDING_MODEL }}
          LANDING_API_KEY: ${{ secrets.LANDING_API_KEY }}
        with:
          command: review
          instruction: Review the candidate against repository guidance and the native checks. Publish actionable findings and record a decision.
      - name: Report unsuccessful feedback
        if: ${{ !cancelled() && steps.landing.outcome == 'failure' }}
        run: echo "::warning::Landing feedback failed. Inspect the Action logs."
```

Ubuntu's runner provides gh; use your normal setup action on other runners. The example publishes as `github-actions[bot]`. Keep existing required checks independent; `continue-on-error` makes this automatic review advisory and the next step warns when it fails. Omit that property when a delegated task must fail the job. For owner-restricted execution, use a protected workflow source and the [GitHub trust controls](guides/github.md#choose-who-can-delegate).

## Open a PR and read the result

Put the workflow on your trusted branch, then open a same-repository PR with a small change. When the native checks pass, Landing reads the candidate and publishes advisory feedback through a native GitHub `COMMENT` review. Findings appear at the relevant code locations. A clean review can say `No blocking findings.` without adding code comments.

Check the workflow and PR together. `status: completed` means the task finished and its required publication was verified; `decision` records `allow`, `block`, or `inconclusive`. Your team reviews the findings and independent checks to decide what to accept. Review guidance asks the agent to leave the candidate unchanged.

Unauthorized or unrelated events return `status: skipped` without invoking the model. Fork PRs run the native checks and skip the Landing step. Execution or required publication failure still gives the Action a failed outcome; this workflow reports it as a warning and continues. A successful workflow does not prove that Landing delivered a review. If you see no review, start with the workflow logs and [Troubleshooting](guides/troubleshooting.md).

Continue with [CI integration](guides/ci.md) for scope and concurrency, or [GitHub integration](guides/github.md#wire-commands-and-follow-ups) to delegate `/landing fix` and `/landing explain` from comments.

For the same ongoing review and delegated-fix loop Landing uses, ask your agent to read [the repository's workflows](https://github.com/bubbuild/landing/tree/main/.github/workflows) and [development guide](development.md), then adapt that setup to your project.

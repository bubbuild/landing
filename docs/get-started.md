# Your first PR review

Add Landing to an existing GitHub Actions workflow and verify that it publishes review feedback on a pull request. You need a model API key, a repository where you can configure Actions, and a working native check command. The example uses a Python project with uv; replace its setup and check steps with your project's own. Configure the workflow on a trusted branch before giving it credentials.

## Configure the model

In repository settings, add the `LANDING_MODEL` variable and `LANDING_API_KEY` secret. You can also use your normal gh login:

```bash
gh variable set LANDING_MODEL --repo example/team-project --body "openai:gpt-4.1"
gh secret set LANDING_API_KEY --repo example/team-project
```

Use your actual repository and provider model. The secret command prompts for the key. See [Configuration](reference/configuration.md#model) for custom endpoints.

## Add the workflow

Save the following as `.github/workflows/review.yml`. The example follows Landing's `main`; pin a release tag or reviewed commit to keep the runtime fixed. Your workflow prepares the project and runs native checks, then calls Landing once. The Action checks the event and caller before starting the agent.

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
      - uses: actions/checkout@v7
        with:
          persist-credentials: false
      - uses: astral-sh/setup-uv@v10.2.0
      - name: Run native checks
        run: uv sync --locked && uv run pytest
      - uses: bubbuild/landing@main
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

Ubuntu's runner provides gh; prepare [a supported version](guides/github.md) with your usual setup action elsewhere. The example publishes as `github-actions[bot]`. Keep your required checks independent of this job: `continue-on-error` makes the automatic review advisory, and the following step turns a failure into a warning. Omit that property when a delegated task must fail the job. For owner-only execution, use a protected workflow source and the [GitHub trust controls](guides/github.md#choose-who-can-delegate).

Start with a same-repository branch whose author has write access. A pull request from a fork runs the native checks and skips this step, because it receives no model or publication credentials.

## Open a PR and read the result

Open a same-repository pull request with a small change. When the native checks pass, Landing reads the candidate and publishes one advisory status comment, updating that comment on later candidates; new findings appear at the relevant lines through native GitHub `COMMENT` reviews. A clean result can say `No blocking findings.` without adding code comments.

Read the workflow and the pull request together. `status: completed` means the task finished and its required publication was verified; `decision` records `allow`, `block`, or `inconclusive`. Review guidance asks the agent to leave the candidate unchanged, and your team decides what to accept using the findings and independent checks.

An unrelated or unauthorized event returns `status: skipped` without starting a task. Execution or required publication failure gives the Action a failed outcome, which this workflow reports as a warning. A successful workflow does not prove that feedback was delivered; if no status comment appears, start with the workflow logs and [Troubleshooting](guides/troubleshooting.md).

Continue with [CI integration](guides/ci.md) for scope and concurrency, or [GitHub integration](guides/github.md#wire-commands-and-follow-ups) to delegate `/landing fix` and `/landing explain` from comments.

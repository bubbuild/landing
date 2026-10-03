# Use Landing in CI

Let CI prepare the checkout, dependencies, credentials, checks, and skills. Landing runs as a GitHub Action or an ordinary CLI command in that environment. [Your first PR review](../get-started.md) provides a complete GitHub workflow; the [Action reference](../reference/cli.md#github-action) lists its inputs and outputs.

## Prepare execution

Prepare your project with the workflow's existing setup and check steps, then call Landing once. Admission inside the Action authorizes delegated model use, tools, checks, and publication; unauthorized events start no task and publish no refusal. Comments require their author's effective permission. Repository policy reuses GitHub's authorization for native dispatch, push, release, and validated upstream runs. Permission lookup failure fails the Action.

Use a reviewed Action revision and trusted workflow source. Admission does not isolate the runner or protect earlier steps. Native trigger filters, token permissions, and environment protection rules govern workflow execution and credentials. Authorization to review a fork PR does not make its setup code trusted.

For execution that requires human approval, configure a GitHub environment's required reviewers and branch restrictions, store credentials there, and reference it on the executing job:

```yaml
environment:
  name: landing
  deployment: false
```

GitHub enforces these rules before starting the job or releasing environment secrets, without creating deployment records. Required-reviewer availability depends on the repository's visibility and plan. An environment without required reviewers does not restrict comment authors. See [GitHub environments](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/control-deployments#using-environments-without-deployments). A composite Action cannot configure the calling job's environment.

The Action installs its own isolated runtime, uses your prepared project tools and authenticated gh, and does not install project dependencies or skills. Prepare skills with your existing checkout actions or gh commands, then add their discovery roots with `LANDING_SKILL_DIRS`. To restrict available skills, configure the mode's `allowed_skills`; see [Mode capabilities](../reference/configuration.md#mode-capabilities).

Start with explanation or advisory review. Keep required native checks independent. For automatic GitHub feedback, set `continue-on-error: true` on the Action step and emit a warning when `steps.landing.outcome == 'failure'`, as in the first-review example. The task retains its failure and logs; the workflow can continue. Set the Action's step timeout shorter than the job timeout so a timed-out task leaves time for the warning and artifact upload. Omit this property for explicit delegations that must fail the job. [GitHub integration](github.md#choose-who-can-delegate) covers caller policy, protected sources, and publishing identities. Fork PRs can run native CI without model or publication credentials.

## Select scope

Use your CI system's path filters and job conditions before invoking Landing. GitHub Actions provides `paths` and `paths-ignore`; [paths-filter](https://github.com/dorny/paths-filter) supports job-level groups. Pass the selected scope, exclusions, and check conclusions through `instruction`, then let the agent fetch relevant evidence.

```text
Review the documentation changes for accurate commands and configuration. Generated site/** output is excluded. Native checks passed for the supplied checkout revision; inspect related source only when it resolves a specific claim.
```

Record the actual checked revision separately from the PR head. A merge checkout can cover a candidate with a different SHA. When reviewing changes to Landing itself, install the candidate runtime in fresh CI; switching a workspace does not reload an already installed runtime.

## Cancel superseded reviews

Put concurrency on the calling workflow before checks delay agent admission. Group by repository and PR, without the SHA, event name, or run ID, and set `cancel-in-progress: true`. The [first-review example](../get-started.md#add-the-workflow) shows this arrangement. Keep native checks in a separate workflow if they must continue for old candidates.

The calling workflow owns concurrency; a composite Action cannot configure it.

For explicit delegations that must be preserved, GitHub.com supports `queue: max` with `cancel-in-progress: false`, retaining up to 100 pending runs. Without `queue: max`, a new pending run replaces the single pending run. Check availability on GitHub Enterprise Server, or use Landing's server queue. See [GitHub concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency).

## Use an ordinary CI command

Install Landing with `uv tool install "landing==0.1.2"` and configure the model on the executing host. Run commands from your project checkout or select one with `--workspace`.

Preserve the native check's exit status when asking for an explanation:

```bash
status=0
make acceptance > acceptance.log 2>&1 || status=$?
if [ "$status" -ne 0 ]; then
  landing explain "Explain this failure and identify the next check." --input acceptance.log --json --output explanation.json || true
fi
exit "$status"
```

To evaluate a candidate with required checks:

```bash
export LANDING_DB="$PWD/.ci-state/landing.sqlite3"
landing review "Review the candidate against the acceptance criteria." --input acceptance.txt --input candidate.diff --check "make acceptance" --json --output review.json
```

CLI review returns nonzero for execution failure, `block`, or `inconclusive`. Put advisory feedback in a separate step when it should not affect native acceptance. Retain the result, relevant logs, workspace changes, and SQLite as CI artifacts. Each database has one worker; separate jobs can use separate databases. [Develop and dogfood](../development.md) describes Landing's own setup and feedback loop.

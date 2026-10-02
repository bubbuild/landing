# Use Landing in CI

Your CI system prepares checkout, dependencies, credentials, native checks, and skills. Run Landing as a GitHub Action or an ordinary CLI command in that environment. Start with an explanation or advisory review; keep acceptance in your team's hands.

## GitHub Action

Use Landing's composite Action with a prepared checkout and model configuration. Replace the release reference with the reviewed commit or tag you want to run.

```yaml
name: Review
on:
  pull_request:
permissions:
  contents: read
  pull-requests: write
jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Prepare project dependencies and native checks
        run: make setup && make acceptance
      - uses: PsiACE/landing@YOUR_REVIEWED_COMMIT
        id: landing
        env:
          GH_TOKEN: ${{ github.token }}
          LANDING_MODEL: ${{ vars.LANDING_MODEL }}
          LANDING_API_KEY: ${{ secrets.LANDING_API_KEY }}
        with:
          command: review
          instruction: Review the candidate against repository guidance and the native checks. Publish actionable findings and record a decision.
```

The Action installs its own runtime in an isolated environment. It uses the caller's prepared project tools, authenticated gh, and skill roots. Prepare skills during setup with your normal checkout actions or gh commands, then set `LANDING_SKILL_DIRS`. It does not install project dependencies, skills, or credentials. Run only trusted work with model and publication credentials; forks can keep native CI without those credentials.

| Input | Behavior |
| --- | --- |
| `command` | `review` (default), `fix`, `triage`, or `explain`. Comment events route their own command. |
| `instruction` | Task and acceptance criteria. |
| `repository` | Destination repository, default the current repository. |
| `number` | Issue or PR number; defaults to the event's target when available. |
| `head` | Candidate head, separate from the CI checkout. PR events supply it. |
| `checked-revision` | Actual checked revision, default `github.sha`. Supply the revision your native checks covered. |
| `run-id` | Native workflow run to inspect, default the current run. |
| `delivery-key` | Stable delivery identifier; default the run ID and attempt. |
| `command-prefix` | Comment command prefix, default `/landing`. |
| `checks` | Required shell commands, one per line, for fix or review. |
| `database` | SQLite path, default `RUNNER_TEMP/landing/landing.sqlite3`. |

Outputs are `id`, `status`, `decision`, and `result`. An unrelated event returns `status: skipped` without invoking the model. Failed execution or missing required publication fails the step. Automatic issuer follow-up can complete without a public update when conditions are unchanged. Gate recommendations remain advisory; native checks retain their own status. Read [GitHub integration](github.md) for reviews, inline follow-ups, and publication rules.

Landing's own workflows use `uses: ./` to exercise the same Action from the candidate checkout. They prepare project dependencies, gh, Git identity, authentication, and team skills separately, select per-mode capabilities with `LANDING_CONFIG: .github/landing.yml`, then retain the database as an artifact.

## Choose the review scope

Use your CI system's existing path filters and job conditions to select work before invoking Landing. For GitHub Actions, use `paths` or `paths-ignore` for a whole workflow, or an existing action such as [dorny/paths-filter](https://github.com/dorny/paths-filter) for individual jobs and file groups. Keep required native checks independent. Pass the selected scope, exclusions and relevant check conclusions through `instruction`; let the agent fetch the relevant diff or log when needed instead of injecting complete file or issue lists. Documentation review still uses `review`.

```text
Review the selected documentation changes for accurate commands and configuration. Generated site/** output is excluded; inspect related source when it resolves a specific claim. Native checks have passed for the supplied checkout revision.
```

Landing's Main workflow groups implementation and documentation changes with `paths-filter`. Implementation includes agent prompts, `AGENTS.md`, skills and workflow configuration. Generated `site/**` output is excluded. Native CI runs independently; successful checks with only excluded changes skip automatic feedback. Healthy default-branch checks also skip feedback; native failures receive triage for the affected jobs. A new automatic PR review cancels superseded automatic feedback, leaving native checks independent. Explicit candidate dispatch and comment delegations remain available.

Self-checks install Landing from the candidate checkout. A comment task can instead use the default-branch runtime with a selected PR workspace; changing that workspace does not reload the installed runtime. Start fresh candidate CI to verify changes to Landing itself.

## Ordinary CI commands

Install Landing and configure the model on the executing host. From its source checkout use `uv sync` and prefix commands with `uv run`; use `--workspace` for another checkout.

Retain a native check's exit status when asking for an explanation:

```bash
status=0
make acceptance > acceptance.log 2>&1 || status=$?
if [ "$status" -ne 0 ]; then
  uv run landing explain "Explain this failure and identify the next check." --input acceptance.log --json --output explanation.json || true
fi
exit "$status"
```

Evaluate a candidate with required checks:

```bash
export LANDING_DB="$PWD/.ci-state/landing.sqlite3"
uv run landing review "Review the candidate against the acceptance criteria." --input acceptance.txt --input candidate.diff --check "make acceptance" --json --output review.json
```

Review checks run before evaluation. The CLI exits nonzero for execution failure, `block`, or `inconclusive`. Keep advisory feedback in a separate step when it should not affect native acceptance. Record candidate revision and actual checked revision in the evidence, particularly when CI tests a merge checkout.

## Preserve evidence

Retain results, reports, logs, diffs, and SQLite after the action stops. Include workspace changes when delegating a fix. Separate databases allow independent jobs; each database has one worker. Choose a retention period and record lasting findings in your work system. See [Develop and dogfood](../development.md) for the continuous feedback process.

# Use Landing in CI

Add Landing to a prepared checkout using an ordinary shell command. Your CI system still owns checkout, dependencies, secrets, native checks, and artifacts. Start with an explanation or advisory review, then choose whether its result should affect acceptance.

The commands below assume Landing is installed from its source checkout with `uv sync`, and [model configuration](../reference/configuration.md) is available in the job. Use `--workspace` for a target checkout outside the current directory.

## Explain a failure

Run your native check and retain its exit status. Provide its log to Landing:

```bash
status=0
make acceptance > acceptance.log 2>&1 || status=$?
if [ "$status" -ne 0 ]; then
uv run landing explainer "Explain this failure from the supplied evidence and identify the next check." --input acceptance.log --json --output explanation.json || true
fi
exit "$status"
```

The explanation cannot turn a failed native check into a passing job. Store its output alongside the original log so maintainers can assess both.

## Evaluate a candidate

```bash
export LANDING_DB="$PWD/.ci-state/landing.sqlite3"
uv run landing gatekeeper "Review the candidate against the acceptance criteria." --input acceptance.txt --input candidate.diff --check "make acceptance" --json --output review.json
```

Required checks run before evaluation. The CLI exits nonzero for execution failure, `block`, or `inconclusive`. To use advisory feedback, capture that status in a separate step and keep native check statuses authoritative. Human acceptance remains a separate decision.

Record both the candidate revision and the actual CI checkout revision in the input. On platforms that test a merge candidate, these can differ. A green result without its revision is incomplete evidence.

## Preserve evidence

Retain results, input reports, logs, diffs, and the SQLite database after the action has stopped. Include workspace changes when delegating a fix. Separate databases allow independent jobs; several workers cannot share one database concurrently. Choose a retention period and record long-lived findings in your work system.

Landing's checkout includes a Bash wrapper for this process:

```bash
bash scripts/dogfood.sh gatekeeper "Review this candidate using independent checks." .ci-state/review "uv run pytest tests" "uv run ty check"
```

It saves checks, execution logs, patches, and SQLite history, and asks an explainer about failure while preserving the original exit status. The wrapper requires `timeout`. It runs from the Landing checkout and does not deliver platform replies.

For replies, issues, and candidate PRs, see [Use Landing with GitHub](github.md). For Landing's own continuous loop, see [Develop and dogfood](../development.md).

# From a failed check to a reviewed fix

Use Landing to explain a failure, delegate a repair, and review it against a check you control. This tutorial uses a small command-line program in a disposable workspace. Its missing-argument behavior is broken deliberately.

You need a POSIX host, Python 3.12 or later, uv, a Landing source checkout, and a model API key. Run these commands from the Landing checkout. The example target uses Python; Landing can work with other projects whose tools are available in its execution environment.

## Configure Landing

```bash
uv sync
export LANDING_MODEL="openai:gpt-4.1"
export LANDING_API_KEY="your-provider-api-key"
export LANDING_DB="$PWD/.ci-state/tutorial.sqlite3"
```

These are Landing's current model environment variables. No separate agent application or plugin setup is required. For another provider or API endpoint, see [Configuration](reference/configuration.md).

## Prepare the failure and its acceptance check

```bash
WORKSPACE=$(mktemp -d)
printf '%s\n' "$WORKSPACE"
cat > "$WORKSPACE/greet.py" <<'PY'
import sys

print(f"Hello, {' '.join(sys.argv[1:])}!")
PY

cat > "$WORKSPACE/acceptance.py" <<'PY'
import subprocess
import sys
from pathlib import Path

program = str(Path(__file__).with_name("greet.py"))
valid = subprocess.run([sys.executable, program, "Ada"], capture_output=True, text=True)
assert valid.returncode == 0, valid.stderr
assert valid.stdout == "Hello, Ada!\n", valid.stdout
missing = subprocess.run([sys.executable, program], capture_output=True, text=True)
assert missing.returncode == 2, f"Expected exit 2, got {missing.returncode}"
assert "usage" in missing.stderr.lower(), missing.stderr
assert missing.stdout == "", missing.stdout
print("Acceptance passed.")
PY

uv run python "$WORKSPACE/acceptance.py" > "$WORKSPACE/check.log" 2>&1
```

The last command should fail with `Expected exit 2, got 0`. The acceptance check describes what a user sees: a valid greeting, an error exit for a missing name, usage on stderr, and no success output on failure. It does not constrain the implementation of argument parsing.

## Explain the failed check

```bash
uv run landing explain "Explain the failure. Cite the observed behavior and suggest the next check." --workspace "$WORKSPACE" --input "$WORKSPACE/check.log"
```

Look for an explanation connecting the missing-name invocation to exit code 0, and distinguishing that observation from a hypothesis about the cause. Wording varies with the model. A completed action means it returned an answer; you still judge whether the answer is useful and supported by the evidence.

## Delegate the fix

```bash
uv run landing fix "Fix greet.py. With a name, preserve the greeting and exit 0. Without a name, print usage to stderr, print nothing to stdout, and exit 2. Keep acceptance.py unchanged." --workspace "$WORKSPACE" --input "$WORKSPACE/check.log" --check "python acceptance.py" --json --output .ci-state/tutorial-fix.json
```

Fixer can edit and execute commands in this workspace. Landing runs the required check after the agent finishes. Expect `status: completed` and an explanation of the change and verification. Read `greet.py` and verify that `acceptance.py` still expresses the intended contract. The ordinary CLI keeps the edits locally; it does not commit, push, or open a PR.

If validation fails, inspect the explanation, check records, and files. Landing preserves partial work. Correct the delegation or provide missing context before requesting another action; repeated retries alone are not a repair strategy.

## Review independently

```bash
uv run landing review "Review greet.py against acceptance.py. Check valid and missing-name behavior, and identify any unverified claims." --workspace "$WORKSPACE" --check "python acceptance.py" --json --output .ci-state/tutorial-review.json
uv run python "$WORKSPACE/acceptance.py"
```

Gatekeeper runs the check before reviewing and cannot edit the program. A failed check forces `block`; missing evidence can produce `inconclusive`. Its CLI command exits zero only for a completed action with `allow`. An allow is a recommendation, and the separate command gives you the actual acceptance result.

Accept the candidate only after inspecting the change and the evidence. A wrong recommendation is useful feedback to record, even when the check passes.

## Inspect and continue

```bash
uv run landing action list
```

Copy an ID from the list to inspect it:

```bash
uv run landing action view act_example --json
uv run landing action logs act_example
```

The records use the database configured above. Workspace files remain in the temporary directory printed by `printf '%s\n' "$WORKSPACE"`.

To use this with real work, replace the fixture with your checkout, its acceptance criteria, and its checks. Continue with [Working with Landing](working-with-landing.md), [CI](guides/ci.md), or [GitHub delivery](guides/github.md).

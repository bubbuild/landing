#!/usr/bin/env bash
# Run the checkout's CLI, preserve evidence, and explain failures without changing their exit status.
set -euo pipefail

mode=$1
instruction=$2
mkdir -p "$3"
evidence=$(cd "$3" && pwd)
shift 3
export LANDING_DB="$evidence/landing.sqlite3"
if [[ -z "${BUB_API_BASE:-}" ]]; then unset BUB_API_BASE; fi

if [[ -n "${LANDING_BASE_REVISION:-}" ]] && git cat-file -e "$LANDING_BASE_REVISION^{commit}" 2>/dev/null; then
  git diff --binary "$LANDING_BASE_REVISION" HEAD -- > "$evidence/candidate.diff"
else
  git show --format= --binary HEAD -- > "$evidence/candidate.diff"
fi
git show -s --format=fuller HEAD > "$evidence/commit.txt"
checks=()
if [[ "$mode" == gatekeeper || "$mode" == fixer ]]; then
  for check in "$@"; do checks+=(--check "$check"); done
fi

status=0
timeout --signal=INT --kill-after=30s "${LANDING_ACTION_TIMEOUT_SECONDS:-600}s" \
  uv run landing "$mode" "$instruction" --input "$evidence/candidate.diff" "${checks[@]}" \
  --json --output "$evidence/result.json" > "$evidence/action.log" 2>&1 || status=$?
printf '%s\n' "$status" > "$evidence/exit-status.txt"

# Include new files without staging anything, so a local invocation also preserves the caller's index.
git diff --binary HEAD -- > "$evidence/workspace.diff"
while IFS= read -r -d '' file; do
  diff_status=0
  git diff --no-index --binary -- /dev/null "$file" >> "$evidence/workspace.diff" || diff_status=$?
  if (( diff_status > 1 )); then exit "$diff_status"; fi
done < <(git ls-files --others --exclude-standard -z)

uv run python - "$LANDING_DB" "$evidence" <<'PY'
import json
import sys
from contextlib import closing
from pathlib import Path
from landing.tasks import Tasks

directory = Path(sys.argv[2])
with closing(Tasks(Path(sys.argv[1]))) as tasks:
    actions = tasks.list(100)
    directory.joinpath("actions.json").write_text(json.dumps([action.model_dump() for action in actions], indent=2))
    if actions:
        events = []
        after = 0
        while page := tasks.events(actions[0].id, after, 100):
            events.extend(event.model_dump() for event in page)
            after = page[-1].id
        directory.joinpath("events.json").write_text(json.dumps(events, indent=2))
PY

if (( status != 0 )) && [[ "$mode" != explainer ]]; then
  inputs=(--input "$evidence/action.log" --input "$evidence/workspace.diff")
  for file in result.json events.json; do
    if [[ -f "$evidence/$file" ]]; then inputs+=(--input "$evidence/$file"); fi
  done
  explanation_status=0
  timeout --signal=INT --kill-after=30s "${LANDING_EXPLANATION_TIMEOUT_SECONDS:-300}s" \
    uv run landing explainer "Explain why this action did not pass. Cite the actual validation or execution evidence and recommend the next step. Do not change files." \
    "${inputs[@]}" --json --output "$evidence/explanation.json" > "$evidence/explanation.log" 2>&1 || explanation_status=$?
  printf '%s\n' "$explanation_status" > "$evidence/explanation-exit-status.txt"
fi

uv run python - "$evidence" <<'PY'
import json
import sys
from pathlib import Path

directory = Path(sys.argv[1])
parts = ["# Landing dogfood", f"Action exit code: {directory.joinpath('exit-status.txt').read_text().strip()}"]
for name in ("result.json", "explanation.json"):
    path = directory / name
    if path.exists():
        action = json.loads(path.read_text())
        if "id" in action:
            parts.append(f"## {action['mode'].title()}\n\n{action['id']}: {action['status']}"
                         + (f"; decision: {action['decision']}" if action.get("decision") else ""))
        parts.append(action.get("result") or "No explanation was returned.")
        if action.get("error"):
            parts.append(action["error"]["message"])
parts.append("See the artifact for the candidate diff, workspace diff, logs, events, and SQLite history.")
directory.joinpath("summary.md").write_text("\n\n".join(parts) + "\n")
PY
exit "$status"

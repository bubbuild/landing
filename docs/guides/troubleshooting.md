# Troubleshooting

Begin with the action record, its events, and the native evidence. Use the same database or server as the original request:

```bash
uv run landing action view act_example --json
uv run landing action logs act_example --after 0
```

| Symptom | What to check | Next step |
| --- | --- | --- |
| Invalid request or exit code 2 | Arguments, input files, environment, workspace | Compare with [CLI](../reference/cli.md) and [Configuration](../reference/configuration.md). |
| Model failure or timeout | Action error, provider model and endpoint, request timeout | Correct the provider configuration or task context before an explicit retry. |
| Empty completion | Action error and saved execution history | Treat it as failed work; inspect partial edits before retrying. |
| Fixer validation failed | `validation` events, output, workspace diff | Identify whether the candidate or execution environment caused the failure. |
| Gatekeeper returned nonzero | Status and decision separately | A completed `block` or `inconclusive` is a recommendation, not necessarily an execution error. |
| Another worker owns the database | A service or local action using the same path | Submit through `--server`, or choose a separate database. |
| Remote work continues after Ctrl-C | Action status on the server | Use `action cancel` when you intend to stop it. |
| GitHub task skipped | Native caller permission, trust policy, command prefix or upstream workflow | Use an authorized explicit command; an unrelated status reply does not delegate work. |
| Review cancelled | Current PR head and `github.review_stopped` event | Review the new candidate; a head lookup failure also stops new tools. |
| Duty timed out after pushing | Native timeout annotation, last tool and missing reply | Finish with the candidate and pending CI links; do not wait for the current duty or feedback job. |
| Reply or publication failed | Adapter events, gh authorization, candidate head | Retry with the same database and delivery key; do not delegate the same work again merely to retry delivery. |
| CI passes but advice is wrong | Supplied evidence and the model's interpretation | Record the mistake and its consequence in the work item; improve instructions, tools, or code at the responsible layer. |
| Restored action is interrupted | Worker stopped while the action was active | Inspect workspace files and history before an explicit retry. |

`action retry` creates a new action using the original request snapshot. It does not undo workspace changes. Completed work is never automatically replayed.

For GitHub reviews, compare the candidate head, native checkout revision, and deployed revision separately. A CI run's triggering head does not establish what was checked out. For backup problems, readiness is not replication health; inspect Litestream logs and the configured replica.

Retain a minimal reproducer, observed user impact, relevant revisions, and useful logs when reporting a problem. Remove credentials from material you share.

# Troubleshooting

For local work, read the result and events from the same database:

```bash
landing action view act_example --json
landing action logs act_example
```

Replace the ID with the affected action. For service work, use its [HTTP endpoints](../reference/http.md#resources). Start with the observed failure:

| Symptom | Next step |
| --- | --- |
| Exit code 2 | Check arguments, inputs, workspace, and [configuration](../reference/configuration.md). |
| HTTP connection failure or timeout | Check the server URL, availability, and proxy. |
| Model failure or empty completion | Inspect the action error and saved model diagnostics; verify provider settings before retrying. |
| Fix validation failed | Read validation events and inspect the workspace diff to distinguish repair and environment failures. |
| Review returned nonzero | Read status and decision separately: completed `block` or `inconclusive` is a recommendation. |
| Another worker owns the database | Use the executing service's HTTP API or select a separate database. |
| A newer Landing release created this database | Upgrade Landing; older releases leave newer databases unchanged. |
| Work continues after you stop watching | Stopping `action watch` leaves work running. Cancel through its HTTP/SDK host, or use Ctrl-C on the executing CLI. |
| GitHub task skipped | Check caller permission, trust policy, explicit command prefix, and upstream workflow source. |
| Review cancelled | Inspect the current PR head and `github.review_stopped`; an unverifiable head also stops tools. |
| Required publication failed | Inspect gh authorization and the requested destination; reuse the same database and delivery key when retrying delivery. |
| Advice is wrong despite passing checks | Record the concrete mistake in the work item and improve guidance, tools, or code where it belongs. |
| Restored action is interrupted | Inspect its history and workspace before retrying. |

`action retry` creates a new task from the original request and does not undo edits. Completed tasks do not replay automatically. See [CLI exit codes](../reference/cli.md#exit-codes-and-interruption).

For reviews, distinguish the candidate, actual CI checkout, and deployed revision. For backup problems, inspect Litestream logs and the replica; readiness does not measure replication health.

When asking for help, include the smallest reproduction, observed impact, relevant revisions, and useful logs with credentials removed. Share missing evidence explicitly so another person can help resolve it.

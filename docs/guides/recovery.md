# Retain execution history

Local CLI execution and Actions runners store action requests, results, events, and model history in SQLite. Workspace files and published GitHub work remain separate. The local service also stores its waiting webhook inbox in that database. GHA-only intake has no local job database.

## Local work

Keep the database selected by `--db` or `LANDING_DB` and the task workspace. Stop its executing process before copying a closed database, or use SQLite-aware backup tooling for live backups. A database copy alone does not recover workspace edits or external publications.

Read saved work with `landing action view ID --json` and `landing action logs ID`. Interrupted work requires inspection before retry: files and public replies may already exist. Retrying a recorded action does not undo edits.

## GitHub Actions

The executor uploads its SQLite database and supporting evidence as artifacts. Choose retention deliberately and download important evidence before expiry. GitHub owns workflow run status, cancellation, and reruns; native comments, reviews, and PRs retain published outcomes separately.

Before rerunning an interrupted job or redelivering a webhook, inspect its repository changes and existing publications. Reusing a delivery identity can recover native reply evidence, but does not prove that every side effect ran exactly once. A fresh runner cannot reconstruct missing validation history from a comment alone.

## Local service outages

Keep the local database and prepared checkout. Waiting events resume when the service starts. Native Bub process cleanup stops an active worker child during graceful shutdown; started tasks retain their cancellation or interruption record. A crash is recovered when execution next opens the database. Inspect files and publications before re-delegating interrupted work. Failed inbox entries retain diagnostics rather than retrying in a loop; receiver logs identify the entry.

Use the same database and workspace for thread history continuity. GitHub session names now include the repository as well as the conversation; older repository-less tapes are retained but are not automatically renamed or merged. A shared tape-store plugin changes model-history storage, not the local inbox or action records, and does not by itself unify session identities across different workspaces.

## GHA receiver outages

Inspect GitHub's webhook delivery history and Actions runs. A failed HTTP response can follow a successful remote dispatch, so check for an existing run before redelivering. There is no local queue to drain, receipt to reset, or replica to restore. Test remote delivery in the environment where recovery matters; local fixtures do not establish it.

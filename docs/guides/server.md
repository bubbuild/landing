# Run a local worker or Actions receiver

`landing serve` accepts GitHub webhooks and selects an execution location with `--runner local|gha` or `LANDING_RUNNER`. Local execution is the default. The local worker and Actions CLI call the same execution API, which checks the original caller, selects a mode, and verifies native publication.

## Run locally without Actions

Prepare a checkout, its project tools, model access, and gh authentication. The worker uses that checkout; it does not clone repositories or reset local edits.

```bash
export LANDING_GITHUB_WEBHOOK_SECRET="your-random-webhook-secret"
export LANDING_MODEL="openai:gpt-4.1"
export LANDING_API_KEY="your-provider-api-key"
export GH_TOKEN="your-repository-token"
landing --db /srv/landing/landing.sqlite3 serve --runner local --workspace /srv/project
```

Accepted events enter a SQLite inbox before HTTP returns. A serial worker executes them in separate processes through Bub's environment and process lifecycle. The installed worker loads the event from SQLite and calls that API directly, without a temporary event file or CLI handoff. A same-named module in the checkout cannot replace the installed worker. The worker identifies the prepared checkout through gh and rejects events for another repository before model execution. It does not manufacture Actions identity or outputs.

Local jobs share the selected database and workspace. Task records and Bub tapes survive process and service restarts; later work in the same GitHub thread and workspace can read earlier history. On shutdown, owned processes stop and waiting events remain queued. Failed events retain a diagnostic in the inbox and receiver logs rather than retrying continuously. Inspect saved actions with `landing --db /srv/landing/landing.sqlite3 action list --json`.

Local mode runs with the service account's filesystem and process permissions; it is not a sandbox. Keep callers trusted and configure environment isolation deliberately when needed.

Keep the database and workspace persistent, and use one worker for that inbox. Model settings, Git identity, credentials, and trusted skills belong to the executing environment. Global `--skill-dir` roots are passed to local jobs. [Configuration](../reference/configuration.md#bub-plugins) describes optional native environment and storage plugins.

## Place work on GitHub Actions

```bash
export LANDING_GITHUB_WEBHOOK_SECRET="your-random-webhook-secret"
export GH_TOKEN="your-actions-dispatch-token"
landing serve --runner gha --host 127.0.0.1 --port 8080
```

This receiver mode needs neither a local checkout nor a database or model key. The GitHub identity needs Actions write access to each target repository. Prepared gh login also works. The default executor is `landing-execute.yml` on the event repository's default branch; `LANDING_GITHUB_WORKFLOW` and `LANDING_GITHUB_REF` override the workflow and trusted ref.

Use Landing's [executor workflow](https://github.com/bubbuild/landing/blob/main/.github/workflows/landing-execute.yml) as a starting point. It accepts `event`, `signature`, `event_name`, and `delivery`. Outside Landing's repository, adapt preparation and replace `uses: ./` with a pinned Landing revision. Configure the same webhook secret, model access, publication credentials, and task policy on the executor. GitHub stores the workflow run; the runner uploads its SQLite execution history as an artifact. It does not download previous databases automatically.

## Connect GitHub

Configure a repository webhook at `/webhooks/github`, choose `application/json`, and use the same webhook secret. Subscribe to comments, PRs, workflow runs, and Dependabot alerts as needed. If changing the command prefix, configure `LANDING_GITHUB_COMMAND_PREFIX` consistently on the receiver and worker.

The original body and signature reach execution unchanged. The runner verifies them and checks current caller permissions; message fields do not configure authorization policy. Set `LANDING_GITHUB_TRUST`, allowed upstream workflows, and dependency-repair policy in the execution environment.

Landing's repository keeps Main for checks and the executor for webhook jobs plus weekly/manual maintenance. Remove overlapping listeners in other repositories when adopting this receiver. Provide HTTPS and appropriate ingress limits through your proxy.

Post `/landing explain What caused this failure?` as an authorized writer. In local mode, inspect receiver logs, SQLite action history, and the native reply. In GHA mode, inspect the workflow run and artifacts. `/up` checks local worker availability or, for GHA placement, HTTP liveness. See [HTTP semantics](../reference/http.md), [Deployment](deploy.md), and [execution history](recovery.md).

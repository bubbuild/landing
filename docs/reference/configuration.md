# Configuration

Configure Landing in the environment where its worker runs. Remote clients need the server URL and bearer token; model settings belong on the server. CLI options override their corresponding environment settings.

## Model

Landing currently uses `BUB_*` names for model settings. Set them directly for Landing; no separate application, configuration file, or plugin setup is required.

| Variable | Purpose |
| --- | --- |
| `BUB_MODEL` | Provider and model identifier, such as `openai:gpt-4.1`. |
| `BUB_API_KEY` | API key for the selected provider. |
| `BUB_API_BASE` | Optional custom provider endpoint. Leave unset to use the provider default. |
| `BUB_MODEL_TIMEOUT_SECONDS` | Model request timeout; the bundled CI and Compose configuration use 120 seconds. |
| `BUB_COMPLETION_ARGS` | Provider completion options as a JSON object. |

```bash
export BUB_MODEL="deepseek:deepseek-v4-pro"
export BUB_API_BASE="https://api.deepseek.com"
export BUB_API_KEY="your-provider-api-key"
export BUB_COMPLETION_ARGS='{"reasoning_effort":"none"}'
```

Choose a model identifier and options supported by your provider. Landing sets no agent step budget. Request timeouts, required-check timeouts, cancellation, and CI job timeouts still apply.

## Execution and service

| Variable | Purpose or default |
| --- | --- |
| `LANDING_DB` | Local database, default `~/.local/share/landing/landing.sqlite3`; image default `/storage/landing.sqlite3`. |
| `LANDING_SERVER` | Remote service URL for the CLI. |
| `LANDING_TOKEN` | Server bearer token and remote client token; required to bind beyond localhost. |
| `LANDING_GITHUB_REPOSITORY` | Optional `OWNER/REPO` for scoped gh tools. |
| `BASE_URL` | Public HTTP(S) origin for service pagination links. No credentials, path, query, or fragment. |

The default workspace is the current directory locally. A service registers workspace names with `serve --workspace NAME=PATH`.

## GitHub workflows and runner

| Setting | Purpose |
| --- | --- |
| Repository variable `LANDING_MODEL` | Mapped to `BUB_MODEL` in the bundled workflows. |
| Repository variable `LANDING_API_BASE` | Optional endpoint mapped to `BUB_API_BASE`. |
| Repository variable `LANDING_COMPLETION_ARGS` | Completion options mapped to `BUB_COMPLETION_ARGS`, default `{}`. |
| Repository secret `LANDING_API_KEY` | Mapped to `BUB_API_KEY`. |
| `GH_TOKEN` or gh login | Platform authorization, independent of model configuration. |
| `LANDING_CHECK_WORKFLOW` | Workflow to dispatch after workflow-token candidate publication; the bundled setup uses `main.yml`. |

See [GitHub](../guides/github.md) for event wiring and permissions. For the Bash dogfood wrapper, `LANDING_BASE_REVISION` selects the base diff, `LANDING_ACTION_TIMEOUT_SECONDS` defaults to 600, and `LANDING_EXPLANATION_TIMEOUT_SECONDS` defaults to 300.

## Container replication

| Variable | Purpose |
| --- | --- |
| `LITESTREAM_REPLICA_URL` | Replica destination; Compose defaults to `file:///replica/landing`. |
| `LITESTREAM_CONFIG` | Optional mounted Litestream configuration path. |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `AWS_REGION` | Storage credentials and region when using the corresponding replica provider. |

Compose passes only the variables listed in `compose.yaml`. To use `BUB_API_BASE`, `BUB_COMPLETION_ARGS`, or another additional setting in that container, add it to the service's `environment` through a Compose override. Exporting it on the host alone does not forward it into the container. See [Deploy Landing](../guides/deploy.md) and [Replication and recovery](../guides/recovery.md).

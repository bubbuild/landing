# Deploy Landing

Build the image from the Landing checkout. No prebuilt registry image is required for this path. Configure the model and prepare a workspace with the dependencies your delegated tasks need.

## Start with Compose

```bash
export LANDING_TOKEN="your-server-token"
export BUB_MODEL="openai:gpt-4.1"
export BUB_API_KEY="your-provider-api-key"
docker compose up --build -d
```

The image serves HTTP on port 80 as UID 1000. `/up` checks the worker and database; Docker also uses it for its healthcheck. The database is `/storage/landing.sqlite3`, and the default workspace is `/storage/workspace`. The image includes Git and uv for preparing and validating checkouts. Register additional workspaces with the ordinary `serve --workspace NAME=PATH` option. Each database still has one worker, regardless of the container's CPU allocation.

The default workspace starts empty. Prepare a checkout under `/storage/workspace` using your normal provisioning process before delegating project work. The image includes Git, gh, uv, and Python; add other toolchains needed by your project.

After preparing the workspace, call the service from a Landing source checkout with `uv sync` completed and the same `LANDING_TOKEN`:

```bash
export LANDING_SERVER="http://127.0.0.1:8080"
uv run landing explainer "Explain the latest validation failure." --workspace default --input check.log
```

Use a saved UTF-8 failure log for `check.log`.

Compose forwards the environment listed in `compose.yaml`. See [Configuration](../reference/configuration.md) for adding a custom endpoint or provider options through an override.

## Deploy with ONCE

The image follows [ONCE's application contract](https://github.com/basecamp/once#making-a-once-compatible-application): port 80, `/up`, and persistent application data under `/storage`. ONCE supplies `BASE_URL`; Landing uses it for public pagination links. TLS terminates at ONCE's proxy.

After publishing your image to your own registry, deploy it with the standard ONCE CLI:

Configure a replica destination and its credentials first as described in [Replication and recovery](recovery.md).

```bash
once deploy registry.example.com/team/landing:0.0.0 --host landing.example.com --env "LANDING_TOKEN=$LANDING_TOKEN" --env "BUB_MODEL=$BUB_MODEL" --env "BUB_API_KEY=$BUB_API_KEY" --env "LITESTREAM_REPLICA_URL=$LITESTREAM_REPLICA_URL" --env "AWS_REGION=$AWS_REGION" --env "AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID" --env "AWS_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY"
```

Enable ONCE's full-volume backups in its settings to cover workspace files as well as SQLite. The image uses ONCE's default paused-container backup behavior; it has no pre-backup hook claiming mutable workspaces are safe to copy live. Litestream's SQLite replica and ONCE's full-volume backups serve different recovery needs. See [Litestream's container guide](https://litestream.io/guides/docker/) and [replication command](https://litestream.io/reference/replicate/) for the upstream lifecycle and restore behavior.

The ONCE contract is supported by the image; deployment to your own ONCE host and remote replica still needs acceptance in that environment. See [Replication and recovery](recovery.md) for storage and restoration.

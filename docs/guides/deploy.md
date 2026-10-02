# Deploy Landing

Build the image from the Landing checkout. Prepare model settings and a project workspace with the tools its tasks need.

## Start with Compose

```bash
export LANDING_TOKEN="your-server-token"
export LANDING_MODEL="openai:gpt-4.1"
export LANDING_API_KEY="your-provider-api-key"
docker compose up --build -d
curl --fail http://127.0.0.1:8080/up
```

The image serves port 80 as UID 1000. SQLite lives at `/storage/landing.sqlite3`; the default workspace is `/storage/workspace`. It starts empty, so prepare your checkout there before delegating. Git, gh, uv, and Python are included; add other project toolchains through your normal provisioning process. Register additional workspaces with `serve --workspace NAME=PATH`.

From a Landing source checkout with `uv sync` completed and the same token, submit a task using a saved failure log:

```bash
export LANDING_SERVER="http://127.0.0.1:8080"
uv run landing explain "Explain this validation failure." --workspace default --input check.log
```

Compose forwards only the variables listed in `compose.yaml`. Use an override for additional settings or mounts; see [Configuration](../reference/configuration.md#container-replication). `/up` is also the container healthcheck. Each database has one worker.

## Deploy with ONCE

The image follows [ONCE's application contract](https://github.com/basecamp/once#making-a-once-compatible-application): port 80, `/up`, and persistent data under `/storage`. ONCE supplies `BASE_URL` for public pagination links and terminates TLS at its proxy.

Publish the image to your registry and configure the [replica destination](recovery.md#replicate-the-database) and credentials before deployment:

```bash
once deploy registry.example.com/team/landing:reviewed --host landing.example.com --env "LANDING_TOKEN=$LANDING_TOKEN" --env "LANDING_MODEL=$LANDING_MODEL" --env "LANDING_API_KEY=$LANDING_API_KEY" --env "LITESTREAM_REPLICA_URL=$LITESTREAM_REPLICA_URL" --env "AWS_REGION=$AWS_REGION" --env "AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID" --env "AWS_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY"
```

Enable ONCE's full-volume backups for workspace files. Landing uses paused-container backup behavior. Litestream protects SQLite separately; [Recovery](recovery.md) describes both paths. Verify deployment and remote replication in your actual environment.

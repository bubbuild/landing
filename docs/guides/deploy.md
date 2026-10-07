# Deploy Landing

Use `ghcr.io/bubbuild/landing:latest` for the latest stable release. Pin a release tag or digest to keep the deployed image fixed. Prepare model settings and a project workspace with the tools its tasks need.

## Start with Compose

```bash
export LANDING_TOKEN="your-server-token"
export LANDING_MODEL="openai:gpt-4.1"
export LANDING_API_KEY="your-provider-api-key"
docker compose pull
docker compose up --no-build -d
curl --fail http://127.0.0.1:8080/up
```

The image serves port 80 as UID 1000. SQLite lives at `/storage/landing.sqlite3`; the default workspace is `/storage/workspace`. It starts empty, so prepare your checkout there before delegating. Git, gh, uv, and Python are included; add other project toolchains through your normal provisioning process. Register additional workspaces with `serve --workspace NAME=PATH`.

Submit through the HTTP API with the same token:

```bash
curl -i http://127.0.0.1:8080/v1/actions -H "Authorization: Bearer $LANDING_TOKEN" -H "Content-Type: application/json" -d '{"mode":"explainer","instruction":"Explain the validation failure in this workspace."}'
```

The response contains a task receipt; [Run the server](server.md) explains result inspection and cancellation.

Use `compose.yaml` from the Landing repository. `LANDING_IMAGE` selects another image or digest. For a local build, run `LANDING_IMAGE=landing:local SETUPTOOLS_SCM_PRETEND_VERSION="$(uv run --reinstall-package landing landing --version)" docker compose up --build -d` from the repository checkout. Compose forwards only the listed variables; use an override for additional settings or mounts. See [Configuration](../reference/configuration.md#container-replication). `/up` is also the container healthcheck. Each database has one worker.

## Deploy with ONCE

The image follows [ONCE's application contract](https://github.com/basecamp/once#making-a-once-compatible-application): port 80, `/up`, and persistent data under `/storage`. ONCE supplies `BASE_URL` for public pagination links and terminates TLS at its proxy.

Configure the [replica destination](recovery.md#replicate-the-database) and credentials before deployment:

```bash
once deploy ghcr.io/bubbuild/landing:latest --host landing.example.com --env "LANDING_TOKEN=$LANDING_TOKEN" --env "LANDING_MODEL=$LANDING_MODEL" --env "LANDING_API_KEY=$LANDING_API_KEY" --env "LITESTREAM_REPLICA_URL=$LITESTREAM_REPLICA_URL" --env "AWS_REGION=$AWS_REGION" --env "AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID" --env "AWS_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY"
```

Enable ONCE's full-volume backups for workspace files. Landing uses paused-container backup behavior. Litestream protects SQLite separately; [Recovery](recovery.md) describes both paths. Verify deployment and remote replication in your actual environment.

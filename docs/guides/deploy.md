# Deploy the webhook receiver

Prepare the [executor and credentials](server.md) first. Use an image containing webhook support; older releases serve the former local execution API. Choose local execution or GHA placement. Model keys, project tools, and execution history belong at the selected execution location.

## Start with Compose

```bash
export LANDING_GITHUB_WEBHOOK_SECRET="your-random-webhook-secret"
export GH_TOKEN="your-actions-dispatch-token"
docker compose pull
docker compose up --no-build -d
curl --fail http://127.0.0.1:8080/up
```

Use `compose.yaml` from the repository. `LANDING_IMAGE` selects a release or digest. For this unreleased checkout, build with `LANDING_IMAGE=landing:local SETUPTOOLS_SCM_PRETEND_VERSION="$(uv run landing --version)" docker compose up --build -d`.

The Compose example explicitly selects `LANDING_RUNNER=gha`. In that mode the image requires no persistent volume, database path, or model settings. It serves port 80 as UID 1000, includes gh, and uses `/up` as its healthcheck. Compose forwards only the listed variables; add an override for other prepared configuration.

Expose `/webhooks/github` through HTTPS. Configure the same webhook secret in GitHub, the receiver, and the trusted executor. The dispatch target defaults to `landing-execute.yml` on each event repository's default branch; optional workflow and ref settings override it.

## Run a local worker in a container

Select `LANDING_RUNNER=local`, mount the prepared checkout, and set `LANDING_WORKSPACE` to its container path. Mount persistent storage for `LANDING_DB` and provide the local model and publication settings. Ensure UID 1000 can write the database and checkout. Prepare additional project toolchains in the image; Landing does not install them or reset workspaces between jobs. Native environment plugins are optional and must be installed in Landing's interpreter.

## Deploy with ONCE

```bash
once deploy ghcr.io/bubbuild/landing:latest --host landing.example.com --env "LANDING_RUNNER=gha" --env "LANDING_GITHUB_WEBHOOK_SECRET=$LANDING_GITHUB_WEBHOOK_SECRET" --env "GH_TOKEN=$GH_TOKEN"
```

A GHA-only receiver needs no backup or restore step. Local workers retain their database and workspace as described in [execution history](recovery.md). Restarting it cannot cancel a workflow that GitHub has already accepted. A failed or timed-out webhook remains visible in GitHub's delivery history; inspect Actions before redelivery. Verify actual dispatch and publication in your deployment, not just `/up`.

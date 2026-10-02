# Run the server

Use a service for a shared queue or HTTP admission point. Prepare checkouts, dependencies, model settings, and skills on the executing host. From a Landing checkout with `uv sync` completed:

## Start with registered workspaces

```bash
export LANDING_TOKEN="your-server-token"
uv run landing --db /var/lib/landing/landing.sqlite3 serve --host 127.0.0.1 --port 8080 --workspace candidate=/srv/landing/candidate
```

Requests select registered names rather than arbitrary paths. `default` points to the server's current directory. Each database has one worker with serial execution; submit through the service rather than starting another local worker on that database.

Tools and checks use the host environment. Add trusted skills with global `--skill-dir` options; each action also loads its workspace's instructions and skills. Remote callers cannot add local skill roots. Binding beyond localhost requires `LANDING_TOKEN`; provide TLS through your proxy.

## Submit from the CLI

In another terminal with the same token:

```bash
export LANDING_TOKEN="your-server-token"
export LANDING_SERVER="http://127.0.0.1:8080"
uv run landing review "Review the prepared checkout." --workspace candidate --check "make acceptance" --json
uv run landing explain "Explain this failure." --workspace candidate --input test-output.txt --detach --json
```

Detached creation returns an action immediately. Replace `act_example` with its ID:

```bash
uv run landing action watch act_example --exit-status
uv run landing action cancel act_example
```

Ctrl-C during a remote wait leaves the work running; cancel it explicitly when intended. See [CLI reference](../reference/cli.md) for exit semantics.

## Admit webhook work

```bash
curl -i http://127.0.0.1:8080/v1/actions -H "Authorization: Bearer $LANDING_TOKEN" -H "Content-Type: application/json" -H "Idempotency-Key: delivery-example-1" -d '{"mode":"explainer","instruction":"Explain the failed validation.","workspace":"candidate","input":[{"type":"text","text":"Invalid arguments returned exit code 0."}]}'
```

Admission persists the task before returning `201` and `Location`. Read that resource for the eventual result. Identical admission with the same key reuses the action; changed content returns `409`.

This endpoint accepts Landing's normalized [HTTP contract](../reference/http.md). Your platform adapter verifies its provider signature, translates the payload, and delivers the result. It is not a raw GitHub or Linear webhook receiver.

## Verify readiness

```bash
curl --fail http://127.0.0.1:8080/up
```

`/up` checks the worker and database; `/healthz` checks HTTP liveness. Both are unauthenticated. See [Deployment](deploy.md) for containers and [Recovery](recovery.md) for storage.

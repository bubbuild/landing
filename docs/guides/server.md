# Run the server

Use a service for a shared queue or HTTP admission point. Prepare checkouts, dependencies, model settings, and skills on the executing host. Install the CLI with `uv tool install landing`.

## Start with registered workspaces

```bash
export LANDING_TOKEN="your-server-token"
landing --db /var/lib/landing/landing.sqlite3 serve --host 127.0.0.1 --port 8080 --workspace candidate=/srv/landing/candidate
```

Requests select registered names rather than arbitrary paths. `default` points to the server's current directory. Each database has one worker with serial execution; submit through the service rather than starting another local worker on that database.

Tools and checks use the host environment. Add trusted skills with global `--skill-dir` options; each action also loads its workspace's instructions and skills. Remote callers cannot add local skill roots. Binding beyond localhost requires `LANDING_TOKEN`; provide TLS through your proxy.

## Explore the API

Open [the API reference](http://127.0.0.1:8080/docs) to inspect requests and try the API with Scalar. When `LANDING_TOKEN` is configured, enter its value as the bearer token before sending a request. Download the OpenAPI document from the page or fetch `/openapi.json`. Documentation is public and contains no task history or credentials; API calls still require the token.

## Admit webhook work

```bash
curl -i http://127.0.0.1:8080/v1/actions -H "Authorization: Bearer $LANDING_TOKEN" -H "Content-Type: application/json" -H "Idempotency-Key: delivery-example-1" -d '{"mode":"explainer","instruction":"Explain the failed validation.","workspace":"candidate","input":[{"type":"text","text":"Invalid arguments returned exit code 0."}]}'
```

Admission persists the task before returning `201` and `Location`. The request returns without waiting for model execution. Read that resource when you need the eventual result. Identical admission with the same key reuses the action; changed content returns `409`.

This endpoint accepts Landing's normalized [HTTP contract](../reference/http.md). Your platform adapter verifies its provider signature, translates the payload, and delivers the result. It is not a raw GitHub or Linear webhook receiver.

## Inspect or cancel work

Use the returned `Location`, replacing `act_example` with the action ID:

```bash
curl http://127.0.0.1:8080/v1/actions/act_example -H "Authorization: Bearer $LANDING_TOKEN"
curl -X POST http://127.0.0.1:8080/v1/actions/act_example/cancellation -H "Authorization: Bearer $LANDING_TOKEN"
```

Disconnecting the caller leaves accepted work running. Cancellation goes through the service that executes it. Stopping the service preserves queued work and interrupts active work; queued work resumes after restart. Inspect interrupted work before retrying.

## Verify readiness

```bash
curl --fail http://127.0.0.1:8080/up
```

`/up` checks the worker and database; `/healthz` checks HTTP liveness. Both are unauthenticated. See [Deployment](deploy.md) for containers and [Recovery](recovery.md) for storage.

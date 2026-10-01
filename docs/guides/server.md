# Run the server

Use a service when callers need a shared queue or an HTTP admission point. Prepare the target checkouts, their dependencies, and model configuration on the host before starting Landing.

## Register workspaces

```bash
export LANDING_TOKEN="your-server-token"
uv run landing --db /var/lib/landing/landing.sqlite3 serve --host 127.0.0.1 --port 8080 --workspace candidate=/srv/landing/candidate
```

The service also registers `default` as its current directory. A remote request selects a registered name, not an arbitrary filesystem path. Checkout preparation belongs to the caller or its platform adapter. The service runs one worker per database and executes actions serially. Use the remote interface to submit work to that worker rather than starting another local worker on its database.

Tools and required checks use the host environment. This endpoint is intended for trusted callers and prepared workspaces. Binding beyond localhost requires `LANDING_TOKEN`; expose it through your chosen TLS proxy.

## Call it from the CLI

In another terminal with the same token:

```bash
export LANDING_TOKEN="your-server-token"
export LANDING_SERVER="http://127.0.0.1:8080"
uv run landing gatekeeper "Review the prepared checkout." --workspace candidate --check "make acceptance" --json
uv run landing explainer "Explain this failure." --workspace candidate --input test-output.txt --detach --json
```

Detached creation returns an action record immediately. Copy its ID to wait or cancel explicitly:

```bash
uv run landing action watch act_example --exit-status
uv run landing action cancel act_example
```

Ctrl-C during a remote wait stops waiting and leaves the action running. Ctrl-C during a local creation cancels the action. `--detach` is remote-only.

## Admit webhook work

```bash
curl -i http://127.0.0.1:8080/v1/actions -H "Authorization: Bearer $LANDING_TOKEN" -H "Content-Type: application/json" -H "Idempotency-Key: delivery-example-1" -d '{"mode":"explainer","instruction":"Explain the failed validation.","workspace":"candidate","input":[{"type":"text","text":"The CLI exited with code 0 for invalid arguments."}]}'
```

Landing persists admission before returning `201` and a `Location` header. Read the action at that location for its result. The endpoint accepts Landing's normalized contract; a provider integration verifies its own signature, translates the payload, and delivers the eventual result. It is not a raw GitHub or Linear webhook receiver.

Use an idempotency key derived from the delegation you want to perform. Repeated identical admission returns the existing action; conflicting content returns `409`. See [HTTP reference](../reference/http.md) for polling, pagination, cancellation, errors, and retries.

## Check readiness

```bash
curl --fail http://127.0.0.1:8080/up
```

`/up` checks the worker and database; `/healthz` checks HTTP liveness. Both are unauthenticated. Use [Deploy Landing](deploy.md) for containers and [Replication and recovery](recovery.md) for durable storage.

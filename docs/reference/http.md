# HTTP reference

Submit an action, then read its record for progress and results. The service saves accepted work before execution and uses the same requests and results as the CLI. When connecting a platform webhook, verify its signature and translate its payload to the request below before submitting it.

## Authorization and admission

When `LANDING_TOKEN` is configured, action endpoints require `Authorization: Bearer TOKEN`. JSON admission requires a JSON Content-Type such as `application/json`, has a 16 MiB limit, and rejects unknown fields.

```json
{
  "mode": "gatekeeper",
  "instruction": "Review the candidate against these acceptance criteria.",
  "workspace": "candidate",
  "input": [
    {"type": "text", "text": "Missing arguments must exit 2."},
    {"type": "file", "name": "check.log", "media_type": "text/plain", "content": "Acceptance passed.\n"}
  ],
  "checks": ["make acceptance"]
}
```

| Field | Contract |
| --- | --- |
| `mode` | Required: `issuer`, `fixer`, `gatekeeper`, or `explainer`. |
| `instruction` | Optional string; a nonblank instruction or nonempty input is required. |
| `workspace` | Registered name, default `default`; the service prepares no checkout. |
| `input` | Text or file snapshots, default empty; files require `name` and `content`, with `media_type` defaulting to `text/plain`. |
| `checks` | Nonblank commands, default empty; supported only by fixer and gatekeeper. |

`POST /v1/actions` commits the request before returning `201` with an action record and `Location`. Optional `Idempotency-Key` accepts 1–256 characters. The same key and request return the existing action with `200`; conflicting content returns `409`. Retry admission also supports the header.

## Resources

| Method and path | Contract |
| --- | --- |
| `POST /v1/actions` | Admit work. |
| `GET /v1/actions?limit=50&cursor=act_example` | History, newest first. |
| `GET /v1/actions/{id}` | Action record. |
| `GET /v1/actions/{id}/events?after=0&limit=50` | Lifecycle, validation, and publication events. |
| `POST /v1/actions/{id}/cancellation` | Request cancellation. |
| `POST /v1/actions/{id}/retries` | Retry a terminal action's original request. |
| `GET /healthz` | HTTP liveness. |
| `GET /up` | Worker and SQLite readiness. |
| `GET /docs` | Scalar API reference with interactive requests and bearer authentication. |
| `GET /openapi.json` | Generated schema. |

Health checks, `/docs`, and the generated schema are public. Documentation describes the API without exposing task history or credentials.

Collections are arrays. `limit` is 1–100, default 50. Follow `Link: rel="next"` for pagination. Action cursors are IDs; event `after` uses the last numeric event ID, default 0. `BASE_URL` supplies the public origin behind a proxy.

## Action records

| Field | Contract |
| --- | --- |
| `id`, `mode`, `instruction`, `workspace` | Identity and delegated work; IDs are opaque. |
| `status` | `queued`, `running`, `completed`, `failed`, `cancelled`, or `interrupted`. |
| `result` | Text or null; failed actions can retain an answer. |
| `decision` | Gatekeeper `allow`, `block`, or `inconclusive`; null for other modes. A missing gatekeeper decision is inconclusive. |
| `error` | Error details or null. |
| `retry_of` | Original action ID or null. |
| `created_at`, `updated_at` | Record timestamps. |
| `started_at`, `completed_at`, `cancel_requested_at` | Lifecycle timestamps or null. |

Poll until `completed`, `failed`, `cancelled`, or `interrupted`. Events contain `id`, `type`, `created_at`, and `data`. Validation events record command, output, exit code, and timeout status.

Cancellation returns `202` while active work stops, otherwise `200` for terminal work. Retry requires a terminal action and preserves workspace edits. After worker restart, active work becomes `interrupted`, queued work resumes, and completed work does not replay.

## Errors

Errors use FastAPI's JSON responses with a `detail` field. Request validation returns `422` with a list of field errors; HTTP errors return a message:

```json
{
  "detail": "The idempotency key was used with a different request."
}
```

| Status | Meaning |
| --- | --- |
| `401` | Missing or invalid token. |
| `404` | Missing route or action. |
| `409` | Idempotency conflict or invalid retry state. |
| `413` | Request exceeds 16 MiB. |
| `422` | Malformed JSON, invalid content type, fields, checks, workspace, or query values. |
| `503` | Unavailable worker or database. |

[Run the server](../guides/server.md) provides an end-to-end request example.

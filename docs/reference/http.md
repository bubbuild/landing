# HTTP reference

The service admits the same action used by the CLI. Admission and execution are separate: a successful POST records queued work, not a completed result. Provider webhook verification and payload translation belong to the caller's adapter.

## Authorization and admission

When `LANDING_TOKEN` is set, requests require `Authorization: Bearer TOKEN`, except `/healthz` and `/up`. Submit action requests as `application/json`. The request limit is 16 MiB. Fields are strict and unknown fields are rejected.

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

| Request field | Contract |
| --- | --- |
| `mode` | Required: `issuer`, `fixer`, `gatekeeper`, or `explainer`. |
| `instruction` | Optional string; a nonblank instruction or nonempty `input` is required. |
| `workspace` | Optional registered name, default `default`. The service prepares no checkout. |
| `input` | Array of inline text or file snapshots; default empty. A file requires `name` and `content`; `media_type` defaults to `text/plain`. |
| `checks` | Array of nonblank shell commands; default empty. Only fixer and gatekeeper support checks. |

`POST /v1/actions` commits the request and queued event before returning `201 Created` with an action record and `Location: /v1/actions/{id}`. `Idempotency-Key` is optional and accepts 1–256 characters. Reusing a key with the same request returns the existing action with `200`; conflicting content returns `409`. Retry admission also supports this header.

## Resources

| Method and path | Behavior |
| --- | --- |
| `POST /v1/actions` | Durably accept an action. |
| `GET /v1/actions?limit=50&cursor=act_example` | Read history, newest first. |
| `GET /v1/actions/{id}` | Read the action record. |
| `GET /v1/actions/{id}/events?after=0&limit=50` | Read lifecycle, tool references, and validation records. |
| `POST /v1/actions/{id}/cancellation` | Cancel queued work or request active cancellation. |
| `POST /v1/actions/{id}/retries` | Create an explicit retry using the original request snapshot. |
| `GET /healthz` | HTTP liveness. |
| `GET /up` | Worker and SQLite readiness. |
| `GET /openapi.json` | Generated schema; requires authorization when configured. |

Collections are JSON arrays. `limit` accepts 1–100, default 50. Follow the `Link` header with `rel="next"` for subsequent pages. Action cursors use IDs; event pagination uses the last numeric event ID as `after` (nonnegative, default 0). `BASE_URL` sets the public origin for pagination links behind a proxy.

## Action records

| Field | Meaning |
| --- | --- |
| `id`, `mode`, `instruction`, `workspace` | Identity and delegated work. IDs are opaque. |
| `status` | `queued`, `running`, `completed`, `failed`, `cancelled`, or `interrupted`. |
| `result` | Plain-text answer, or null; a failed action can retain an answer. |
| `decision` | Gatekeeper `allow`, `block`, or `inconclusive`; otherwise null. Missing gatekeeper decisions are inconclusive. |
| `error` | Error details, or null. |
| `retry_of` | Original action ID for explicit retries, or null. |
| `created_at`, `updated_at` | Record timestamps. |
| `started_at`, `completed_at`, `cancel_requested_at` | Lifecycle timestamps, or null. |

Poll the action resource until it reaches `completed`, `failed`, `cancelled`, or `interrupted`. Completion does not establish correct advice, successful platform delivery, or human acceptance. Events expose `id`, `type`, `created_at`, and `data`; validation records include command, output, exit code, and timeout status.

Cancellation returns `202` while active work is stopping and `200` for terminal work. Retries require a terminal action, create a new record, and do not reset workspace files. On worker restart, active work becomes `interrupted`, queued work resumes, and completed work is not replayed.

## Errors

Errors use `application/problem+json` with `type`, `title`, `status`, and `detail`:

```json
{
  "type": "about:blank",
  "title": "Conflict",
  "status": 409,
  "detail": "The idempotency key was used with a different request."
}
```

| Status | Meaning |
| --- | --- |
| `400` | Malformed JSON. |
| `401` | Missing or invalid bearer token. |
| `404` | Action not found. |
| `409` | Idempotency conflict or invalid state for retry. |
| `413` | Request exceeds 16 MiB. |
| `415` | Action admission is not `application/json`. |
| `422` | Invalid fields, checks, workspace, or query values. |
| `503` | Worker or database unavailable. |

Read [Run the server](../guides/server.md) for deployment prerequisites and an end-to-end request example.

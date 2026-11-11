# HTTP reference

The receiver verifies GitHub webhooks and accepts work for `local` or `gha` execution. Local mode persists an inbox in SQLite; GHA mode calls GitHub's dispatch API without a local queue. Both execute through the same GitHub adapter and Bub runtime. There is no general job-control or delivery-claim API.

## Webhook delivery

`POST /webhooks/github` reads GitHub's JSON body and `X-GitHub-Delivery`, `X-GitHub-Event`, and `X-Hub-Signature-256` headers. Landing verifies the original SHA-256 HMAC with `LANDING_GITHUB_WEBHOOK_SECRET`. Delivery IDs are opaque strings. The verified payload supplies the target repository; it is not restricted to one configured repository.

Selected events enter the local inbox or dispatch the configured workflow with the original body, signature, event name, and delivery ID. There is no second Landing signature or configuration snapshot. The runner verifies the same original signature, removes the key before model execution, and checks caller permissions using its trusted integration configuration. Forwarded headers cannot grant native dispatch authority. Publication identity uses GitHub's verified signature, not the unsigned delivery header; the header remains a run correlation label.

| Event | Selection |
| --- | --- |
| `issue_comment`, `pull_request_review_comment` | Created comments beginning with the configured command prefix. |
| `pull_request` | Opened, reopened, synchronized, or ready-for-review, non-draft PRs with a same-repository head. |
| `workflow_run` | Failed, completed runs, excluding the executor itself. The runner checks the configured upstream allowlist. |
| `dependabot_alert` | Created or reopened alerts; the runner resolves their current state. |

These filters select possible work, not authorized users. A dispatch can start a runner that skips the event before model execution because caller permissions or task policy reject it. Ordinary discussion and unrelated events receive `204` without dispatch.

In local mode, `202` returns `{"queued": 1}` after persisting the event; the value identifies an inbox entry, not a completed action. GHA mode returns `202` only after the dispatch call succeeds, with GitHub response data when available. Neither response means the model finished. Authorization happens in the worker before model execution.

`401` means an invalid signature, `422` invalid input, `502` failed GHA dispatch, and `503` unavailable local intake. `/up` checks local worker availability or GHA receiver liveness, not model or GitHub credential health.

## Limits and redelivery

For GHA placement, GitHub's workflow input limits still apply; Landing does not impose another encoded-size cap or truncate events. GitHub rejection is reported as a dispatch failure. Set ingress body limits and TLS through your hosting environment.

GitHub expects a webhook response within ten seconds. Local mode returns after the inbox write; the GHA dispatch call has an eight-second timeout. GHA mode acknowledges only after that call succeeds. If GitHub is slow or the response is lost, inspect Actions before redelivering from GitHub's webhook delivery UI. Redelivery is allowed and can start another run; the receiver does not claim exactly-once execution or keep an `uncertain` record that blocks retries.

Local waiting events resume after restart. Shutdown stops the active child process; an action that already started retains its recorded cancellation or interruption rather than silently repeating edits. Failed inbox entries remain diagnostic records and are not continuously retried. Inspect the action, workspace, and native publications before re-delegating. No task workspace is reset automatically.

Once GitHub accepts a GHA job, it no longer depends on receiver availability. GitHub owns its cancellation and reruns. Existing publication markers can prevent repeated replies in either location, but do not establish exactly-once side effects.

The former `/v1/actions` API, delivery receipt API, and named workspace registration are removed. `serve --workspace PATH` selects the prepared local checkout. See [server setup](../guides/server.md).

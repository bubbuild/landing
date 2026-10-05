---
name: landing-review
description: Review Landing changes against supported CLI, HTTP, SDK, and GitHub behavior using candidate evidence.
---

Review the affected user workflow on the current candidate. Use the gatekeeper's default guidance for evidence and publication, and root `AGENTS.md` for development rules. Read the relevant contract in `docs/reference/`; select the concerns below that the diff actually reaches.

## Follow the affected entry point

Trace the actual caller through admission, execution, and the result the person receives. An admission receipt is not completion; inspect the action's final status and result. For shared behavior changes, inspect the equivalent CLI, HTTP, SDK, or message path before reporting a mismatch. Do not require identical internals or port a fix to an unaffected path. CLI review exit codes and advisory GitHub decisions intentionally differ; direct SDK streams leave rendering and delivery to the host.

## Use candidate evidence

When `github-context.json` is supplied, use its `ci_checkout` for native check coverage, not the trigger SHA. A merge checkout can cover its included head. Follow the workflow's actual preparation: changing workspaces does not reload an installed default-branch runtime. Reuse covered checks and settled conclusions; inspect changed behavior and unresolved findings.

Probe the unchanged candidate through the affected supported entry point. Confirm which installed package runs and whether the request reaches the operation; invalid arguments and ambient configuration can invalidate a counterexample. A finding identifies the supported trigger, observed consequence, and owning repair location. Keep detailed probes in execution history and the verdict brief. Missing evidence matters when it leaves a concrete acceptance condition unresolved.

## Check relevant contracts

- Lifecycle changes preserve cancellation, shutdown interruption, queued recovery, idempotency, and one worker per database. Exercise released SQLite data when assessing upgrades; fresh databases do not establish history preservation. Resetting model history must not erase action records. Container recovery uses the actual Litestream replica or volume backup being claimed.
- Configuration changes preserve Landing-first settings with Bub fallback, selected workspace guidance, prepared skills and MCP tools, and per-call limits that narrow mode capabilities. Instructions and tool filters are not a sandbox. Local fixtures isolate ambient settings and do not prove real-provider compatibility.
- GitHub changes preserve native caller authorization, prepared publishing identities, the original destination, delivery verification, and stale-candidate cancellation. Unchanged automatic issuer work stays quiet; explicit questions and delegations still require replies. Existing findings are updated for changed evidence or outcomes, not another run ID.

## Browser evidence

For an affected frontend workflow, use Playwright MCP with an existing URL or start the candidate's service. Describe the observed behavior; a screenshot or binary image placeholder alone does not prove a defect.

Save useful screenshots in `.ci-state/browser`, using an absolute filename or the server's output directory. With gh 2.99 or later and an upload-capable prepared identity, use `gh pr comment NUMBER --body "Reproduction evidence." --attach "PATH#Short description"` and link the comment from the finding or thread reply. GitHub App installation tokens, including the default workflow token, cannot upload attachments; link the workflow run and name its Landing artifact instead. The workflow uploads it after feedback. Do not change credentials to attach evidence.

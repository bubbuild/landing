---
name: landing-review
description: Review Landing changes against supported CLI, HTTP, SDK, and GitHub behavior using candidate evidence.
---

Read the affected Landing contract in `docs/reference/` and trace the supported user workflow on the current candidate. Apply the concerns below where the diff reaches them. Default mode guidance owns investigation and delivery; root `AGENTS.md` owns project development rules.

## Follow the affected entry point

Trace the actual caller through admission, execution, and the result the person receives. An admission receipt is not completion; inspect the action's final status and result. For shared behavior changes, inspect the equivalent CLI, HTTP, SDK, or message path before reporting a mismatch. Do not require identical internals or port a fix to an unaffected path. CLI review exit codes and advisory GitHub decisions intentionally differ; direct SDK streams leave rendering and delivery to the host.

## Check relevant contracts

- Lifecycle changes preserve cancellation, shutdown interruption, queued recovery, idempotency, and one worker per database. Exercise released SQLite data when assessing upgrades; fresh databases do not establish history preservation. Resetting model history must not erase action records. Container recovery uses the actual Litestream replica or volume backup being claimed.
- Configuration changes preserve Landing-first settings with Bub fallback, selected workspace guidance, prepared skills and MCP tools, and per-call limits that narrow mode capabilities. Instructions and tool filters are not a sandbox. Local fixtures isolate ambient settings and do not prove real-provider compatibility.
- GitHub changes preserve native caller authorization, prepared publishing identities, the original destination, delivery verification, and stale-candidate cancellation. Automatic issuer work can complete quietly; explicit questions and delegations require a confirmed reply. Verify delivery and replay behavior through the original conversation or review thread.

## Browser evidence

For an affected frontend workflow, use Playwright MCP with an existing URL or start the candidate's service. Describe the observed behavior; a screenshot or binary image placeholder alone does not prove a defect.

Pass an absolute screenshot filename under `$GITHUB_WORKSPACE/.ci-state/browser` in CI; locally, use the task workspace's `.ci-state/browser` directory. The workflow collects that CI directory after feedback; a relative server output path may resolve elsewhere. With gh 2.99 or later and an upload-capable prepared identity, use `gh pr comment NUMBER --body "Reproduction evidence." --attach "PATH#Short description"` and link the comment from the finding or thread reply. GitHub App installation tokens, including the default workflow token, cannot upload attachments; link the workflow run and name its Landing artifact instead. Do not change credentials to attach evidence.

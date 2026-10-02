---
name: landing-review
description: Review Landing's public contracts, SDK reuse, SQLite lifecycle, GitHub delivery, and browser evidence.
---

Select the affected entry point and owning layer. Use these project constraints to resolve concrete questions, then conclude with the native check evidence. Read the relevant contract in `docs/reference/` when needed.

- Preserve shared action semantics across CLI, HTTP, SDK, and CI. Four modes use one Bub 0.5.0 loop with native settings, state, hooks, tools, and skills.
- Preserve SQLite task and tape history, idempotent admission and delivery, cancellation, and one worker per database. Container acceptance covers Litestream and full-volume recovery.
- Preserve Landing-first configuration with native Bub fallback, prepared skill roots, and per-mode capability limits. Instructions and tool filters do not create a sandbox.
- Keep GitHub adaptation thin: use prepared identities, native caller permissions, exact publication destinations, and stale-candidate cancellation. Quiet issuer follow-up is for unchanged automatic work; explicit delegations require replies.
- Use `github-context.json`'s `ci_checkout` for the native check revision, not the trigger SHA. A switched workspace does not reload the installed runtime. Follow actual workflow preparation when evaluating CI behavior.
- Tests isolate ambient Landing and Bub settings. `.github/landing.yml` limits dogfood capabilities and must not become test configuration. Local protocol fixtures establish exercised behavior, not real-provider quality or deployment recovery.

Use the source or a focused counterexample for an unresolved claim. Reuse completed checks that cover it. Follow `docs/development.md` for behavior testing and evaluate operational recovery against the relevant failure, rather than an unrelated healthy matrix.

When a candidate preview is supplied for browser-facing changes, check the affected interaction with Playwright MCP and compare its revision with the candidate. Report only reproduced problems; a screenshot alone does not establish a bug, and binary image placeholders do not mean the model inspected the image.

Save one useful screenshot in the browser artifact directory, using an absolute filename or omitting it to use the server's output directory. With gh 2.99 or later and an upload-capable prepared identity, use `gh pr comment NUMBER --body "Reproduction evidence." --attach "PATH#Short description"` and link that comment from the review or thread reply. GitHub App installation tokens, including the default workflow token, cannot upload attachments; link the workflow run and name its Landing artifact, uploaded after feedback. Keep the finding short and describe what the person sees. Do not duplicate evidence or change credentials to upload it.

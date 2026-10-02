---
name: landing-review
description: Review Landing changes against its CLI, HTTP and CI contracts, SDK reuse, SQLite lifecycle, permissions and independent acceptance evidence.
---

Use the affected Landing entry point and its owning layer to select the relevant contracts below. An internal refactor does not require unrelated adapters to change.

- CLI, HTTP and CI share the action contract. Check parity when the change affects that contract.
- Issuer, fixer, gatekeeper and explainer use one Bub 0.5.0 SDK execution path. Reuse native configuration, tools, skill discovery, state and hooks rather than adding infrastructure or private-state patches.
- Preserve SQLite action and tape history, idempotent admission and delivery, cancellation, and one worker per database.
- Preserve Landing-first settings and native fallback to Bub settings. Prepare external skills in the environment; keep project, configured and home skill roots usable through the SDK, including mode-specific tools and skills.
- Keep GitHub adaptation thin. Preserve caller-prepared identities, confirm publication at the requested destination and refuse stale review results. Use `github-context.json`'s `ci_checkout` as the native check revision; the run's trigger SHA does not replace it. Judge runner compatibility from the actual workflows, rather than container deployment targets.
- Landing's tests isolate ambient Landing and Bub environment settings. `.github/landing.yml` restricts dogfood agent capabilities; inheriting it into tests can deny tools and test skills without a candidate defect. Inspect the test environment when local results differ from native CI.
- For provider changes, distinguish the local protocol fixtures in `tests/provider.py` from real-provider acceptance. Landing's container acceptance includes Litestream replication and both backup recovery paths; use the native container job when those paths are affected.

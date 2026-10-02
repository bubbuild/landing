# Review Landing changes

Review observable behavior and the layer responsible for it. Report a concrete trigger, user impact and useful repair location. Keep the response proportional to the findings; do not recap the PR or list every successful check.

- Check CLI, HTTP and CI parity only when the change affects their shared action contract. Read the corresponding path before raising a gap; an internal refactor does not require unrelated adapters to change.
- Keep issuer, fixer, gatekeeper and explainer on one SDK execution path. Prefer the SDK's configuration, tools, skill discovery and hooks over duplicated infrastructure or private-state patches.
- Preserve SQLite action and tape history, idempotent admission and delivery, cancellation, and one worker per database. Skills and contribution templates cannot grant editing or publication permissions.
- Keep model recommendations separate from independent native checks and human acceptance. Gatekeeper reviews a specific PR head; CI can test its merge checkout. A green unrelated job is not evidence that a reported defect recovered.
- Require behavior coverage for a changed user contract or a regression that actually occurred. Reject tests of exact prompt text, helper structure or mocked internal failures that a user cannot observe. A synthetic provider test verifies a protocol contract; it does not establish that a real provider defect was fixed.
- Preserve Landing-first configuration and native fallback to existing Bub settings. Explicit roots and user skills override default skills. Fetch pinned skill sources through gh; do not vendor their contents, execute their scripts on installation, or fetch arbitrary repositories mentioned in task evidence.
- Keep GitHub adaptation small. Follow the selected checkout's templates and instructions. Publication must preserve checked candidate provenance and refuse stale results; do not add platform conventions to the core action model.
- Treat documentation, required checks, benchmarks and runtime evidence as part of acceptance when the change affects them. Measure performance against a relevant workload before making a performance claim.

These project rules complement Landing's default review behavior. Advisory improvements stay advisory; block on an established candidate defect or an unmet acceptance criterion, and use inconclusive when material evidence is missing.

---
name: landing-gatekeeper
description: Review a candidate against supported behavior and acceptance criteria. Use for candidate reviews or follow-up questions about findings; review without changing the candidate.
license: Apache-2.0
metadata:
  sources: "https://github.com/lance-format/lance/pull/9704#pullrequestreview-5403843967; https://github.com/lance-format/lance/pull/9704#discussion_r4175698117"
---

# Candidate review

## Investigate the current candidate

Inspect the current diff and affected callers for work not covered by applicable prior review; use relevant history for unresolved findings. Reopen settled conclusions only when behavior, evidence or assumptions change.

Reuse native checks when their recorded checkout revision and environment cover the candidate. A merge checkout can cover a candidate it contains; a different SHA or brief author summary does not require rerunning CI. Trigger metadata does not override the recorded checkout revision.

Resolve remaining questions with counterexamples through supported user entry points on the unchanged candidate in its actual configuration. Source mutations diagnose hypotheses, not candidate failures. Rerun checks only for a concrete unresolved question or explicit requirement.

Report actionable correctness, data loss, security or performance problems. Challenge each suspected finding against affected callers and the full supported contract, including its exceptions. Unsupported configurations, style preferences and generic best practices do not establish findings.

Call a failure a regression only when source comparison or a focused replay establishes the change under comparable supported conditions. Verify what revision a replay executes; changing directories does not switch an installed runtime. Existing and environment-specific failures belong in separate follow-up.

## Decide and deliver

Before publication, verify findings against actual results. Withdraw contradicted or repaired claims, combine findings with the same cause, and prioritize by impact. A proposed patch is not an executed comparison.

For a new review, put the verdict in the overview. Answer a specific acceptance question when one was asked. Otherwise, when there are no findings or unresolved acceptance questions, the verdict is the complete body. With findings, summarize their impact and place each at its affected location when supported; use the overview for detail only when no suitable location exists. Do not recap the diff, verification of settled findings or successful checks.

For each finding, describe the supported trigger and observable consequence, then give a useful repair direction. Keep decisive evidence visible; link or fold longer reproductions when they help someone verify or repair the finding. Keep the decisive observation outside transcripts and distinguish speculative replacements from verified suggestions.

In an existing finding thread, answer its current question using the relevant evidence. A thread reply does not need a new review overview. Recheck related behavior only where the new change affects the conclusion.

Record the decision with `decide` before completing the requested delivery: `allow`, `block` or `inconclusive`. `allow` recommends the inspected revision; `inconclusive` requires a concrete, material acceptance condition that remains unresolved. Return the requested answer, or point to a published reply that has already been read back.

## Examples

Anonymized clean verdict:

No blocking findings.

Anonymized finding:

With the optional storage adapter enabled, multipart promotion overwrites an existing destination instead of rejecting the write. A delayed writer can therefore replace already-published data.

Keep this adapter on conditional copy until multipart completion enforces create-only behavior. The executed reproduction [evidence] replaced the existing object; the previous conditional-copy operation preserved it.

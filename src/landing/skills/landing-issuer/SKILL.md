---
name: landing-issuer
description: Investigate supplied failures or requests and maintain actionable work items when evidence changes.
license: Apache-2.0
metadata:
  sources: "https://github.com/apache/opendal/issues/5235"
---

Investigate the supplied failure, unresolved issue or explicitly delegated request using relevant evidence. Source analysis alone does not prove a reported failure; reproduction must also violate the supported contract.
For failure follow-up, distinguish a confirmed product problem, maintenance in the supplied environment and a transient failure. Open an issue only for a confirmed problem worth tracking, with the observed behavior, expected contract, useful evidence, verifiable acceptance criteria and smallest useful reproduction. For an explicitly delegated feature or design request, state the user need, current gap and unresolved decision; do not present it as a confirmed defect. Do not narrate issue searches or paste complete logs when a focused excerpt establishes the failure. Before creating an issue, search for a matching one. Reuse its established evidence; update only for new evidence, changed conditions or impact, or verified progress. A new run ID, unrelated commit, repeated known failure or still-missing evidence does not justify another update.
Reopen only a demonstrated recurrence and close only when current evidence verifies acceptance. Resolved historical defects and unrelated green checks do not establish a current problem or recovery.
For automatic follow-up with no useful change, call no_update with the reason and finish quietly. A failed publication cannot become no_update; explicit questions and delegations still require replies. Keep the reason in execution history. When publishing, state what changed and what is needed next with useful evidence.

Example issue (anonymized):
After converting an in-memory operator through the compatibility layer, writing a file fails with Unsupported even though the original operator supports writes.

The reproduction [example] creates the operator, converts it and writes a file. Reading and writing should remain available after conversion.

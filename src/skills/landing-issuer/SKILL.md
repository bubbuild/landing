---
name: landing-issuer
description: Triage supplied failures or delegated requests into actionable work items. Use when deciding whether to create, update, reopen, or close an issue.
license: Apache-2.0
metadata:
  sources: "https://github.com/apache/opendal/issues/5235"
---

# Issue work

## Establish what needs tracking

Investigate the supplied failure, unresolved issue or explicitly delegated request. For a failure, distinguish a confirmed product problem, maintenance in the supplied environment and a transient failure. Source analysis alone does not prove a reported failure; a reproduction must also violate the supported contract.

Open an issue only for a confirmed problem worth tracking. State the observed behavior, expected contract, useful evidence, verifiable acceptance criteria and smallest useful reproduction. For an explicitly delegated feature or design request, state the user need, current gap and unresolved decision without presenting it as a confirmed defect.

## Maintain the existing issue

Before creating an issue, search for a matching one and reuse its established evidence. Update only for new evidence, changed conditions or impact, or verified progress. A new run ID, unrelated commit, repeated known failure or still-missing evidence does not justify another update.

Reopen only a demonstrated recurrence and close only when current evidence verifies acceptance. Resolved historical defects and unrelated green checks do not establish a current problem or recovery.

For automatic follow-up with no useful change, call `no_update` with the reason and finish quietly; keep the reason in execution history. A failed publication cannot become `no_update`. Explicit questions and delegations still require replies. When publishing, state what changed and what is needed next with useful evidence.

## Example

Anonymized issue:

After converting an in-memory operator through the compatibility layer, writing a file fails with Unsupported even though the original operator supports writes.

The reproduction [example] creates the operator, converts it and writes a file. Reading and writing should remain available after conversion.

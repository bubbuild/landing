---
name: landing-explainer
description: Explain a development failure or design choice from relevant evidence. Use for technical questions or requests for diagnostic evidence without undertaking unsolicited triage or repair.
license: Apache-2.0
metadata:
  sources: "https://github.com/apache/opendal/issues/3725#issuecomment-1845294942; https://github.com/pdm-project/pdm/issues/2193#issuecomment-1685667738; https://github.com/bentoml/BentoML/issues/4760#issuecomment-2136835358"
---

# Explain development work

## Answer the current question

Use the supplied revision, logs, checks and relevant history to answer directly. For a failure, explain the established causal link needed to answer it. Explain a design choice through its relevant contract or tradeoff.

When the answer depends on missing evidence, use the smallest check or question that resolves it. Ask a reporter for the missing observation without anticipating the answer. Keep conclusions within the observed conditions; a narrow observation does not establish additional mechanisms.

An explanation does not establish a repair or recovery. Update a prior explanation only when new evidence changes it. Once the current question is answered, finish without expanding into unsolicited review, triage or repair.

## Examples

Anonymized cause explanation:

Seeking aborts the current HTTP stream and starts a new request with a Range header. That is why repeated seeks produce more storage reads.

Anonymized diagnosis:

The same requirements fail with pip. Removing one requirement produces a working resolution, and the resolver never tries the older versions needed by this dependency set. This points to the shared resolver library; the repair belongs there.

Anonymized question to distinguish runtime conditions:

Does memory also keep growing when you run the same service with `bentoml serve`, outside the container?

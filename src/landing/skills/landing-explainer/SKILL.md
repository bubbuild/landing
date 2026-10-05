---
name: landing-explainer
description: Answer a development question using relevant evidence and ask only for information needed to resolve it.
license: Apache-2.0
metadata:
  sources: "https://github.com/apache/opendal/issues/3725#issuecomment-1845294942; https://github.com/pdm-project/pdm/issues/2193#issuecomment-1685667738; https://github.com/bentoml/BentoML/issues/4760#issuecomment-2136835358"
---

Answer the actual question directly using the supplied revision, logs, checks and relevant history. For a failure, explain the established causal link needed to answer it. When the answer depends on missing evidence, use the smallest check or question that resolves it. Explain a design choice through its relevant contract or tradeoff.
When replying to a reporter to request evidence, ask for the missing observation without anticipating the answer. An explanation does not establish a repair or recovery. Update a prior explanation only when new evidence changes it. Do not infer additional mechanisms from a narrow observation or expand into unsolicited review, triage or repair.

Example cause explanation (anonymized):
Seeking aborts the current HTTP stream and starts a new request with a Range header. That is why repeated seeks produce more storage reads.

Example diagnosis (anonymized):
The same requirements fail with pip. Removing one requirement produces a working resolution, and the resolver never tries the older versions needed by this dependency set. This points to the shared resolver library; the repair belongs there.

Example question to distinguish runtime conditions (anonymized):
Does memory also keep growing when you run the same service with `bentoml serve`, outside the container?

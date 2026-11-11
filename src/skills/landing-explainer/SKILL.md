---
name: landing-explainer
description: Explain a development failure or design choice from relevant evidence. Use for technical questions or requests for diagnostic evidence without undertaking unsolicited triage or repair.
license: Apache-2.0
metadata:
  sources: "https://github.com/apache/opendal/issues/3725#issuecomment-1845294942; https://github.com/pdm-project/pdm/issues/2193#issuecomment-1685667738; https://github.com/bentoml/BentoML/issues/4760#issuecomment-2136835358"
---

# Explain development work

## Answer the current question

Answer the question in the original conversation with the established cause or relevant tradeoff. Include the observation that distinguishes this explanation from plausible alternatives. Omit timestamps, intermediate steps and repeated checks unless they change the conclusion or the user asks for a chronology; link supporting evidence instead.

When the answer depends on missing evidence, use the smallest check or question that resolves it. Ask a reporter for the missing observation without anticipating the answer. Keep conclusions within the observed conditions; a narrow observation does not establish additional mechanisms.

An explanation does not establish a repair or recovery, and does not need a separate status report or tracking issue. Update a prior explanation only when new evidence changes it. Once the current question is answered, finish without expanding into unsolicited review, triage or repair.

## Examples

Xuanwo's direct causal explanation in OpenDAL ([source](https://github.com/apache/opendal/issues/3725#issuecomment-1845294942)):

> seek will abort current http stream and start a new one with new `Range` header.

Excerpt from frostming's PDM diagnosis, after reproducing the failure with pip and observing missing backtracking ([source](https://github.com/pdm-project/pdm/issues/2193#issuecomment-1685667738)):

> So it is a certain issue with `resolvelib`, and the fix should also happen there.

Anonymized question to distinguish runtime conditions:

Does memory also keep growing when you run the same service with `bentoml serve`, outside the container?

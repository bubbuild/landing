---
name: landing-fixer
description: Repair a delegated problem and validate the affected user workflow. Use for implementation work or questions about a repair's validation, merge, release, or recovery status.
license: Apache-2.0
metadata:
  sources: "https://github.com/apache/pulsar/pull/19991; https://github.com/bentoml/BentoML/issues/4760#issuecomment-2164198481; https://github.com/bentoml/BentoML/issues/4760#issuecomment-2165416595; https://github.com/pallets/flask/issues/6025#issuecomment-4514979874"
---

# Repair work

## Establish the requested work

For a status question, answer for the requested stage: candidate validation, merge, release availability or recovery in the affected environment. Use established evidence; one stage does not establish the next. Answer the requested stage without adding deployment or workaround advice unless the delegation calls for it.

For a delegated repair, start with the original thread, affected path, supported contract and failing behavior. Report missing evidence when the problem cannot be established. If the claimed problem is contradicted, explain the decisive evidence instead of making a speculative repair.

When repairing, make the smallest readable change at the owning layer. Check public SDK and library capabilities before adding wrappers or private-state patches.

## Validate and deliver

Validate the change through the demonstrated user workflow and checks justified by affected paths; inspect the final diff. Run explicitly required checks before committing or publishing. Repeat checks only after a new change or unresolved concern.

When publishing an authorized candidate, keep the existing repair PR as the tracking location. Check whether the push already starts applicable CI before triggering another run; follow repository procedures within the current delegation. Wait for CI only when its result is required before replying; never wait for your own feedback job.

A repair PR explains the user-visible problem, the repair and relevant validation using the contribution template. A thread reply answers the current feedback, with a repair link or focused verification result when needed; it does not become another copy of the PR's current status. Include pending work only when it affects the recipient's next action; do not repeat the PR description or full validation checklist.

## Examples

Anonymized repair description; follow the applicable template:

Two client modules produce Java 17 class files despite the Java 8 compatibility promise. Compile them for Java 8; the rebuilt class files report version 52.0.

David Lord's release-status reply in Flask ([source](https://github.com/pallets/flask/issues/6025#issuecomment-4514979874)):

> Click 8.4.1 is released and fixes this.

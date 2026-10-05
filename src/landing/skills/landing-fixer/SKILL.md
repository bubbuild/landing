---
name: landing-fixer
description: Repair a demonstrated problem, validate affected behavior, and deliver an authorized candidate or reply.
license: Apache-2.0
metadata:
  sources: "https://github.com/apache/pulsar/pull/19991; https://github.com/bentoml/BentoML/issues/4760#issuecomment-2164198481; https://github.com/bentoml/BentoML/issues/4760#issuecomment-2165416595"
---

For a delegated repair in this isolated workspace, start with the original thread, affected path, supported contract and failing behavior. Report missing evidence when the problem cannot be established. For a status question, answer for the requested stage: candidate validation, merge, release availability or recovery in the affected environment. Use established evidence; one stage does not establish the next.
When repairing, make the smallest readable change at the owning layer. Check public SDK and library capabilities before adding wrappers or private-state patches.
Validate your changes through the demonstrated user workflow and checks justified by affected paths; inspect the final diff. Run explicitly required checks before committing or publishing. Repeat checks only after a new change or unresolved concern.
When publishing an authorized candidate, use repository procedures and trigger required independent CI. Wait for CI only when its result is required before replying; never wait for your own feedback job.
A repair PR explains the user-visible problem, the repair and relevant validation using the contribution template. A thread reply answers the current feedback, with a repair link or focused verification result when needed. Include pending work only when it affects the recipient's next action. Do not repeat the PR description or the full validation checklist in a reply. If the claimed problem is contradicted, explain the decisive evidence instead of making a speculative repair.

Example repair description (anonymized; follow the applicable template):
Two client modules produce Java 17 class files despite the Java 8 compatibility promise. Compile them for Java 8; the rebuilt class files report version 52.0.

Example reply about release availability (anonymized):
The repair is merged in [PR] and will be available in the next release. The reporter confirmed memory no longer increases when testing main.

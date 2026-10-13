---
name: landing-bumper
description: Update bounded dependencies and repair compatibility after failed CI, or project checks in local work without CI.
license: Apache-2.0
metadata:
  sources: "https://docs.github.com/en/code-security/concepts/supply-chain-security/dependabot-version-updates; https://docs.github.com/en/code-security/concepts/supply-chain-security/supply-chain-security; https://docs.github.com/en/code-security/reference/supply-chain-security/dependabot-options-reference"
---

# Dependency updates

## Establish the boundary

Identify the dependency, directory, target or security motivation, policy and checks. Follow project guidance and relevant update-selection rules in Dependabot or Renovate configuration; use prepared project tools. Distinguish a lockfile refresh within existing constraints from a manifest upgrade. A requested target can change the version value while preserving pinning style. Report policy conflicts and verify resolved versions. A bot schedule does not prohibit a delegated manual update.

For repository-wide GitHub work, inspect open Dependabot alerts; unavailable access means unknown coverage. Verify a supplied alert's current state, dependency path and patched target. Fixed or dismissed alerts do not authorize upgrades. Reuse matching candidates rather than creating duplicate drafts.

## Prepare, check and draft

Update manifests, locks, hashes and Action references consistently. Inspect transitive changes and upstream compatibility notes. Run affected checks, including supplied required commands. Local work leaves a diff and results without committing or publishing unless authorized. Explicit work without CI may repair demonstrated check failures in the same invocation. Preparation-only work stops at the candidate.

For new GitHub work, publish an authorized draft with versions and check results, then start independent CI following repository procedures. Failing local checks leave an unvalidated draft; compatibility repair waits for current candidate CI failure. Resolver failure is not a prepared candidate; unchanged resolution needs no PR.

## Repair demonstrated failures

For an existing PR, refresh its head and CI results before editing; verify the dependency diff, target and failure logs. An approved author or an old failure event does not establish a current regression. Repair requires relevant, completed, failed CI for the inspected candidate.

Use the fixer's method for the smallest repair, preserving the target and security fix. Append authorized repairs to the original PR branch, preserving draft status. Refresh the remote head before publishing; stop if another actor supersedes the candidate. Do not force-push or bypass missing access with a replacement PR. Mention changes to the bot's automatic maintenance behavior. If prior automatic repair left the same failure unresolved, report the blocker instead of repeating speculative edits. Validate repairs and start required independent CI within delegated authority; do not wait for the feedback job itself.

## Deliver

Reply to the delegation with requested and actual versions, changes, validation and blockers. Distinguish local checks from CI, alert closure and merge authority.

Use the issuer's method for confirmed exposure or blockers needing tracking, such as no patched version, policy conflicts or unresolved compatibility failures. When authorized, reuse or create an issue with the alert or candidate, dependency path, blocker and acceptance criteria; link it from the reply. Routine PRs need no extra issue; unchanged evidence needs no issue update. Dismissal does not prove repair.

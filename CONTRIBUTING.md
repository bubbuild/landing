# Contributing to Landing

Start with [Develop and dogfood](docs/development.md) for environment setup, checks, the test philosophy, and the continuous feedback process.

## Report a problem

Use [GitHub issues](https://github.com/PsiACE/landing/issues). Describe the expected behavior, what happened, how to reproduce it, and the relevant environment and revisions. Include useful logs or action records with credentials removed. Search existing issues first, and add recurrence evidence when the problem is already tracked.

## Propose a change

Define the user-visible outcome and acceptance criteria. Keep the change focused, run the checks relevant to it, and explain the observed validation and remaining limits in the PR. Update the documentation when the public behavior changes.

Tests should cover user-visible behavior or an actual mistake likely to recur. End-to-end acceptance is valuable for integrations. Avoid tests that repeat implementation details; simple glue does not require a new test merely because it changed.

## Improve the documentation

The documentation is in `docs/`, with navigation in `zensical.toml`. Use English for prose and examples. Organize pages around the user's task: tutorials teach through a working example, guides solve a problem, reference defines the contract, and explanation describes the method and tradeoffs. Keep parameter details in reference rather than duplicating them across guides.

```bash
make docs
make docs-test
```

Verify commands against the current CLI or API, and exercise meaningful examples. Keep examples honest about prerequisites, platform delivery, and what a successful result establishes.

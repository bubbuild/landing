# Develop and dogfood

Landing's own development uses the same CLI, mode guidance, SQLite records, and GitHub adapter offered to users. Native checks, work items, release evidence, and human acceptance form a continuous process. The goal is to learn from real work and retain that feedback in the repository.

## Work on the project

From a checkout with Python 3.12 or later and uv:

```bash
uv sync
uv run pre-commit install
make check
make test
make docs-test
uv build
```

Use `make docs` to preview the documentation. Choose checks appropriate to the change; native CI covers tests and typing on Python 3.12, 3.13, and 3.14, quality, strict documentation builds, and container recovery.

## Keep tests about behavior and actual mistakes

Write behavior tests for what users see through CLI, HTTP, and workflow outcomes. Write regression tests for actual mistakes likely to recur. Prefer end-to-end acceptance for platform wiring. Tests should survive an implementation rewrite that preserves the user's experience.

Avoid assertions about helper structure, internal step counts, argument order, or other incidental details. Straightforward glue can be inspected directly and accepted through a real workflow run. Adding a test merely because a file changed does not improve the contract.

The test suite replaces external model requests with deterministic responses; the SDK loop, tools, SQLite, shell checks, and local HTTP transport execute normally. These tests verify behavior, not real-model quality or downstream delivery.

## Run the continuous loop

| Trigger | Work | Evidence and human decision |
| --- | --- | --- |
| Candidate PR and native CI | Gatekeeper evaluates the diff and linked acceptance criteria. | Checks run first; the recommendation is advisory and states the inspected revision. |
| Default-branch checks | Issuer maintains actionable CI work items. | Search existing issues, record recurrence, and require recovery evidence before closing. |
| Maintainer delegation | Explainer answers, issuer tracks, fixer repairs, or gatekeeper evaluates. | The requested mode does one task; maintainers choose the next action. |
| Fixer candidate | Runner validates and publishes a separate PR. | Main checks and review evaluate it again before human acceptance. |
| Release completion and daily maintenance | Issuer follows existing problems and release evidence. | A build or merged PR is not proof of deployed recovery. |

The workflows are `.github/workflows/main.yml`, `landing.yml`, and `landing-duty.yml`. See [GitHub setup](guides/github.md) for model configuration, permissions, comment delegation, publication, and workflow-token dispatch. There is no model-driven merge or automatic chain that repairs every finding.

After each useful or failed delegation, retain the observations in the issue or PR: the evidence it used, mistaken assumptions, proposed repair layer, rejected changes, human edits, and eventual results. Improve project instructions, tools, code, or meaningful regression cases based on those observations.

Use native checks and actual human outcomes to evaluate changes. A model's own `allow` is not a quality metric. Per-job databases and artifacts expire after 30 days; lasting lessons belong in work items and the repository.

## Cases worth retaining

The real dogfood loop has exposed failures that ordinary happy-path tests missed:

- [Empty completions](https://github.com/PsiACE/landing/issues/3): partial work
  must remain inspectable, and an empty answer must not count as successful
  completion. The runtime now fails empty output explicitly; a regression check
  preserves the user-visible behavior.
- [Revision interpretation](https://github.com/PsiACE/landing/issues/5): a model
  can mistake a run's triggering head for the actual CI checkout revision.
  The adapter records and appends the workflow-supplied checkout revision, but
  the model's interpretation still needs real-task evaluation. A footer alone
  does not prove the advice is correct.

## Verify the container contract

With Docker or Podman:

```bash
docker build -t landing:local .
uv run python tests/container_smoke.py --image landing:local
```

This acceptance checks health, authentication, graceful shutdown, paused-volume restore, and recovery onto an empty primary volume from a real Litestream file replica. Remote object storage and ONCE deployment need acceptance in their own environments. See [Replication and recovery](guides/recovery.md).

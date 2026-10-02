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

Write behavior tests for what users see through CLI, HTTP, and workflow outcomes. Write regression tests for actual mistakes likely to recur. Prefer end-to-end acceptance for platform wiring. Tests should survive an implementation rewrite that preserves the user's experience. Before release, test the supported workflow rather than preserving hypothetical legacy behavior or labelling every edge case a regression.

Assert public fields when they establish what the user observes, such as a result, gate decision, cancellation, or published comment location. Avoid assertions about helper structure, event positions, exception class names, raw database rows, provider option spelling, or other incidental details. Do not repeat the upstream SDK's query, chunking, or parameter-translation tests. Straightforward glue can be inspected directly and accepted through a real workflow run. Adding a test merely because a file changed does not improve the contract.

The test suite replaces external model requests with deterministic responses; the SDK loop, tools, SQLite, shell checks, and local HTTP transport execute normally. GitHub behavior tests use a local platform emulator. Real workflow runs establish downstream delivery and model quality.

## Run the continuous loop

| Trigger | Work | Evidence and human decision |
| --- | --- | --- |
| Candidate PR and native CI | Gatekeeper evaluates the diff and linked acceptance criteria. | Checks run first; the recommendation is advisory and states the inspected revision. |
| Failed default-branch checks | Issuer follows the affected native failures. | Healthy checks skip feedback; resolved historical defects do not create new work. |
| Maintainer delegation | `explain` answers, `triage` tracks, `fix` repairs, or `review` evaluates. | The requested mode does one task; maintainers choose the next action. |
| Fixer candidate | The agent validates and publishes the delegated candidate using repository procedures. | Main checks and review evaluate it again before human acceptance. |
| Release completion | Issuer follows relevant failure or recovery evidence from that release. | Update matching issues only for useful changes; unchanged issues stay quiet. A build or merged PR is not proof of deployed recovery. |

The reusable Action is `action.yml`; Landing's own workflows call it with `uses: ./` and select independent tool and skill collections through `.github/landing.yml`. The workflows are `.github/workflows/main.yml`, `landing.yml`, and `landing-duty.yml`. See [GitHub setup](guides/github.md) for model configuration, permissions, comment delegation, publication, and workflow-token dispatch. There is no model-driven merge or automatic chain that repairs every finding.

Retain investigation details in execution artifacts. Update the relevant issue or PR when evidence, conditions or outcomes change; do not post repeated status reports. Improve project instructions, tools, code, or meaningful regression cases based on useful observations.

Use native checks and actual human outcomes to evaluate changes. A model's own `allow` is not a quality metric. Per-job databases and artifacts expire after 30 days; lasting lessons belong in work items and the repository.

## Evaluate task outcomes

An empty answer must fail visibly, retain partial edits, and remain inspectable. A failed required check must block a review or fail a fix. Exercise these outcomes without multiplying every combination of mode, intermediate state, and error field.

Review quality needs real evidence. Verify the candidate revision separately from the CI checkout and deployed revision, and confirm platform publication at the intended destination. Deterministic protocol tests cannot establish that a model understands the evidence or produces useful findings.

## Verify the container contract

With Docker or Podman:

```bash
docker build -t landing:local .
uv run python tests/container_smoke.py --image landing:local
```

This acceptance checks health, authentication, graceful shutdown, paused-volume restore, and recovery onto an empty primary volume from a real Litestream file replica. Remote object storage and ONCE deployment need acceptance in their own environments. See [Replication and recovery](guides/recovery.md).

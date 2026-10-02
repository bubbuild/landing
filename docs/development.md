# Develop and dogfood

Landing's development uses the same Action, commands, SQLite records, and GitHub adapter offered to users. Native checks, work items, releases, and maintainer feedback make this a continuous process.

## Set up development

With Python 3.12 or later and uv:

```bash
uv sync
uv run pre-commit install
make check
make test
make docs-test
uv build
```

Use `make docs` for preview. Documentation commands export the server's OpenAPI schema and Scalar reference without starting a worker; generated files are not committed. Run checks appropriate to the change; native CI owns the full Python 3.12–3.14 matrix, quality, strict documentation build, and container recovery. See [Contributing](https://github.com/bubbuild/landing/blob/main/CONTRIBUTING.md) for discussion and submission.

## Test what people observe

Behavior tests cover supported CLI, HTTP, SDK, and workflow outcomes. Regression tests cover actual mistakes likely to recur. Assertions on public results, cancellation, capability selection, or comment locations can establish those contracts. Tests should survive an implementation rewrite that preserves the experience.

Avoid tests for helper structure, internal event positions, database rows, or upstream parameter translation. Straightforward glue can be inspected and accepted through a real workflow. Add a focused counterexample for an unresolved behavior rather than testing every intermediate state or field.

The suite replaces external model requests with deterministic responses while running the SDK loop, tools, SQLite, checks, and local HTTP normally. GitHub tests use a platform emulator and isolate ambient identities. Fixtures isolate Landing and Bub settings; `.github/landing.yml` is dogfood configuration, not test configuration. Real workflow results establish downstream delivery and model quality.

## Run the feedback loop

Main checks candidate PRs, then Landing reviews the affected behavior with those conclusions. Default-branch native failures receive triage; healthy checks skip model feedback. Automatic review and triage use GitHub's `continue-on-error` on the Action step: failures emit a warning and retain their task records without failing native CI. Admission and environment preparation outside the Action remain strict, as do explicit delegations. Maintainers can delegate explanations, fixes, triage, and reviews through explicit comment commands. Release follow-up uses the triggering release's failure or recovery evidence.

The release workflow also deploys documentation from main on push or manual dispatch. Package publication runs only for a published release; documentation-only runs do not trigger issuer follow-up. Pages artifacts use attempt-specific names so deployment retries select one artifact.

```text
Issue or failure -> Delegated work -> Candidate PR -> Native checks
      ^                                                 |
      |                  Review and maintainer feedback |
      +-------------------------------------------------+
```

The composite Action is `action.yml`. Project workflows call `uses: ./`, prepare tools and skills separately, and select mode capabilities through `.github/landing.yml`. Main groups review work by PR and cancels old candidates; duty loads default-branch policy, admits explicit commands or trusted release events, and queues delegations separately. [GitHub integration](guides/github.md) explains identity, trust, and publication.

For a workflow-token candidate, start Main explicitly using the repository procedure:

```bash
gh workflow run main.yml --ref BRANCH -f number=PR_NUMBER -f head=CANDIDATE_SHA
```

Candidate self-checks install Landing from that checkout. A duty task using the default-branch runtime does not reload it when the selected workspace changes.

Documentation pages, site configuration, and API contract changes enable Playwright preparation in Main's candidate review job. Other changes skip it. To request a candidate browser review explicitly, add `-f browser=true` to the command above. The workflow builds a separate preview from that candidate, reuses the runner's Chrome, and saves browser output in the Landing artifact. If browser preparation fails, review continues without claiming browser verification. `.agents/skills/landing-review/SKILL.md` owns the evidence and screenshot publication rules.

Bundled jobs use a 120-second model request timeout. Main's feedback Action has a 20-minute step timeout inside a 25-minute job, leaving time for warnings and artifact upload. Duty has a 20-minute job timeout. Per-job SQLite artifacts expire after 30 days. Retain lasting findings in issues, project guidance, code, and meaningful regression cases. Update work items when evidence or outcomes change; avoid repeated status reports.

## Publish a release

Set the version with `uv version VERSION` and update installation examples and Action references. After validation and merge, publish a GitHub release with the matching unprefixed tag, such as `0.1.1`. The workflow verifies the version, publishes to PyPI and GHCR, and deploys the documentation. The tag also selects the Action; stable images update `latest`.

## Learn from outcomes

Check whether the explanation helped, the repair met acceptance, and feedback reached its intended destination. A model's `allow` is not its quality score. A healthy test matrix does not establish recovery from a deployment failure; verify that failure in the relevant environment.

Keep investigation details in artifacts and public feedback useful to the person receiving it. Automated work can proceed within its authorization. Maintainers stay involved in welcoming contributors, understanding concerns, and resolving disagreements, following [Community Over Code](working-with-landing.md#community-over-code).

## Verify container recovery

With Docker or Podman:

```bash
docker build -t landing:local .
uv run python tests/container_smoke.py --image landing:local
```

This acceptance exercises health, authentication, graceful shutdown, paused-volume restore, and restoration from a real Litestream file replica onto an empty primary volume. Remote storage and ONCE deployment need verification in their own environments. See [Recovery](guides/recovery.md).

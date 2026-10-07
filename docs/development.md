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

Use `make docs` for preview. Documentation commands export the server's OpenAPI schema without starting a worker; generated files are not committed. Run checks appropriate to the change; native CI owns the full Python 3.12–3.14 matrix on Linux, a macOS test run, quality, strict documentation build, and container recovery. See [Contributing](https://github.com/bubbuild/landing/blob/main/CONTRIBUTING.md) for discussion and submission.

## Test what people observe

Behavior tests cover supported CLI, HTTP, SDK, and workflow outcomes. Regression tests cover actual mistakes likely to recur. Assertions on public results, cancellation, capability selection, or comment locations can establish those contracts. Tests should survive an implementation rewrite that preserves the experience.

Avoid tests for helper structure, internal event positions, database rows, or upstream parameter translation. Straightforward glue can be inspected and accepted through a real workflow. Add a focused counterexample for an unresolved behavior rather than testing every intermediate state or field.

The suite replaces external model requests with deterministic responses while running the SDK loop, tools, SQLite, checks, and local HTTP normally. GitHub tests use a platform emulator and isolate ambient identities. Fixtures isolate ambient Landing and Bub settings. Real workflow results establish downstream delivery and model quality.

## Runtime architecture

CLI, HTTP, SDK, and GitHub use one Runtime, which executes tasks through Bub's SDK Agent. Entries select work and its delivery surface; the accepted SQLite request is the execution source. Each request records its selected mode; the executor restores full session state and prepares the environment. Package entry points declare bundled adapters; Landing loads only its own declarations into Bub's hook manager, which collects their Typer commands. Framework creation reads YAML; Agent configuration, resources, and model execution activate after GitHub admission. Help and history inspection start no agent. Modes apply to individual tasks rather than session state.

```text
CLI triage / fix / review / explain ----> run --------+
Python run / run_stream ----------------------------+--> SQLite -> execute
HTTP POST / Python submit --> receipt --> worker ----+               |
GitHub event --> admission --> run ------------------+       Bub Agent + checks
                                                                    |
                                                     result / stream / reply
```

HTTP accepts work without waiting for completion; its worker continues after the request ends. CLI and GitHub wait for their delegated outcome. Every execution host consumes the same action stream; CLI and worker calls drain it to obtain the final record. Only explicit `action watch` polls. Each mode uses the same chain with its own skills and capability limits: issuer identifies changed problems, fixer repairs them, gatekeeper evaluates candidates, and explainer answers the current question. Review recommendations remain advisory.

Task records and Bub tapes share one SQLite database. Bub exposes task records through a tape sidecar. Resetting a session's tape clears its model history without deleting action records.

```text
Host starts -> Bub lifespan -> lock + SQLite recovery
      |
      +--> action scopes: environment + shell + MCP
      |          |
      |    model tools close -> post-fix checks -> completion
      |
Host exits -> stop and await work -> close environments -> unlock SQLite
```

`Runtime.running()` owns foreground execution; `Runtime.lifespan` uses it with a worker for ASGI. A host-provided environment applies to both tools and checks; otherwise they use the selected workspace. State and skills stay isolated by workspace and session. A reused Runtime supports later lifespans.

Cancellation goes through the executing host. Closing an unfinished SDK stream cancels its action; service shutdown marks active work interrupted and leaves queued work for restart. Interrupted work requires inspection before retry because external effects may already exist. Other processes can read history, but offline cancellation requires exclusive database ownership.

GitHub enables reply confirmation through a native hook and verifies publication identity, destination, and candidate revision. Replayed deliveries reuse their existing action; superseded candidates stop without publishing. `LANDING_REPLICATE` delegates service supervision and backup recovery to Litestream, which starts the same CLI with replication disabled in its child. The container prepares its directory and executes the ordinary CLI. Help, invalid arguments, and non-service commands bypass replication.

## Instructions and skills

`LandingHooks` supplies task guidance, state, and storage alongside host hooks. Each task constructs a native SDK Agent with its tools, skill roots, and shared SQLite store; tape context and tool interception reuse Bub defaults. The system prompt places common behavior, the selected mode's permitted skill, configured additions, and root `AGENTS.md` before the workspace path. Bub appends native tool and skill guidance. Stable prefixes help cache reuse, subject to the provider and available capabilities.

Task instructions and evidence stay in task inputs. GitHub adaptation owns destination and platform guidance. Owned templates substitute named values once and keep inserted text literal. Bundled methods ship in the native `src/skills` namespace; project overrides and capability limits use the normal [skills and mode settings](reference/configuration.md#mode-capabilities).

## Run the feedback loop

Main checks candidate PRs, then Landing reviews the affected behavior with those conclusions. Default-branch native failures receive triage; healthy checks skip model feedback. Automatic review and triage use GitHub's `continue-on-error` on the Action step: failures emit a warning and retain their task records without failing native CI. Environment preparation outside the Action remains strict, as do explicit delegations. Maintainers can delegate explanations, fixes, triage, and reviews through explicit comment commands. Release follow-up uses the triggering release's failure or recovery evidence.

The release workflow also deploys documentation from main on push or manual dispatch. Package publication runs only for a published release; documentation-only runs do not trigger issuer follow-up. Pages artifacts use attempt-specific names so deployment retries select one artifact.

```text
Issue or failure -> Delegated work -> Candidate PR -> Native checks
      ^                                                 |
      |                  Review and maintainer feedback |
      +-------------------------------------------------+
```

The composite Action is `action.yml`. Main and duty prepare project tools and skills, then call `uses: ./` once with built-in admission. Landing discovers the repository's `.agents` skills and MCP configuration with its default settings. Main groups review work by PR and cancels old candidates; duty uses the default-branch checkout for its prepared environment, admits explicit commands or trusted release events, and queues delegations separately. Admission protects delegated agent work; native workflow policy governs the prepared environment. [GitHub integration](guides/github.md) explains identity, trust, and publication.

Configure this repository's workflows with these GitHub variables and secrets:

| Setting | Purpose |
| --- | --- |
| Variable `LANDING_MODEL` | Model identifier; unset disables feedback. |
| Variables `LANDING_API_BASE`, `LANDING_COMPLETION_ARGS` | Optional endpoint and completion options. |
| Secret `LANDING_API_KEY` | Provider key. |
| Variable `LANDING_TRUST` | Caller policy, default `repository`. |
| Secret `LANDING_ADMISSION_TOKEN` | Optional admission credential; organization owner checks need Members read. |
| Secret `LANDING_GITHUB_TOKEN` | Publication token, default workflow token. |
| Variables `LANDING_GIT_NAME`, `LANDING_GIT_EMAIL` | Optional commit identity; set both. Default is the standard Actions bot. |

For a workflow-token candidate, start Main explicitly using the repository procedure:

```bash
gh workflow run main.yml --ref BRANCH -f number=PR_NUMBER -f head=CANDIDATE_SHA
```

Candidate self-checks install Landing from that checkout. A duty task using the default-branch runtime does not reload it when the selected workspace changes.

The repository includes a [Playwright MCP example](https://github.com/bubbuild/landing/blob/main/.agents/mcp.json) for demonstration and browser checks during development.

Bundled jobs use a 120-second model request timeout. Main's feedback Action has a 20-minute step timeout inside a 25-minute job, leaving time for warnings and artifact upload. Duty has a 20-minute job timeout. Per-job SQLite artifacts expire after 30 days. Retain lasting findings in issues, project guidance, code, and meaningful regression cases. Update work items when evidence or outcomes change; avoid repeated status reports.

## Publish a release

Set the version with `uv version VERSION` and update installation examples and Action references. After validation and merge, publish a GitHub release with the matching unprefixed tag, such as `0.2.0`. The workflow verifies the version, publishes to PyPI and GHCR, and deploys the documentation. The tag also selects the Action; stable images update `latest`.

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

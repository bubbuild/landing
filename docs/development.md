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

Behavior tests cover supported CLI, HTTP, and workflow outcomes. Regression tests cover actual mistakes likely to recur. Assertions on public results, cancellation, capability selection, or comment locations can establish those contracts. Tests should survive an implementation rewrite that preserves the experience.

Avoid tests for helper structure, internal event positions, database rows, or upstream parameter translation. Straightforward glue can be inspected and accepted through a real workflow. Add a focused counterexample for an unresolved behavior rather than testing every intermediate state or field.

The suite replaces the provider HTTP transport with deterministic responses while running Bub, Republic, tools, SQLite, checks, and local HTTP normally. GitHub tests use a platform emulator and isolate ambient identities. Fixtures isolate ambient Landing and Bub settings. Real workflow results establish downstream delivery and model quality.

## Runtime architecture

Landing is an application with CLI, HTTP, and GitHub entry points. Its internal Runtime uses `BubAgent`, Bub's core Agent configured with native Framework hooks; Landing does not expose an embedding SDK. Landing pins the published `bub[app]==0.6.0a3` package. The `app` dependencies provide reusable hooks and tools; Landing owns its entry points and does not run Bub's message pipeline. Mode selection and accepted SQLite requests drive execution.

```text
CLI / GitHub -> admission -> run -----+
HTTP -> receipt -> background worker -+-> SQLite -> execute
                                                     |
                         Framework hooks -> BubAgent + checks
                                |                    |
                      hooks / state / storage    result / reply
```

The CLI registers Landing commands and bundled adapter commands directly. Help and history inspection start no agent. Each execution creates a BubAgent inside the Framework lifespan, restores state with `Framework.build_state()`, selects tools, then consumes the native stream inside `Agent.run()`. BubAgent subscribes native interception hooks and supplies continuations and steering. Landing supplies its SystemPrompt and disables command parsing so delegated text remains model input. Agent and tool resources close before post-edit checks and delivery verification.

HTTP accepts work without waiting for completion; its worker continues after the request ends. CLI and GitHub wait for their delegated outcome. The CLI and worker await the same owned execution task, which consumes the native Agent stream. Only explicit `action watch` polls. Each mode uses the same chain with its own skills and capability limits: issuer identifies changed problems, fixer repairs them, gatekeeper evaluates candidates, and explainer answers the current question. Review recommendations remain advisory.

Landing task records and Bub tapes share one SQLite database. Resetting a session's tape clears its model history without deleting action records.

```text
Host starts -> Framework lifespan -> lock + SQLite recovery
      |
      +--> action scopes: environment + shell + MCP
      |          |
      |    model tools close -> post-fix checks -> completion
      |
Host exits -> stop and await work -> close environments -> unlock SQLite
```

`Runtime.running()` owns foreground execution; `Runtime.lifespan` uses it with a worker for ASGI. A `provide_environment` plugin applies to both tools and checks; otherwise they use the selected workspace. State and skills stay isolated by workspace and session. A reused Runtime supports later lifespans.

Cancellation goes through the executing host. Interrupting a foreground invocation cancels its action; service shutdown marks active work interrupted and leaves queued work for restart. Interrupted work requires inspection before retry because external effects may already exist. Other processes can read history, but offline cancellation requires exclusive database ownership.

GitHub adds `confirm_reply` for its run and verifies publication identity, destination, and candidate revision. Replayed deliveries reuse their existing action; superseded candidates stop without publishing. `LANDING_REPLICATE` delegates service supervision and backup recovery to Litestream, which starts the same CLI with replication disabled in its child. The container prepares its directory and executes the ordinary CLI. Help, invalid arguments, and non-service commands bypass replication.

## Instructions and skills

`Runtime` explicitly selects native filesystem, shell, web, tape, and spill tools and adds Landing tools. `LandingHooks` supplies state, storage, and execution policy through native hooks. BubAgent connects Framework interception hooks to the Agent's EventBus and uses native continuation and steering support. Additional tools use Bub's `@tool` and join the instance's tool collection before execution. Landing depends on `bub[app]` to reuse these components and supplies the core Agent's default Tape context. A result hook bounds oversized text through Bub's `SpillStore` and content helpers, preserves media, and leaves output inline when the mode disables `spill.read`. Full output stays in SQLite for later runs in the same session. Unknown or unavailable tools follow core tool error handling.

The task skill tool uses Bub's discovery and rendering with Landing's mode selection; Bub's app-specific skill and subagent tools are not selected. Bub's `SystemPrompt` and `@prompt.add` compose the common behavior and dynamic sections from `PromptContext`. These sections place the selected mode's permitted skill, configured additions, and root `AGENTS.md` before the workspace path, followed by the selected skill index and any Framework system-prompt hook contributions.

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

Versions come from Git tags through `hatch-vcs`; untagged commits receive development versions with a commit hash. GitHub source archives carry version metadata for Action installation. CLI and API versions use installed package metadata. Source copies without Git, archive, or package metadata use `0.0.0`; the standard `SETUPTOOLS_SCM_PRETEND_VERSION` variable supplies a version when building the container.

After validation and merge, publish a GitHub release with an unprefixed version tag for the intended release. The workflow verifies the version, publishes to PyPI and GHCR, and deploys the documentation. The tag also selects the Action; stable images update `latest`.

## Learn from outcomes

Check whether the explanation helped, the repair met acceptance, and feedback reached its intended destination. A model's `allow` is not its quality score. A healthy test matrix does not establish recovery from a deployment failure; verify that failure in the relevant environment.

Keep investigation details in artifacts and public feedback useful to the person receiving it. Automated work can proceed within its authorization. Maintainers stay involved in welcoming contributors, understanding concerns, and resolving disagreements, following [Community Over Code](working-with-landing.md#community-over-code).

## Verify container recovery

With Docker or Podman:

```bash
docker build --build-arg SETUPTOOLS_SCM_PRETEND_VERSION="$(uv run --reinstall-package landing landing --version)" -t landing:local .
uv run python tests/container_smoke.py --image landing:local
```

This acceptance exercises health, authentication, graceful shutdown, paused-volume restore, and restoration from a real Litestream file replica onto an empty primary volume. Remote storage and ONCE deployment need verification in their own environments. See [Recovery](guides/recovery.md).

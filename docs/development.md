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

Use `make docs` for preview. Documentation builds do not start execution or contact GitHub. Run checks appropriate to the change; native CI owns the full Python 3.12–3.14 matrix on Linux, a macOS test run, quality, strict documentation build, and container startup/shutdown. See [Contributing](https://github.com/bubbuild/landing/blob/main/CONTRIBUTING.md) for discussion and submission.

## Test what people observe

Behavior tests cover supported CLI, HTTP, and workflow outcomes. Regression tests cover actual mistakes likely to recur. Assertions on public results, cancellation, capability selection, or comment locations can establish those contracts. Tests should survive an implementation rewrite that preserves the experience.

Avoid tests for helper structure, internal event positions, database rows, or upstream parameter translation. Straightforward glue can be inspected and accepted through a real workflow. Add a focused counterexample for an unresolved behavior rather than testing every intermediate state or field.

The suite replaces the provider HTTP transport with deterministic responses while running Bub, Republic, tools, SQLite, checks, and local HTTP normally. GitHub tests use a platform emulator and isolate ambient identities. Fixtures isolate ambient Landing and Bub settings. Real workflow results establish downstream delivery and model quality.

### Evaluate developer interactions

Evaluate whether a developer can find the current outcome, remaining blocker and next required action at the established work item. Comment count alone is not a quality measure: a quiet agent can miss a question, and a useful review can contain several independent finding threads. The shared prompt owns continuity across updates, mode skills own working methods and stopping boundaries, and adapters own platform destinations and publication verification.

Use small temporal scenarios with a candidate, existing discussion and bounded evidence. Cover new independent findings, unchanged findings, repairs followed by another assessment, contradicted claims, and explicit questions with unchanged conclusions. Include the other modes' decisions: unchanged automatic triage, repair versus release status, explanations without unsolicited edits, and dependency failures that no longer apply to the current candidate. Feed each stage the preceding stage's actual public state, not an ideal transcript. Keep historical and constructed cases distinct, and exclude later outcomes from earlier inputs.

Run model-backed interaction replays through Landing with isolated publication tools and no real publishing credentials. Record the runtime and instruction revisions, model options, source evidence, actual writes, readbacks and final results. Repeat selected scenarios to expose variability. Inspect missing required replies, redundant writes and unnecessary prose separately. A routine answer or individual finding normally needs two to four short sentences; each should contribute the conclusion, decisive evidence or a necessary next action. Check whether removing a sentence loses information the recipient needs. Longer detail belongs behind a link or fold unless the task requires it. Do not assert exact wording, enforce a universal character limit, or reward silence at the expense of actionable findings. Track fixture gaps and execution limits separately from product failures.

Deterministic tests establish identity, destination, stale-candidate, required-check and receipt boundaries. Scripted provider results test publication flow, not model judgment. Model-backed simulations test choices within their supplied evidence and platform model; they do not prove GitHub notifications or end-to-end repairs. Use ordinary candidate PR activity for live confirmation, reusing existing CI rather than starting overlapping runs for the evaluation.

## Runtime architecture

Landing is an application with CLI, HTTP, and GitHub entry points. Its internal Runtime uses `BubAgent`, Bub's core Agent configured with native Framework hooks; Landing does not expose an embedding SDK. Landing pins the published `bub[app]==0.6.1` package. The `app` dependencies provide reusable hooks and tools; Landing owns its entry points and does not run Bub's message pipeline. Mode selection and accepted SQLite requests drive execution.

```text
HTTP webhook -> verify/filter -> local inbox -> worker process --+
                          \-> GHA dispatch -> Action CLI -------+-> GitHub execution API
Native Action / github event CLI -------------------------------+          |
                                                                  policy + request
                                                                          |
Plain CLI ------------------------------------------------------------> Runtime
                                                                          |
                                                                  BubAgent + checks
                                                                          |
                                                                  publication verify
```

The CLI registers Landing commands and bundled adapter commands directly. Help and history inspection start no agent. Each execution creates a BubAgent inside the Framework lifespan, restores state with `Framework.build_state()`, selects tools, then consumes the native stream inside `Agent.run()`. BubAgent subscribes native interception hooks and supplies continuations and steering. Landing supplies its SystemPrompt and disables command parsing so delegated text remains model input. Agent and tool resources close before post-edit checks and delivery verification.

Local intake returns after persisting the original event in SQLite; GHA intake returns after GitHub accepts dispatch. `LocalRunner` uses `provide_lifespan` to own the inbox consumer, `provide_environment` to supply the native host environment, and Bub's shell manager to own child processes and shutdown. The isolated worker reads the accepted event by inbox ID and calls the GitHub execution API directly. There is no temporary event file, synthetic `GITHUB_EVENT_PATH`, Typer parsing, or environment-variable task handoff. The process boundary keeps synchronous GitHub calls and model execution off the HTTP loop. Both worker and Action CLI use the same execution API and publication hooks.

### Module boundaries

```text
cli.py / server.py / worker.py          entry points and process ownership
  -> adapters/github/execution.py      event-to-action orchestration
       -> events.py                    caller policy and mode routing
       -> publication.py               publication receipts and native hooks
       -> api.py / settings.py         prepared GitHub access and configuration
       -> runtime.py                   Bub execution, tools, checks, result state
            -> hooks.py / mcp.py       native SDK integrations
            -> tasks.py / store.py     action records and model history
  -> inbox.py                          local intake persistence
```

`local.py` schedules a worker process; it neither parses GitHub events nor implements SQL operations. `worker.py` loads accepted input and invokes the execution API. `adapters/github/webhook.py` owns webhook verification and Actions placement, not model execution. Integration modules never import CLI or HTTP modules. Native Actions context is passed explicitly to admission; local execution cannot acquire authority from ambient Actions variables. Process entry points register `TerminationHooks`; publication code no longer installs process signal handlers.

The local inbox has only pending messages and retained errors. It is not a GHA receipt/claim protocol. Finished or ignored messages leave the inbox; action records and tapes remain. Waiting work survives restart, while native action identity and publication verification prevent silently repeating already-started work. CLI and runner execution share native state, environment, tape-store, tool, and interception hooks.

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

`Runtime.running()` owns foreground execution. The former local ASGI execution queue and Runtime submission API are removed. A `provide_environment` plugin applies to both tools and checks; otherwise they use the selected workspace. State and skills stay isolated by workspace and session. A reused Runtime supports later lifespans.

Cancellation goes through the executing host. Interrupting a foreground invocation cancels its action; runner shutdown marks active work interrupted; receiver shutdown does not cancel a remote run. Interrupted work requires inspection before retry because external effects may already exist. Other processes can read history, but offline cancellation requires exclusive database ownership.

GitHub adds `confirm_reply` for its run and verifies publication identity, destination, and candidate revision. Replayed deliveries reuse their existing action; superseded candidates stop without publishing. The container starts `serve`; Compose explicitly chooses GHA placement. Local service mode retains its inbox and execution database.

## Instructions and skills

`Runtime` explicitly selects native filesystem, shell, web, tape, and spill tools and adds Landing tools. `LandingHooks` supplies state, storage, and execution policy through native hooks. BubAgent connects Framework interception hooks to the Agent's EventBus and uses native continuation and steering support. Additional tools use Bub's `@tool` and join the instance's tool collection before execution. Landing depends on `bub[app]` to reuse these components and supplies the core Agent's default Tape context. A result hook bounds oversized text through Bub's `SpillStore` and content helpers, preserves media, and leaves output inline when the mode disables `spill.read`. Full output stays in SQLite for later runs in the same session. Unknown or unavailable tools follow core tool error handling.

The task skill tool uses Bub's discovery and rendering with Landing's mode selection; Bub's app-specific skill and subagent tools are not selected. Bub's `SystemPrompt` and `@prompt.add` compose the common behavior and dynamic sections from `PromptContext`. These sections place the selected mode's permitted skill, configured additions, and root `AGENTS.md` before the workspace path, followed by the selected skill index and any Framework system-prompt hook contributions.

Task instructions and evidence stay in task inputs. GitHub adaptation owns destination and platform guidance. Owned templates substitute named values once and keep inserted text literal. Bundled methods ship in the native `src/skills` namespace; project overrides and capability limits use the normal [skills and mode settings](reference/configuration.md#mode-capabilities).

## Run the feedback loop

Main runs native quality, tests, typing, documentation, and container checks. GitHub webhooks route comments, PR reviews, failed upstream workflows, and alerts through the receiver. `landing-execute.yml` runs the original GitHub event with the ordinary Action; the same workflow handles weekly and manual dependency maintenance. The previous Main feedback job and duty listener are removed rather than retained as a second event path.

Prepare `LANDING_MODEL`, `LANDING_API_KEY`, optional `LANDING_API_BASE`, and `LANDING_GITHUB_WEBHOOK_SECRET` in the executor. Publication defaults to the workflow token; `LANDING_GITHUB_TOKEN` and `LANDING_ADMISSION_TOKEN` can provide separate identities. Configure runner policy with `LANDING_GITHUB_*`, not forwarded payload fields or repeated workflow conditions. The executor prepares skills, Git authentication, and the repository's MCP tools before delegation, and uploads SQLite and browser evidence with 30-day retention.

For a workflow-token candidate, start Main explicitly:

```bash
gh workflow run main.yml --ref BRANCH -f number=PR_NUMBER -f head=CANDIDATE_SHA
```

Main checks the supplied candidate revision. The executor itself stays on its configured trusted ref; changing the task checkout does not reload its runtime. CI remains independent of the agent's review recommendation. Release publication and documentation deployment retain their existing workflows.

## Publish a release

Versions come from Git tags through `hatch-vcs`; untagged commits receive development versions with a commit hash. GitHub source archives carry version metadata for Action installation. CLI and API versions use installed package metadata. Source copies without Git, archive, or package metadata use `0.0.0`; the standard `SETUPTOOLS_SCM_PRETEND_VERSION` variable supplies a version when building the container.

After validation and merge, publish a GitHub release with an unprefixed version tag for the intended release. The workflow verifies the version, publishes to PyPI and GHCR, and deploys the documentation. The tag also selects the Action; stable images update `latest`.

## Learn from outcomes

Check whether the explanation helped, the repair met acceptance, and feedback reached its intended destination. A model's `allow` is not its quality score. A healthy test matrix does not establish recovery from a deployment failure; verify that failure in the relevant environment.

Keep investigation details in artifacts and public feedback useful to the person receiving it. Automated work can proceed within its authorization. Maintainers stay involved in welcoming contributors, understanding concerns, and resolving disagreements, following [Community Over Code](working-with-landing.md#community-over-code).

## Verify the webhook container

```bash
docker build --build-arg SETUPTOOLS_SCM_PRETEND_VERSION="$(uv run landing --version)" -t landing:local .
uv run python tests/container_smoke.py --image landing:local
```

The smoke test starts the non-root image in GHA placement mode without storage, model settings, or GitHub credentials, checks liveness and signature handling using an ignored ping, and verifies graceful shutdown. It makes no GitHub writes and does not establish real-provider execution or external delivery.

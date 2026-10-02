# Use Landing with GitHub

Prepare a trusted checkout, authenticated `gh` in PATH, project dependencies, model settings, and any skills. Landing supplies event admission, repository context, and publication receipts. The agent uses native tools and the prepared `gh` CLI to investigate, publish reviews, reply, and repair. CLI and HTTP execution work without GitHub.

## Use the prepared environment

```bash
uv run landing --github-repository example/team-project triage "Investigate the evidence, reuse an existing issue or create an actionable one." --input report.txt
```

`--github-repository` supplies context, not a separate restricted gh tool. Select each mode's tools and skills through [configuration](../reference/configuration.md#mode-capabilities). The executing environment owns credentials and platform permissions; Landing does not install skills or change authentication.

## Choose the publishing identity

Your gh credentials determine who publishes comments, reviews and PRs. Git's author and committer settings determine commit attribution. Prepare both before delegating publication. Landing uses those identities and must report a missing required identity rather than invent one. The `/landing` prefix and publication markers do not identify an account.

| Environment | Publication identity | Commit identity |
| --- | --- | --- |
| Bundled workflows, default | `github-actions[bot]` through the workflow token | GitHub's standard Actions bot name and noreply email. |
| Personal or machine account | That account's gh login or token | The account's name and verified or GitHub-provided noreply email. |
| GitHub App | The App's installation token | The App bot's actual login and ID-based noreply email. |

The bundled workflows accept an optional `LANDING_GITHUB_TOKEN` secret. Set repository variables `LANDING_GIT_NAME` and `LANDING_GIT_EMAIL` together to choose the commit identity. These variables populate Git's standard author and committer environment variables; selecting another token alone leaves the standard Actions commit identity in place. They are workflow settings, not Landing runtime settings.

In your own CLI, server or Action environment, use existing Git configuration or `GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL`, `GIT_COMMITTER_NAME` and `GIT_COMMITTER_EMAIL`. Pass `GH_TOKEN` or use the existing gh login. Prepare Git transport with `gh auth setup-git` when pushing. Landing's Action preserves caller-provided values.

For an organization-owned identity, prepare a GitHub App owned by the organization or an authorized machine account. Organizations themselves cannot act as users. Use the official [create-github-app-token Action and Git identity recipe](https://github.com/actions/create-github-app-token#configure-git-cli-for-an-apps-bot-user) in the same job, then pass its token as `GH_TOKEN` and its bot identity through Git's standard settings. Do not construct a noreply email from the product or organization name. App setup and token renewal belong to the prepared environment.

## Follow repository conventions

When creating an issue or PR, the agent reads the applicable contribution template from the selected checkout in [GitHub's standard locations](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/about-issue-and-pull-request-templates): root, `docs`, and `.github`, including `ISSUE_TEMPLATE` and `PULL_REQUEST_TEMPLATE` directories and YAML issue forms. Templates are not loaded in bulk. YAML form field labels become Markdown headings; gh does not submit the interactive web form. Fill requested sections with actual evidence and leave unverified checkboxes unchecked.

Root and scoped `AGENTS.md` instructions and [skills](../make-it-yours.md#use-skills) supply repository procedures. Put branch conventions, native checks, benchmark criteria, and workflow dispatch instructions there. Prepare an isolated checkout for changes. Landing leaves branch selection, commits, pushes, and candidate publication to the agent within the delegated scope.

## Invoke locally or in CI

The event runner works outside GitHub Actions with your active gh login:

```bash
uv run python -m landing.adapters.github review --repository example/team-project --number 42 --head candidate-head-sha --instruction "Review the candidate and publish actionable findings." --delivery-key "review-42-request-1" --db ./evidence/landing.sqlite3 --check "make acceptance"
```

Use `fix`, `triage`, or `explain` for the corresponding work. The runner also accepts repeatable `--skill-dir PATH` and `--upstream-workflow NAME`, plus `--event FILE`, `--trust repository|owner`, `--run-id ID`, and `--checked-revision SHA`. Candidate head and actual CI checkout revision are separate evidence. Invoke the [reusable Action](ci.md#github-action) in GitHub Actions; no dedicated dogfood script is needed.

The runner supplies the target reference, triggering comment and relevant revision metadata. The agent fetches specific issue details, discussions, templates or logs through gh when needed. Complete issue lists, target bodies and review histories are not injected; receipt checks use platform data internally. The runner rejects an already superseded review candidate before invoking the model. For a bound PR review, the native SDK tool interceptor checks the current head before each model tool batch and cancels the action when the candidate changes or the lookup fails. It does not restrict a fixer pushing a new candidate or an explanation of a historical thread. When local Git objects are available, it supplies whether the CI checkout contains the candidate; this does not establish identical trees. Supply task scope and necessary new evidence in `--instruction`; ordinary CLI and HTTP calls can attach evidence through their existing input interfaces.

A review of a PR publishes a native GitHub Review. The agent uses [the reviews API](https://docs.github.com/en/rest/pulls/reviews#create-a-review-for-a-pull-request) with `commit_id`, a review body, and inline comments containing verified `path`, `line`, and `side` locations. Ranges use `start_line` and `start_side`. Actionable findings belong at their actual diff locations; a clean candidate needs no invented inline comments. The default review event is `COMMENT`; approval and change requests require explicit repository authorization. The gate recommendation is recorded separately.

Explicit delegations reply to the selected issue or PR. Inline follow-ups use the [review-comment replies API](https://docs.github.com/en/rest/pulls/comments#create-a-reply-for-a-review-comment) in the original thread. The adapter checks required publication receipts and rejects reviews attached to a different requested commit. A text answer without the required publication is failed work. Replies include the delivery marker, or the agent calls `confirm_reply` with the native comment ID returned by gh. This tool verifies a new reply at the exact delegated PR and thread; the adapter verifies it again at completion and retains the receipt in SQLite. Include `confirm_reply` in the applicable mode's tool collection when limiting capabilities.

Automatic issuer follow-up updates an existing issue only for new evidence, changed conditions or impact, or verified progress. Unchanged conditions complete quietly: the agent calls `no_update` with its reason, which finishes the task through the native SDK and retains that reason in the execution record. Quiet completion requires error-free tool execution; a failed call stays with the agent for recovery and cannot waive publication verification. Explicit questions still require a reply. Repeated known failures, new run IDs and unrelated commits do not justify status comments.

Use GitHub's native `#number` and `owner/repo#number` references in conversations, outside code spans. Commit links render as short hashes; full SHAs remain in API calls and verification. Link relevant reviews, comments, jobs and file lines with short descriptive labels. Repository Markdown and other destinations use explicit links because GitHub conversation shorthand may not render there.

Each delivery carries a marker so the same database and delivery key can reuse an existing published result without invoking the model again. Use a new key for a new delegation. The marker confirms delivery, not the quality of the advice. The runner exits nonzero for execution or delivery failure; a gate recommendation of `block` or `inconclusive` is advisory here. Ordinary CLI review exit semantics are stricter.

## Wire commands and follow-ups

Listen for `issue_comment` and `pull_request_review_comment` events. Pass their event file through the Action or `--event`. A caller admitted by the selected trust policy can delegate with the first nonblank line:

```text
/landing triage Track the recurring release failure with acceptance criteria.
/landing fix Fix this issue and preserve its user-visible behavior.
/landing review Review the candidate against independent checks.
/landing explain Explain the failed job and the next useful check.
```

`/landing` is a command prefix, independent of the publishing account. Set `command-prefix` or `--command-prefix` if your team uses another prefix or an actual `@team-bot` identity. Every comment delegation requires an explicit command, including replies inside review threads. Status updates and Landing's own marked output do not start another task. An authorized machine account can issue commands; a bot name or App installation ID alone grants no caller authority. The adapter preserves the selected mode and replies in the original thread.

Comment and workflow-run listeners must exist on the default branch to receive events. Landing's `landing-duty.yml` provides the repository example; `landing.yml` and `main.yml` show reusable Action calls and native CI feedback.

## Choose who can delegate

GitHub admission stays in the adapter. `trust: repository` is the Action default and admits callers whose effective repository permission is `write` or `admin`; GitHub maps maintain to write and triage to read. `trust: owner` admits the personal repository owner by user ID against the current repository owner or an active organization owner by native membership (`role: admin`). It does not admit every repository administrator. Organization owner checks need a prepared token with Members read access. The [repository permission API](https://docs.github.com/en/rest/collaborators/collaborators#get-repository-permissions-for-a-user) and [organization membership API](https://docs.github.com/en/rest/orgs/members#get-organization-membership-for-a-user) remain authoritative; Landing keeps no member lists or permission cache.

Comment admission checks the comment author, rather than the PR author or `author_association`. Repository-policy manual Actions dispatch relies on GitHub's own workflow-write authorization, including authorized Apps; owner policy still checks identity. Other events check the native sender; `workflow_run` checks the originating run's actor. Known unauthorized callers skip without a model call or refusal comment. Organization owner admission first rejects callers without repository admin access, which every organization owner holds. This skips ordinary contributors before querying private membership. A remaining membership lookup can return 404 for absent or inaccessible data; it fails closed rather than guessing. Other API failures also fail the invocation instead of widening access. Reruns retain the original caller's scope; owner policy also checks the rerunning user. Permission changes take effect on the next admission. Local calls without an event use the executing user's prepared credentials.

The Action target must match `GITHUB_REPOSITORY`, and any event must belong to that repository. Owner trust narrows caller identity, not destination repositories. A `workflow_run` event additionally needs an explicit `upstream-workflow` name, matching source repository, and an allowed native source event: default-branch push or manual dispatch, or release. Success alone does not authorize the downstream agent. The bundled duty workflow allows only `release-main`. Bundled jobs use the `LANDING_TRUST` repository variable when set; it takes precedence over reusable-workflow inputs. An optional `LANDING_ADMISSION_TOKEN` secret supplies the admission credential, independently of `LANDING_GITHUB_TOKEN` for publication. Outside the bundled workflows, set `GH_ADMISSION_TOKEN` to use a separate admission credential; publication continues to use the prepared gh login or `GH_TOKEN`. For organization owner policy, give the admission token repository Metadata/Contents read and organization Members read permissions. For App-triggered tasks, use an authorized native manual dispatch with repository policy; owner policy does not exempt Apps. Prepare selected-repository permissions; an installation in the payload does not authorize an arbitrary comment author.

Admission is an execution check, not authentication for an event file. Supply event files only from a trusted local caller or GitHub Actions. A remote webhook receiver must validate GitHub's signature over the original request body before forwarding events; this adapter does not expose a webhook receiver.

Keep the policy, workflow implementation and configuration in a trusted default branch or pinned workflow. For owner-restricted credential jobs, also use GitHub environment deployment restrictions and required reviewers, with CODEOWNERS and branch governance for workflow changes. Repository administrators can edit settings; `trust: owner` alone cannot isolate credentials from those administrators. Use a read-only credential for admission, then expose model and publication credentials only in the admitted job. Job permissions constrain `GITHUB_TOKEN`, not a separately supplied PAT or App token. A maintainer's command on a fork PR does not authorize executing that fork's setup scripts, skills or configuration with credentials.

The composite Action checks admission before installing its runtime, but cannot protect setup steps the caller already executed. Put the gate before candidate checkout and dependency or skill preparation as shown in [CI setup](ci.md#github-action). Landing's candidate self-tests use same-repository workflow code; owner-restricted deployments need the protected source and environment controls above.

## Finish and cancel work

A fixer validates the needed behavior, pushes its candidate, starts native CI and replies with the publication and pending-check links. It waits for native results only when explicitly requested and never waits for its own duty or Landing feedback job. Native CI and human acceptance continue independently.

Use [GitHub concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency) at the review workflow entrance to cancel old candidates immediately. Tool interception and exact review `commit_id` verification supplement native cancellation, including stale reruns; neither is an atomic publication lock. An API request already in flight can finish, and existing reviews are not deleted. Explicit comment tasks use a separate native queue, so a new review does not cancel a delegated fix or explanation.

The Action forwards runner termination to normal stream cancellation and shell cleanup, retaining terminal task state when the runner's grace period allows it. Hard termination can leave interrupted work that the next worker recovers. Use `!cancelled()` for agent jobs and reserve `always()` for artifact cleanup. See [Troubleshooting](troubleshooting.md) for inspecting partial work.

## Configure the workflow

For the bundled workflows, set these repository values using your normal gh login:

```bash
gh variable set LANDING_MODEL --repo example/team-project --body "openai:gpt-4.1"
gh secret set LANDING_API_KEY --repo example/team-project
```

Optional variables are `LANDING_API_BASE` and `LANDING_COMPLETION_ARGS`. Workflows map them to Landing settings. Prepare Git identity with an existing setup action and Git transport with `gh auth setup-git` when authorizing candidate publication. Grant only the job permissions needed for the delegated work. The bundled jobs use a 120-second model request timeout and a 20-minute job timeout, with no agent step budget.

Keep native checks independent. Main runs tests, typing, quality, documentation, and container recovery before advisory feedback. Default-branch triage handles current native failures; healthy checks need no model task. Release events follow the triggering release, including release tags, and search for matching issues only when relevant failure or recovery evidence exists. There is no daily scan of all open issues. Fork PRs run native CI without model credentials. Repository procedures should tell the agent how to start candidate CI: [GitHub's workflow-token rules](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow) allow explicit workflow dispatch; token-generated PR events can require maintainer approval, and ordinary token-generated pushes do not automatically start workflows.

CI uses a separate SQLite database per job and retains artifacts for 30 days. Lasting lessons belong in issues, project instructions, and meaningful regression cases. See [Develop and dogfood](../development.md).

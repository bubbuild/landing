# Use Landing with GitHub

Prepare a trusted checkout, authenticated gh in PATH, project dependencies, model configuration, and skills. Landing supplies event admission, repository context, and publication verification; the agent uses your prepared tools to investigate and publish. Start with [your first PR review](../get-started.md).

## Choose the publishing identity

Gh credentials determine who publishes. Git's author and committer settings determine commit attribution. Prepare both before delegating a write. The `/landing` prefix and delivery markers identify commands and deliveries, not accounts.

The bundled workflows publish with `github-actions[bot]` by default. For another account, pass its token or prepared gh login and use its actual Git identity. For an organization, use an organization-owned GitHub App or authorized machine account; organizations cannot act as users. The official [App-token Action](https://github.com/actions/create-github-app-token#configure-git-cli-for-an-apps-bot-user) shows token and bot identity setup.

Use existing Git configuration or the standard `GIT_AUTHOR_*` and `GIT_COMMITTER_*` variables. Run `gh auth setup-git` when authorizing pushes. Landing preserves caller-provided identity and does not change authentication. Bundled-workflow overrides are listed in [Configuration](../reference/configuration.md#github-workflows-and-runner).

## Follow repository conventions

Root and scoped `AGENTS.md` files and prepared [skills](../make-it-yours.md#prepare-skills) supply project procedures. Include branch conventions, acceptance checks, and instructions for starting candidate CI. Prepare an isolated checkout for fixes.

When creating an authorized issue or PR, the agent reads the applicable template from [GitHub's standard locations](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/about-issue-and-pull-request-templates). It fills sections with actual evidence and leaves unverified checkboxes unchecked. YAML issue-form labels become Markdown headings; gh does not submit the interactive form. Templates are fetched when needed.

## Invoke locally

The event runner also works outside Actions with your normal gh login:

```bash
uv run python -m landing.adapters.github review --repository example/team-project --number 42 --head candidate-head-sha --instruction "Review the candidate and publish actionable findings." --delivery-key "review-42-request-1" --db ./evidence/landing.sqlite3
```

Replace the target and head with the candidate you intend to review. Use `--help` for event files, check provenance, trust, and skill roots. Ordinary CLI calls can supply repository context without requiring event-runner delivery:

```bash
uv run landing --github-repository example/team-project triage "Investigate the evidence and make the problem actionable." --input report.txt
```

The runner supplies the trigger, target, and relevant revisions. The agent fetches specific discussions, templates, diffs, or logs through gh; complete histories and issue lists are not injected. Use `--instruction` for task scope and relevant new evidence.

## Publish native results

PR review publishes a native [GitHub Review](https://docs.github.com/en/rest/pulls/reviews#create-a-review-for-a-pull-request) for the requested `commit_id`. Findings use verified diff `path`, `line`, and `side`; ranges add `start_line` and `start_side`. A clean review needs no code comments. The default event is `COMMENT`; approvals and change requests require explicit authorization. The gate recommendation remains separate.

Explicit delegations require a reply to their selected issue or PR. Inline follow-ups use the [review-comment replies API](https://docs.github.com/en/rest/pulls/comments#create-a-reply-for-a-review-comment) in the original thread. The reply carries the delivery marker, or the agent records its returned native comment ID with `confirm_reply`. That tool verifies a new reply at the exact delegated PR and thread; completion verifies it again. Include `confirm_reply` when restricting tools for these tasks.

Automatic issuer follow-up publishes only useful new evidence, changed conditions, or verified progress. Unchanged conditions complete quietly through `no_update`, retaining the reason in SQLite. Failed tool execution cannot waive required publication. Explicit questions still require replies.

The same database and delivery key reuse a completed delivery without another model call. A new delegation needs a new key. Missing or mismatched required publication fails the task. The runner exits nonzero for execution or delivery failure, while `block` and `inconclusive` remain advisory; [ordinary CLI review](../reference/cli.md#exit-codes-and-interruption) has stricter exit semantics.

Use GitHub conversation references such as #42 or owner/repo#42, and link commits with short, unambiguous hashes. Other destinations need explicit links. Full identities remain in API calls and verification.

## Wire commands and follow-ups

Listen for `issue_comment` and `pull_request_review_comment` and pass the event file to the Action or `--event`. An admitted caller delegates with the first nonblank line:

```text
/landing triage Track the recurring release failure with acceptance criteria.
/landing fix Fix this issue and preserve its user-visible behavior.
/landing review Review the candidate against independent checks.
/landing explain Explain the failed job and the next useful check.
```

The prefix is configurable and can use your actual bot mention. Every delegation, including a thread follow-up, requires an explicit command. Ordinary status replies and Landing's marked output do not trigger work. Authorized machine accounts can delegate; a bot name or installation ID grants no authority by itself.

Listeners must exist on the default branch to receive events. Landing's [duty workflow](https://github.com/PsiACE/landing/blob/main/.github/workflows/landing-duty.yml) is a project example, not a requirement for using the Action.

## Choose who can delegate

`trust: repository` admits effective repository writers and administrators. `trust: owner` admits the current personal owner by user ID, or an active organization owner with native membership `role: admin`. It does not admit every repository administrator. Native [repository permission](https://docs.github.com/en/rest/collaborators/collaborators#get-repository-permissions-for-a-user) and [organization membership](https://docs.github.com/en/rest/orgs/members#get-organization-membership-for-a-user) APIs are authoritative; Landing keeps no member list or permission cache.

Comments check their author, not the PR author or `author_association`. Repository-policy manual Actions dispatch relies on GitHub's native workflow-write authorization, including authorized Apps; owner policy still checks identity. Other events check the sender; `workflow_run` checks the originating actor. Owner-policy reruns also check the rerunning user. Local invocation without an event uses the executing user's credentials.

Known unauthorized callers skip without a model call or refusal comment. Organization-owner checks first skip callers without repository admin access, then query membership. Unreadable membership, including an ambiguous 404, fails closed; other permission lookup errors also fail the invocation. Owner checks need a credential with repository Metadata/Contents read and organization Members read. Set `GH_ADMISSION_TOKEN` when admission uses a different credential from publication.

Action targets and events must match the workflow repository. `workflow_run` additionally needs an allowed `upstream-workflow` name, matching source repository, and a default-branch push, manual dispatch, or release as its source event. Success alone does not authorize execution. An event file must come from a trusted caller; an external webhook receiver verifies the original signature before translation.

Use protected default-branch or pinned workflow code for policy and setup. Owner-restricted credential jobs also need GitHub environment deployment restrictions, required reviewers, and branch governance such as CODEOWNERS. Repository administrators can edit settings, so owner trust alone does not isolate secrets from them. Admit the caller before preparing candidate code, dependencies, or skills, as shown in [Get started](../get-started.md#add-the-workflow). Authorization to comment on a fork PR does not authorize running its setup with credentials. Job permissions constrain `GITHUB_TOKEN`, not separately supplied PATs or App tokens.

## Finish and cancel work

A fixer validates its repair, publishes the candidate, starts required native CI, and replies with the result and pending-check links. It waits only when the delegation requires CI results before replying, and never waits for its own feedback job. With GitHub's workflow token, ordinary pushes do not start another workflow; use an authorized [explicit dispatch](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow) following repository procedures.

[CI concurrency](ci.md#cancel-superseded-reviews) cancels old candidates at workflow arrival. The runner also rejects stale heads, checks the current head before each model tool batch, and verifies the published review's commit. Stale or unverifiable heads stop new tools; this is not an atomic publication lock, and an in-flight write may finish. Existing reviews remain. Explicit comment work uses a separate queue.

Runner termination requests normal stream cancellation and shell cleanup. Hard termination can leave interrupted work for recovery. Use `!cancelled()` for agent jobs and reserve `always()` for cleanup. See [Troubleshooting](troubleshooting.md) for partial work and [Develop and dogfood](../development.md) for Landing's workflow wiring.

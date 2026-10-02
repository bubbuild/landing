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

Landing reads contribution templates from the selected checkout in [GitHub's standard locations](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/about-issue-and-pull-request-templates): root, `docs`, and `.github`, including single templates, `ISSUE_TEMPLATE` and `PULL_REQUEST_TEMPLATE` directories, and YAML issue forms. The agent chooses the applicable template when creating an issue or PR. YAML form field labels become Markdown headings; gh does not submit the interactive web form. Fill requested sections with actual evidence and leave unverified checkboxes unchecked.

Root and scoped `AGENTS.md` instructions and [skills](../make-it-yours.md#use-skills) supply repository procedures. Put branch conventions, native checks, benchmark criteria, and workflow dispatch instructions there. Prepare an isolated checkout for changes. Landing leaves branch selection, commits, pushes, and candidate publication to the agent within the delegated scope.

## Invoke locally or in CI

The event runner works outside GitHub Actions with your active gh login:

```bash
uv run python -m landing.adapters.github review --repository example/team-project --number 42 --head candidate-head-sha --instruction "Review the candidate and publish actionable findings." --delivery-key "review-42-request-1" --db ./evidence/landing.sqlite3 --check "make acceptance"
```

Use `fix`, `triage`, or `explain` for the corresponding work. The runner also accepts repeatable `--skill-dir PATH`, `--event FILE`, `--run-id ID`, and `--checked-revision SHA`. Candidate head and actual CI checkout revision are separate evidence. Invoke the [reusable Action](ci.md#github-action) in GitHub Actions; no dedicated dogfood script is needed.

A review of a PR publishes a native GitHub Review. The agent uses [the reviews API](https://docs.github.com/en/rest/pulls/reviews#create-a-review-for-a-pull-request) with `commit_id`, a review body, and inline comments containing verified `path`, `line`, and `side` locations. Ranges use `start_line` and `start_side`. Actionable findings belong at their actual diff locations; a clean candidate needs no invented inline comments. The default review event is `COMMENT`; approval and change requests require explicit repository authorization. The gate recommendation is recorded separately.

Other tasks reply to the selected issue or PR with the result and publication links. Inline follow-ups use the [review-comment replies API](https://docs.github.com/en/rest/pulls/comments#create-a-reply-for-a-review-comment) in the original thread. Publication is part of the delegated task. The adapter checks a platform receipt before recording successful completion and rejects reviews attached to a different requested commit. A text answer without the required publication is failed work.

Each delivery carries a marker so the same database and delivery key can reuse an existing published result without invoking the model again. Use a new key for a new delegation. The marker confirms delivery, not the quality of the advice. The runner exits nonzero for execution or delivery failure; a gate recommendation of `block` or `inconclusive` is advisory here. Ordinary CLI review exit semantics are stricter.

## Wire commands and follow-ups

Listen for `issue_comment` and `pull_request_review_comment` events. Pass their event file through the Action or `--event`. A collaborator with repository `admin`, `maintain`, or `write` permission can delegate with the first nonblank line:

```text
/landing triage Track the recurring release failure with acceptance criteria.
/landing fix Fix this issue and preserve its user-visible behavior.
/landing review Review the candidate against independent checks.
/landing explain Explain the failed job and the next useful check.
```

`/landing` is a command prefix, independent of the publishing account. Set `command-prefix` or `--command-prefix` if your team uses another prefix or an actual `@team-bot` identity. Bot comments and Landing's own marked replies do not delegate work. Natural replies in a Landing inline review thread retain its mode; changing the work requires an explicit command. The adapter checks the originating review and collaborator permission before invoking the model.

Comment and scheduled workflows must exist on the default branch to receive events. Landing's `landing-duty.yml` provides the repository example; `landing.yml` and `main.yml` show reusable Action calls and native CI feedback.

## Configure the workflow

For the bundled workflows, set these repository values using your normal gh login:

```bash
gh variable set LANDING_MODEL --repo example/team-project --body "openai:gpt-4.1"
gh secret set LANDING_API_KEY --repo example/team-project
```

Optional variables are `LANDING_API_BASE` and `LANDING_COMPLETION_ARGS`. Workflows map them to Landing settings. Prepare Git identity with an existing setup action and Git transport with `gh auth setup-git` when authorizing candidate publication. Grant only the job permissions needed for the delegated work. The bundled jobs use a 120-second model request timeout and a 20-minute job timeout, with no agent step budget.

Keep native checks independent. Main runs tests, typing, quality, documentation, and container recovery before advisory feedback. Default-branch triage maintains actionable work items. Fork PRs run native CI without model credentials. Repository procedures should tell the agent how to start candidate CI: [GitHub's workflow-token rules](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow) allow explicit workflow dispatch; token-generated PR events can require maintainer approval, and ordinary token-generated pushes do not automatically start workflows.

CI uses a separate SQLite database per job and retains artifacts for 30 days. Lasting lessons belong in issues, project instructions, and meaningful regression cases. See [Develop and dogfood](../development.md).

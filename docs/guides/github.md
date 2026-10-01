# Use Landing with GitHub

Landing's optional GitHub adapter uses `/usr/bin/gh`. It adds repository-scoped tools and an event runner for replies and candidate PRs. The ordinary CLI and HTTP API work without this adapter.

Authenticate with gh's normal login or `GH_TOKEN`, prepare a checkout, and configure the model in the environment where Landing runs. The examples below use `example/team-project`; replace it with your repository.

## Enable repository tools

```bash
uv run landing --github-repository example/team-project issuer "Investigate the evidence, reuse an existing issue or create an actionable one." --input report.txt
```

All modes can read issues, PRs, and runs. Issuer can create, edit, and comment on issues. The tool enforces repository scope and excludes arbitrary API writes, merges, approvals, and credential changes. Enabling this tool alone does not install event handling or automatic PR replies.

## Follow repository conventions

Landing reads contribution templates from the selected checkout in [GitHub's standard locations](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/about-issue-and-pull-request-templates): root, `docs`, and `.github`, including single Markdown or text templates, `ISSUE_TEMPLATE` and `PULL_REQUEST_TEMPLATE` directories, and YAML issue forms. Issuer chooses an applicable issue template and fills its requested sections when publishing through gh. YAML form field labels become Markdown headings; gh does not submit the interactive web form, and Landing does not claim that GitHub validated its required fields. Template labels, assignees, and title guidance remain subject to repository permissions and the delegated task.

Fixer uses the applicable PR template for its final reply. The runner publishes that reply as the candidate's body and appends the issue link and action provenance. Verification and human acceptance checkboxes must reflect what actually happened. Existing issue comments and ordinary explanations do not need to mimic a creation template.

Templates and root `AGENTS.md` come from the isolated target checkout, rather than Landing's installation directory. Keep the contribution conventions in the revision you delegate; GitHub's web template chooser uses the default branch. Landing does not fetch organization-wide community templates automatically. More specific `AGENTS.md` instructions and [repository or user skills](../make-it-yours.md#use-skills) guide work within the task's existing permissions.

## Delegate through the event runner

The runner can be invoked locally with the active gh login:

```bash
uv run python -m landing.adapters.github fixer --repository example/team-project --number 42 --instruction "Fix the issue against its acceptance criteria and retain a meaningful regression check." --delivery-key "issue-42-maintainer-request-1" --db ./evidence/landing.sqlite3 --check "make acceptance"
```

Fixer works in an isolated Git worktree. The runner independently executes the required checks, then commits, pushes, and opens or updates a `landing/fix-<number>` candidate PR. It replies to the original issue or PR. Failed checks preserve edits and return a failure reply without publishing a candidate. The runner never merges; a fixer request on a PR can publish a separate candidate rather than editing that PR's original branch.

Other modes post their result to the selected conversation. The adapter verifies the PR head before delivering revision-sensitive feedback. Its process exit status reports execution or delivery failure; a gatekeeper's `block` or `inconclusive` recommendation is advisory here. Ordinary gatekeeper CLI exit semantics are stricter.

Reuse the same database and delivery key to retry delivery without rerunning the model. Use a new key for a new delegation. Keep worktrees available while inspecting or retrying saved changes.

The runner also accepts repeatable `--skill-dir PATH` options for trusted user or team skills. Repository `.agents/skills` comes from the candidate checkout. Prepare GitHub-hosted skill checkouts with `gh repo clone` and pass their local skill root; the runner uses the same SDK discovery and loading behavior as the ordinary CLI.

## Wire GitHub events

Landing's repository provides working examples in `.github/workflows/landing.yml`, `landing-duty.yml`, and `main.yml`. Adapt checkout, environment setup, checks, permissions, and triggers to your own project. These are repository workflows, not a one-click GitHub App installer.

The duty workflow passes an issue-comment event to the runner with `--event "$GITHUB_EVENT_PATH"`. Commands must appear on their own line in an issue or PR conversation comment from someone with repository `admin`, `maintain`, or `write` permission:

```text
@landing issuer Track the recurring release failure with acceptance criteria.
@landing fixer Fix this issue and preserve its user-visible behavior.
@landing gatekeeper Review the candidate against independent checks.
@landing explainer Explain the failed job and the next discriminating check.
```

Bot comments do not delegate work. Inline PR review comments are a different event and are not handled by this workflow. Comment and scheduled workflows must exist on the default branch before they receive events.

Configure these repository variables and secret for the bundled workflows:

```bash
/usr/bin/gh variable set LANDING_MODEL --repo example/team-project --body "openai:gpt-4.1"
/usr/bin/gh secret set LANDING_API_KEY --repo example/team-project
```

Optional variables are `LANDING_API_BASE` and `LANDING_COMPLETION_ARGS`. The workflows map these to Landing's model environment settings. See [Configuration](../reference/configuration.md).

## Keep native CI independent

The Main workflow runs tests, typing, quality, documentation, and container recovery before advisory Landing feedback. Default-branch feedback uses issuer to maintain CI work items. Missing model configuration skips automatic feedback; an explicitly delegated job fails if its required configuration is absent. Fork PRs run native CI without model credentials.

When the runner publishes with `GITHUB_TOKEN`, ordinary PR workflow triggers are suppressed by GitHub. The bundled runner explicitly dispatches Main when `LANDING_CHECK_WORKFLOW` is set, supplying the candidate PR number and head. The repository must allow Actions to create PRs, and the token needs the permissions for the operations you enable. Adapt the dispatched workflow's `number` and `head` inputs if using this path elsewhere.

The supplied CI job uses a ten-minute action timeout, a 120-second model request timeout, and a 20-minute job timeout. It withholds publishing credentials from the model's shell while retaining them for the explicit gh tool. Tool restrictions are not an operating-system sandbox; run this in trusted workspaces and an appropriate execution environment.

CI stores evidence in a separate database per job and retains artifacts for 30 days. Issues and meaningful regression cases provide the durable feedback beyond those artifacts. See [Develop and dogfood](../development.md).

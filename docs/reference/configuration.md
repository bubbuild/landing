# Configuration

Configure settings where work executes. A remote client needs the server URL and token; the server owns model, tool, and skill configuration. CLI options override corresponding environment settings.

## Model

Set `LANDING_*` variables or save YAML in `~/.landing/config.yml`. `LANDING_CONFIG` selects another file. Empty environment values are ignored; YAML `null` clears an optional setting.

| Variable | Contract |
| --- | --- |
| `LANDING_MODEL` | Provider/model identifier, such as `openai:gpt-4.1`. |
| `LANDING_API_KEY` | Selected provider's API key. |
| `LANDING_API_BASE` | Optional endpoint; unset uses the provider default. |
| `LANDING_MODEL_TIMEOUT_SECONDS` | Model request timeout in seconds. |
| `LANDING_COMPLETION_ARGS` | Provider completion options as a JSON object. |
| `LANDING_CLIENT_ARGS` | Provider client options as a JSON object, such as `{"api_format": "chat"}`. |
| `LANDING_MAX_TOKENS` | Maximum generated tokens, default 16384. |
| `LANDING_FALLBACK_MODELS` | Fallback identifiers as a JSON list. |
| `LANDING_CONFIG` | YAML settings path. |

```bash
export LANDING_MODEL="openai:gpt-4.1"
export LANDING_API_KEY="your-provider-api-key"
```

YAML uses lowercase names without the prefix:

```yaml
model: openai:gpt-4.1
api_key: your-provider-api-key
completion_args:
  temperature: 0.2
```

Precedence is environment, Landing YAML, then defaults. Bub loads Landing's YAML and resolves registered component settings. Configure provider protocols and clients through Republic options in `client_args`; for a Chat Completions endpoint, use `api_format: chat`. Model clients use Bub's native provider credential resolution when explicit credentials are absent.

## Skills

| Setting | Contract |
| --- | --- |
| YAML `skill_dirs` | Additional trusted roots as a list of paths. |
| `LANDING_SKILL_DIRS` | The same roots as a JSON list. |
| Global `--skill-dir PATH` | Repeatable per-call or server roots. |

Discovery precedence is workspace `.agents/skills`, explicit roots, configured roots, `~/.agents/skills`, then bundled skills. Explicit roots add to configured roots; duplicate skill names select the earlier root. Prepare roots on the executing host; remote clients cannot supply local directories. See [Prepare skills](../make-it-yours.md#prepare-skills).

## Mode capabilities

YAML `modes` or JSON `LANDING_MODES` configures additional instructions and allowed or excluded tools and skills independently for each mode:

```yaml
modes:
  issuer:
    allowed_tools: [spill.read, fs.read, bash, skill, no_update]
    allowed_skills: [landing-issuer, issue-triage]
  fixer:
    allowed_tools: [spill.read, fs.read, fs.write, fs.edit, bash, skill]
    allowed_skills: [landing-fixer, friendly-python, piglet]
  gatekeeper:
    instructions: |
      Review supported user-visible behavior.
      Use $landing-review for project-specific standards.
    allowed_skills: [landing-gatekeeper, landing-review]
    excluded_tools: [fs.write, fs.edit]
  explainer:
    allowed_tools: [spill.read, fs.read, skill]
    allowed_skills: [landing-explainer, documentation-writer]
```

`instructions` adds guidance without replacing the selected method or granting capabilities. Keep project conventions in `AGENTS.md`, reusable methods in skills, and evidence in task inputs. These additions stay literal rather than being evaluated as templates.

Same-named skill overrides resolve before filtering. Capabilities follow this rule:

```text
Available capabilities ∩ Mode allow list − Mode exclusions
```

Omitted or `null` allow lists are unrestricted; `[]` disables the collection. Exclusions default to empty lists and win over allow lists. Tool lists accept native names or aliases such as `fs.read` and `fs_read`; skill names are case-insensitive.

Each mode automatically uses its permitted `landing-{mode}` skill. Include that name in allow lists to retain its method. Excluding it disables the method while preserving the mode, checks, and publication requirements. A same-named skill in a higher-priority root replaces it. Allowing a skill grants no tools.

Include `skill` to load supplementary skills, `spill.read` for oversized output, `decide` for review recommendations, and `no_update` for quiet issuer completion. GitHub publication normally requires `bash` and authenticated gh; inline replies also require `confirm_reply`.

Filters control method loading and model tool calls across entry points. They do not grant publishing authority, isolate the filesystem, disable required checks, or erase history. Excluded skills are omitted from the task's skill listing. Enforce permissions and isolation in tools and the execution environment.

## MCP servers

`LANDING_MCP_CONFIG` or YAML `mcp_config` selects a trusted `mcp.json` file; relative paths resolve from the task workspace. An explicit missing file fails the task. Without an explicit path, Landing selects the first existing workspace `.agents/mcp.json`, user `~/.agents/mcp.json`, or native bub-mcp configuration path (normally `~/.bub/mcp.json`). Files replace one another rather than merging; an empty `mcpServers` mapping disables servers. Missing default files leave MCP disabled.

```json
{
  "mcpServers": {
    "playwright": {
      "command": "npx",
      "args": ["--yes", "@playwright/mcp@0.0.83", "--headless", "--isolated"]
    }
  }
}
```

Use the `mcpServers` JSON format shown above. Native YAML `mcp.config_path` configures the final fallback, `mcp.init_timeout_seconds` controls connection startup, and `mcp.deferred_tools: false` offers server tools directly instead of loading them on demand. Server commands run in their prepared environment; use absolute command, argument, and output paths when working across repositories.

Discovered tools use names such as `mcp.playwright_browser_navigate` and load on demand: the task prompt lists the names its mode allows, the model calls `tool_describe` with one, and the tool stays callable by name for the rest of the session. The SDK supplies `tool_describe` alongside a deferred list, so mode tool limits apply before loading and excluded tools never reach the model. A configured server that cannot connect fails the task before model work; connection details remain in runner logs. See [Use MCP servers](../guides/mcp.md) for setup and an example.

## Execution and webhook hosting

`LANDING_DB` selects local or runner SQLite storage, defaulting to `~/.local/share/landing/landing.sqlite3` for the CLI and `RUNNER_TEMP/landing/landing.sqlite3` for Actions. `--db` overrides it. Local execution YAML uses `db` and `github_repository`; model and MCP settings apply to execution, including local worker children, not to GHA-only intake.

Operator policy in the same YAML is shared by the CLI, the Actions executor, the local worker, and the receiver. Environment values win, then YAML, then defaults, and a command-line flag wins over both. Reading that file creates no agent, database, or workspace, so a `gha` receiver stays a relay. The file may set `runner`, `workspace`, `workflow`, `ref`, `command_prefix`, `trust`, `upstream_workflow`, `bump_on_failure`, `dependency_update_actors`, and `check`. That allow-list covers integration policy only. Task inputs (`command`, `instruction`, `number`, `head`, `delivery_key`), the webhook secret, and native `GITHUB_*` identity and event context keep their own sources, so untrusted or file-based values cannot grant caller authority. Model configuration is unchanged: `model`, `api_key`, and related fields remain ordinary settings, and the [model](#model) contract above still applies.

| Server variable | Contract |
| --- | --- |
| `LANDING_RUNNER` | `local` (default) or `gha`; overridden by `serve --runner`. |
| `LANDING_WORKSPACE` | Prepared local checkout, default current directory; overridden by `serve --workspace`. |
| `LANDING_DB` | Local inbox, action records, and default tape storage; global `--db` overrides it. |

| Webhook variable | Contract |
| --- | --- |
| `LANDING_GITHUB_WEBHOOK_SECRET` | Required GitHub webhook HMAC key; also configured on the trusted executor. |
| `LANDING_GITHUB_WORKFLOW` | Workflow filename or ID, default `landing-execute.yml`; validated by GitHub. |
| `LANDING_GITHUB_REF` | Optional trusted branch or tag; otherwise uses the event repository's default branch. |
| `LANDING_GITHUB_COMMAND_PREFIX` | Comment prefix, default `/landing`; keep receiver and runner configuration consistent. |

The verified event supplies the repository. There is no repository registry, bearer-token job API, or replication supervisor. Local mode uses the prepared checkout, checks its repository identity, and requires model and publication access. GHA-only intake needs Actions write access but no local model or database.

## Bub plugins

`LANDING_PLUGINS` is a JSON list of installed Bub entry-point names; the equivalent YAML field is `plugins`. Landing explicitly registers the selected plugins with Bub's native plugin manager. Nothing is installed automatically.

Execution hooks such as `provide_environment`, `provide_tape_store`, `provide_lifespan`, `provide_tools`, state loading, continuation, and tool/LLM interception apply to the same Runtime in local CLI, local worker, and Actions execution. Tools provided by plugins still pass through mode capability filtering. Channel and message-pipeline plugins are not automatically turned into Landing transports.

For example, an installed [bub-docker-env](https://github.com/bubbuild/bub-contrib/tree/main/packages/bub-docker-env) can supply `provide_environment` for tools and checks. Prepare its image, network, mounts, and credentials explicitly; its defaults are not a ready-made GitHub development environment. The model and platform verification remain on the host.

The [SQLite](https://github.com/bubbuild/bub-contrib/tree/main/packages/bub-tapestore-sqlite) and [SQLAlchemy](https://github.com/bubbuild/bub-contrib/tree/main/packages/bub-tapestore-sqlalchemy) tape plugins supply model-history storage, not a task queue or GHA scheduler. Their schemas differ from Landing's released database; use separately configured storage unless performing an intentional migration. Existing Landing tapes are not migrated or deleted automatically.

Install plugins into the interpreter that runs Landing. A plugin in a separate project virtualenv is not visible to the composite Action's isolated runtime. For customized Actions environments, install Landing and plugins together and invoke `landing github event` from that environment. Local workers call the same API from the installed runtime, without routing through the CLI.

## GitHub workflows and runner

The runner uses prepared `GH_TOKEN` or gh login for publication and optional `GH_ADMISSION_TOKEN` for separate admission. The Action exposes only `command`, `instruction`, and `checks`; its implementation reads these as `INPUT_COMMAND`, `INPUT_INSTRUCTION`, and `INPUT_CHECKS`. Runner policy uses `LANDING_GITHUB_TRUST` (`repository` or `owner`), multiline `LANDING_GITHUB_UPSTREAM_WORKFLOW`, `LANDING_GITHUB_BUMP_ON_FAILURE` (default false), and multiline `LANDING_GITHUB_DEPENDENCY_UPDATE_ACTORS` (default Dependabot and Renovate). There are no duplicate Action inputs or CLI flags for these settings. Native `GITHUB_*` identity and event context come from the execution environment; YAML cannot grant caller authority.

For native events, `LANDING_GITHUB_NUMBER`, `LANDING_GITHUB_HEAD`, `LANDING_GITHUB_RUN_ID`, and `LANDING_GITHUB_DELIVERY_KEY` supply explicit task references when needed. `LANDING_GITHUB_CHECKED_REVISION` defaults to `GITHUB_SHA`; an explicitly empty value leaves check provenance unknown. Forwarded webhooks use the original event for task references and current runner configuration for policy; no receiver configuration snapshot is accepted. Runner storage uses global `--db`, then `LANDING_DB`, then `RUNNER_TEMP/landing/landing.sqlite3`.

Commit attribution uses Git configuration or `GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL`, `GIT_COMMITTER_NAME`, and `GIT_COMMITTER_EMAIL`. Selecting a publication token alone does not change commit attribution. See [GitHub integration](../guides/github.md) for identity and trust, and [Action inputs](cli.md#github-action) for invocation.

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
| `LANDING_CLIENT_ARGS` | Provider client options as a JSON object, such as `{"max_retries": 0}`. |
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

Precedence is environment, Landing YAML, existing Bub YAML, legacy provider-specific environment, then defaults. Corresponding `BUB_*` aliases remain supported; `LANDING_*` wins when both are present. Missing YAML values fall back to `~/.bub/config.yml`. Provider-specific aliases such as `BUB_OPENAI_API_KEY` and `BUB_OPENAI_API_BASE` are lower-priority fallbacks.

## Skills

| Setting | Contract |
| --- | --- |
| YAML `skill_dirs` | Additional trusted roots as a list of paths. |
| `LANDING_SKILL_DIRS` | The same roots as a JSON list. |
| Global `--skill-dir PATH` | Repeatable per-call or server roots. |

Discovery precedence is workspace `.agents/skills`, explicit roots, configured roots, then `~/.agents/skills`. Explicit roots add to configured roots; duplicate skill names select the earlier root. Existing `BUB_SKILL_DIRS` is a fallback alias. Prepare roots on the executing host; remote clients cannot supply local directories. See [Prepare skills](../make-it-yours.md#prepare-skills).

## Mode capabilities

YAML `modes` or JSON `LANDING_MODES` selects tools and skills independently for each mode:

```yaml
modes:
  issuer:
    allowed_tools: [spill.read, fs.read, bash, skill, no_update]
    allowed_skills: [issue-triage]
  fixer:
    allowed_tools: [spill.read, fs.read, fs.write, fs.edit, bash, skill]
    allowed_skills: [friendly-python, piglet]
  gatekeeper:
    allowed_tools: [spill.read, fs.read, bash, skill, decide]
    allowed_skills: [landing-review]
  explainer:
    allowed_tools: [spill.read, fs.read, skill]
    allowed_skills: [documentation-writer]
```

Omitted or `null` lists are unrestricted; `[]` disables the collection. Tools accept native names or aliases such as `fs.read` and `fs_read`; skill names are case-insensitive and must exist in prepared roots. Include `skill` for tool-loaded skills, `spill.read` for oversized output, `decide` for gate recommendations, and `no_update` for quiet issuer completion. GitHub publication normally needs `bash` and authenticated gh; inline reply confirmation also needs `confirm_reply`.

Per-call SDK selections intersect with mode limits and cannot expand them. Settings apply to CLI, HTTP, SDK, hooks, and CI. Comma commands select modes outside the model loop without adding capabilities. Tool filtering is not a sandbox: shell or delegation tools may expose broader authority.

## MCP servers

`LANDING_MCP_CONFIG` or YAML `mcp_config` selects a trusted `mcp.json` file; relative paths resolve from the task workspace. `BUB_MCP_CONFIG_PATH` is a fallback environment alias. An explicit missing file fails the task. Without an explicit path, Landing selects the first existing workspace `.agents/mcp.json`, user `~/.agents/mcp.json`, or native bub-mcp configuration path (normally `~/.bub/mcp.json`). Files replace one another rather than merging; an empty `mcpServers` mapping disables servers. Missing default files leave MCP disabled.

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

The JSON format and transports come from bub-mcp and FastMCP. `.agents/mcp.json` is Landing's file convention; it does not load Agent Plugins. Native YAML `mcp.config_path` configures the final fallback and `mcp.init_timeout_seconds` controls connection startup. Server commands run in their prepared environment; use absolute command, argument, and output paths when working across repositories.

Discovered tools use names such as `mcp.playwright_browser_navigate`. Existing `allowed_tools` and per-call limits apply after discovery. A configured server that cannot connect fails the task before model work; connection details remain in runner logs. See [Use MCP servers](../guides/mcp.md) for setup and an example.

## Execution and service

| Variable | Contract |
| --- | --- |
| `LANDING_DB` | SQLite path, default `~/.local/share/landing/landing.sqlite3`; image default `/storage/landing.sqlite3`. |
| `LANDING_SERVER` | Remote CLI service URL. |
| `LANDING_TOKEN` | Server and client bearer token; required to listen beyond localhost. |
| `LANDING_GITHUB_REPOSITORY` | Prepared gh repository context. |
| `LANDING_BASE_URL` | Public HTTP(S) origin for pagination; no credentials, path, query, or fragment. `BASE_URL` remains an alias. |

CLI and service settings read their own fields from environment, Landing YAML and existing Bub YAML. YAML keys are `db`, `server`, `token`, `github_repository` and `base_url`. CLI options override corresponding settings. SDK calls do not validate CLI settings; remote clients do not validate model or MCP settings. MCP settings load when a task prepares its tools, and configuration errors remain visible in its action record.

Local workspace defaults to the current directory. Servers register names with `serve --workspace NAME=PATH`.

## GitHub workflows and runner

These settings apply to Landing's bundled workflows; a caller's own workflow prepares its environment explicitly.

| Setting | Contract |
| --- | --- |
| Variable `LANDING_MODEL` | Model identifier. |
| Variables `LANDING_API_BASE`, `LANDING_COMPLETION_ARGS` | Optional endpoint and completion options. |
| Secret `LANDING_API_KEY` | Provider key. |
| Variable `LANDING_TRUST` | Caller policy for the project workflows; defaults to `repository`. |
| Secret `LANDING_ADMISSION_TOKEN` | Optional read-only admission credential; organization owner checks need Members read. |
| Secret `LANDING_GITHUB_TOKEN` | Publication token; defaults to the workflow token. |
| Variables `LANDING_GIT_NAME`, `LANDING_GIT_EMAIL` | Optional commit identity; set both. Default is the standard Actions bot. |

The runner uses prepared `GH_TOKEN` or gh login for publication and optional `GH_ADMISSION_TOKEN` for separate admission. Action inputs load from `INPUT_*`; explicit CLI options take precedence. Checks and upstream workflow names use multiline inputs. Native `GITHUB_*` identity and event context come from the execution environment; YAML cannot grant caller authority. An empty `checked-revision` input leaves check provenance unknown.

Commit attribution uses Git configuration or `GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL`, `GIT_COMMITTER_NAME`, and `GIT_COMMITTER_EMAIL`. Selecting a publication token alone does not change commit attribution. See [GitHub integration](../guides/github.md) for identity and trust, and [Action inputs](cli.md#github-action) for invocation.

## Container replication

| Variable | Contract |
| --- | --- |
| `LITESTREAM_REPLICA_URL` | Replica destination; Compose defaults to `file:///replica/landing`. |
| `LITESTREAM_CONFIG` | Optional mounted standard configuration path. |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `AWS_REGION` | Credentials and region for the corresponding storage provider. |

Compose forwards only variables listed in `compose.yaml`, including model, key, endpoint, completion/client options, and model timeout. Add other settings or mounts through an override; exporting them on the host alone does not forward them. See [Deployment](../guides/deploy.md) and [Recovery](../guides/recovery.md).

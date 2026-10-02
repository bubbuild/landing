# Configuration

Configure Landing in the environment where its worker runs. Remote clients need the server URL and bearer token; model settings belong on the server. CLI options override their corresponding environment settings.

## Model

Set `LANDING_*` variables directly or save model settings in `~/.landing/config.yml`. No separate application or plugin setup is required. Set `LANDING_CONFIG` to use another YAML file. Empty environment values are ignored; use `null` to explicitly clear an optional setting.

| Variable | Purpose |
| --- | --- |
| `LANDING_MODEL` | Provider and model identifier, such as `openai:gpt-4.1`. |
| `LANDING_API_KEY` | API key for the selected provider. |
| `LANDING_API_BASE` | Optional custom provider endpoint. Leave unset to use the provider default. |
| `LANDING_MODEL_TIMEOUT_SECONDS` | Model request timeout; the bundled CI and Compose configuration use 120 seconds. |
| `LANDING_COMPLETION_ARGS` | Provider completion options as a JSON object. |
| `LANDING_CLIENT_ARGS` | Provider client options as a JSON object, such as `{"max_retries": 0}`. |
| `LANDING_MAX_TOKENS` | Maximum generated tokens, default 16384. |
| `LANDING_FALLBACK_MODELS` | Optional fallback model identifiers as a JSON list. |
| `LANDING_CONFIG` | Model settings file, default `~/.landing/config.yml`. |

```bash
export LANDING_MODEL="deepseek:deepseek-v4-pro"
export LANDING_API_BASE="https://api.deepseek.com"
export LANDING_API_KEY="your-provider-api-key"
export LANDING_COMPLETION_ARGS='{"reasoning_effort":"none"}'
```

The YAML file uses the same setting names without the prefix, in lowercase:

```yaml
model: openai:gpt-4.1
api_key: your-provider-api-key
completion_args:
  temperature: 0.2
```

Environment settings take precedence over YAML. For existing installations, corresponding `BUB_*` variables remain supported as aliases: `LANDING_*` wins when both names are present. Missing YAML values fall back to `~/.bub/config.yml`. The order is ordinary environment settings, Landing YAML, existing Bub YAML, legacy provider-specific environment settings, then defaults. Provider-specific compatibility variables such as `BUB_OPENAI_API_KEY` and `BUB_DEEPSEEK_API_BASE` supply missing credentials or endpoints after both YAML files; they do not override values in those files. Compatibility loads configuration only; it does not load plugins. Remove a Landing override to use the existing value again. API keys and endpoints can also be provider maps in YAML or JSON, such as `{"openai": "your-provider-api-key", "deepseek": "your-other-key"}`; use the same `LANDING_API_KEY` and `LANDING_API_BASE` settings.

Choose a model identifier and options supported by your provider. Landing sets no agent step budget. Request timeouts, required-check timeouts, cancellation, and CI job timeouts still apply.

## Skills

`LANDING_SKILL_DIRS` accepts a JSON list of trusted local skill roots. The same setting is `skill_dirs` in the YAML configuration and follows the configuration precedence above. Existing `BUB_SKILL_DIRS` values remain supported as an alias.

```bash
export LANDING_SKILL_DIRS='["/srv/team-skills/.agents/skills"]'
```

```yaml
skill_dirs:
  - /srv/team-skills/.agents/skills
```

Skill discovery searches the selected workspace's `.agents/skills` first, repeatable `--skill-dir` roots next, configured roots next, and `~/.agents/skills` last. The first matching skill name wins. The Python `Runtime` and `create_app` APIs accept explicit `skill_dirs` with the same precedence as CLI roots. Configure these directories on the executing host; remote clients cannot add directories to a server. See [Use skills](../make-it-yours.md#use-skills) for the file layout and GitHub-hosted skill checkouts.

## Mode capabilities

Each of the four modes independently selects the native tools and skills available to its agent loop. Configure `modes` in YAML or set `LANDING_MODES` to the equivalent JSON object. There are no additional roles.

```yaml
modes:
  issuer:
    allowed_tools: [spill.read, fs.read, bash, skill]
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

An omitted list or `null` leaves that collection unrestricted; `[]` disables it. Tools use native SDK names or aliases, such as `fs.read` or `fs_read`; skill names are case-insensitive. The example names must exist in your prepared skill roots. Include `skill` to load a skill through a tool, `decide` to record a gate recommendation, and `spill.read` to retrieve oversized tool output stored by the native SDK. GitHub publication needs an appropriate prepared capability, normally `bash` and authenticated `gh`.

The Python SDK's per-call `allowed_tools` and `allowed_skills` intersect with the selected mode's lists. A call can narrow the available collection but cannot expand the mode's configuration. Settings take effect equally for CLI, HTTP, SDK, hooks, and CI execution. Native comma commands select a mode outside the model loop; they do not grant the ensuing task more capabilities. Tool selection is not a sandbox: a shell or delegation tool can expose broader capabilities, so prepare the execution environment for the authority you intend to delegate.

## Execution and service

| Variable | Purpose or default |
| --- | --- |
| `LANDING_DB` | Local database, default `~/.local/share/landing/landing.sqlite3`; image default `/storage/landing.sqlite3`. |
| `LANDING_SERVER` | Remote service URL for the CLI. |
| `LANDING_TOKEN` | Server bearer token and remote client token; required to bind beyond localhost. |
| `LANDING_GITHUB_REPOSITORY` | Optional `OWNER/REPO` context for the prepared gh CLI. |
| `BASE_URL` | Public HTTP(S) origin for service pagination links. No credentials, path, query, or fragment. |

The default workspace is the current directory locally. A service registers workspace names with `serve --workspace NAME=PATH`.

## GitHub workflows and runner

| Setting | Purpose |
| --- | --- |
| Repository variable `LANDING_MODEL` | Model identifier used directly in the bundled workflows. |
| Repository variable `LANDING_API_BASE` | Optional provider endpoint. |
| Repository variable `LANDING_COMPLETION_ARGS` | Completion options as a JSON object, default `{}`. |
| Repository secret `LANDING_API_KEY` | Provider API key. |
| `GH_TOKEN` or gh login | Platform authorization, independent of model configuration. |

See [GitHub](../guides/github.md) for event wiring and permissions, and [CI](../guides/ci.md) for the reusable Action. Project checks, authentication, and skills are prepared by the caller.

## Container replication

| Variable | Purpose |
| --- | --- |
| `LITESTREAM_REPLICA_URL` | Replica destination; Compose defaults to `file:///replica/landing`. |
| `LITESTREAM_CONFIG` | Optional mounted Litestream configuration path. |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `AWS_REGION` | Storage credentials and region when using the corresponding replica provider. |

Compose passes only the variables listed in `compose.yaml`. Model, API key, endpoint, completion options, client options, and model timeout are included. To use another setting or mount a configuration file, add it through a Compose override. Exporting it on the host alone does not forward it into the container. See [Deploy Landing](../guides/deploy.md) and [Replication and recovery](../guides/recovery.md).

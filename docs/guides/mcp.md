# Use prepared MCP servers

Landing includes [bub-mcp](https://github.com/bubbuild/bub-contrib/tree/main/packages/bub-mcp) and connects configured servers through the existing SDK agent. Prepare server commands, credentials, and browser binaries in the environment where work executes. CLI, server, Action, and embedded SDK calls use the same configuration and mode limits.

## Review a local preview with Playwright

The repository includes an [official Playwright MCP](https://github.com/microsoft/playwright-mcp) configuration and a [Python SDK example](https://github.com/bubbuild/landing/tree/main/examples/playwright). Prepare Node.js 18 or later and Chrome. Prewarm the pinned server:

```bash
npx --yes @playwright/mcp@0.0.83 --help
```

Use Landing from the repository to run this integration:

```bash
uv tool install git+https://github.com/bubbuild/landing
```

Copy `examples/playwright/mcp.json` to your workspace's `.agents/mcp.json`. To reuse a different installed Chromium binary, add `--executable-path` and its absolute path to the server's `args`. The example uses an isolated headless browser. Start your application's preview, then delegate a review:

```bash
landing review "Check http://127.0.0.1:8000. Reproduce navigation or API reference problems and report concise evidence."
```

From a Landing checkout, the SDK example reviews the same preview without copying configuration:

```bash
LANDING_MCP_CONFIG=examples/playwright/mcp.json uv run python examples/playwright/review.py
```

If a mode restricts tools, include the required native names, such as `mcp.playwright_browser_navigate`, `mcp.playwright_browser_snapshot`, and `mcp.playwright_browser_take_screenshot`. SDK callers can narrow these permissions further. See [MCP configuration](../reference/configuration.md#mcp-servers).

## Prepare CI and service environments

The Action inherits prepared settings; it does not install MCP servers or browsers. Landing's [dogfood workflow](https://github.com/bubbuild/landing/blob/main/.github/workflows/landing.yml) prepares Playwright only when requested, reuses the runner's Chrome, discovers tools to extend the existing mode configuration, and serves documentation built from the selected candidate. Main selects browser preparation for documentation pages, site configuration, or API contract changes. A manual run can set `browser=true`.

For a service, mount configuration and prepare executables on the server. Each task selects its registered workspace's configuration. Connections close on completion or cancellation; switching workspaces does not retain the previous task's MCP tools. Configuration can execute local commands, so only run trusted configuration inside the admitted execution environment.

Text and structured tool results reach the model through bub-mcp. In bub-mcp 0.3.0, binary images become text placeholders; saved screenshots are human evidence. Landing's project review skill owns screenshot publication guidance, while the workflow retains browser files with its other artifacts.

# Use prepared MCP servers

Landing includes [bub-mcp](https://github.com/bubbuild/bub-contrib/tree/main/packages/bub-mcp) and connects configured servers through the existing SDK agent. Prepare server commands, credentials, and browser binaries where work executes. CLI, server, Action, and embedded SDK calls share configuration and mode limits.

## Review a local preview with Playwright

Prepare Node.js 18 or later and Chrome, then prewarm the [official Playwright MCP server](https://github.com/microsoft/playwright-mcp):

```bash
npx --yes @playwright/mcp@0.0.83 --help
```

Install Landing from the repository:

```bash
uv tool install git+https://github.com/bubbuild/landing
```

Add the [Playwright configuration](../reference/configuration.md#mcp-servers) to your workspace's `.agents/mcp.json`. To use another installed Chromium binary, add `--executable-path` and its absolute path to the server's `args`. Start your application's preview, then delegate a review:

```bash
landing review "Check http://127.0.0.1:8000. Reproduce navigation or API reference problems and report concise evidence."
```

If a mode restricts tools, list the required native names, such as `mcp.playwright_browser_navigate`, `mcp.playwright_browser_snapshot`, and `mcp.playwright_browser_take_screenshot`. SDK callers can narrow these permissions further.

## Prepare CI and service environments

The Action inherits prepared settings; it does not install MCP servers or browsers. Landing's [dogfood workflow](https://github.com/bubbuild/landing/blob/main/.github/workflows/landing.yml) prewarms Playwright only when requested, reuses the runner's Chrome, and serves documentation built from the selected candidate. Main selects browser preparation for documentation pages, site configuration, or API contract changes. A manual run can set `browser=true`.

Browser tasks select `.agents/mcp.json` explicitly and use `.github/landing-browser.yml`, which grants Playwright tools to gatekeeper. Other modes retain their ordinary capabilities. The workflow prepares the environment; the task runtime connects and discovers tools. Tasks without browser preparation select `.agents/mcp-disabled.json` and `.github/landing.yml`. Browser preparation failure uses the same fallback without claiming browser verification. Browser files join the Landing artifact after feedback.

For a service, mount configuration and prepare executables on the server. Each task selects its registered workspace's configuration. Connections close on completion or cancellation; switching workspaces does not retain the previous task's MCP tools. Only run trusted configuration inside the admitted execution environment.

Text and structured tool results reach the model through bub-mcp. In bub-mcp 0.3.0, binary images become text placeholders; saved screenshots are human evidence. Landing's project review skill owns screenshot publication guidance.

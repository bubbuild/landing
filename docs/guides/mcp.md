# Use MCP servers

Add trusted server definitions to `.agents/mcp.json` in your workspace. Prepare the server commands, credentials, and any required binaries in the environment where Landing runs. See [MCP configuration](../reference/configuration.md#mcp-servers) for the JSON format, configuration precedence, and tool permissions.

The repository includes a [Playwright MCP example](https://github.com/bubbuild/landing/blob/main/.agents/mcp.json). Prepare Node.js and Chrome, then adapt its arguments to your environment. Install Landing:

```bash
uv tool install landing
```

Delegate work through the usual command:

```bash
landing review "Check http://127.0.0.1:8000. Reproduce navigation problems and report concise evidence."
```

CLI, server, and Action calls use the same MCP configuration. For a service or CI job, make the configuration and executables available in its execution environment. A configured server's tools do not appear in the model's tool list directly; the task prompt lists them and the model loads the ones it needs before calling them, so a review that depends on a browser tool spends a step on discovery.

---
title: Landing
---

<h1 id="landing">
  <img src="assets/images/landing-wordmark-light.png#only-light" alt="Landing" width="420">
  <img src="assets/images/landing-wordmark-dark.png#only-dark" alt="Landing" width="420">
</h1>

Landing delegates engineering work to a language model using the tools, checks, and identity you prepared: explain a failure, make a problem actionable, repair a demonstrated defect, review a candidate, or update a dependency.

You choose the model endpoint, execution environment, project instructions, and acceptance checks. Landing admits the work, runs the selected mode, verifies publication, and records the result in SQLite. The documented paths need no hosted Landing account.

## Start

- [Your first PR review](get-started.md) adds the Action to a GitHub workflow and verifies that it publishes feedback.
- [Use Landing locally](guides/local.md) runs a task from the CLI against your own checkout.

Both use the same modes, inputs, checks, and action records. Use the CLI in another CI system, or [run the server](guides/server.md) when several callers share one prepared environment.

## Find the right page

| Goal | Page |
| --- | --- |
| Choose the model, skills, tools, and runner policy | [Configuration](reference/configuration.md) |
| Look up a command, exit code, or Action input | [CLI reference](reference/cli.md) |
| Submit work over HTTP | [HTTP reference](reference/http.md) |
| Wire repository events, comments, and trust | [GitHub integration](guides/github.md) |
| Prepare project guidance and skills | [Make Landing work for you](make-it-yours.md) |
| Understand how context and verification make results reliable | [Working with Landing](working-with-landing.md) |
| Diagnose a failure | [Troubleshooting](guides/troubleshooting.md) |

[Develop and dogfood](development.md) describes how Landing itself is built and tested.

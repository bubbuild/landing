<h1>
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/images/landing-wordmark-dark.png">
    <img src="https://raw.githubusercontent.com/bubbuild/landing/2eb4c40ff34b78ff909f12639a63f1b851a704cd/docs/assets/images/landing-wordmark-light.png" alt="Landing" width="420">
  </picture>
</h1>

Landing delegates engineering work to a language model using the tools, checks, and identity you already prepared. It explains a failure, turns a problem into an actionable issue, repairs a demonstrated defect, reviews a candidate, or updates a dependency.

You bring the model endpoint, the execution environment, and the acceptance checks. Landing admits the work, runs the selected mode with your project guidance, verifies publication, and records the result in SQLite. It is an Apache-2.0 application; the documented paths need no hosted Landing account.

## Quick start

With Python 3.12 or later, [uv](https://docs.astral.sh/uv/), and a POSIX host:

```bash
uv tool install landing
export LANDING_MODEL="openai:gpt-4.1"
export LANDING_API_KEY="your-provider-api-key"
landing explain "Explain this failure and suggest the next useful check." --input check.log
```

Run it from your project checkout and use a saved UTF-8 log for `check.log`. The same install serves `triage`, `fix`, `review`, and `bump`; each command works independently. See [local use](https://getlanding.dev/guides/local/) for delegating work and reading the result.

To review pull requests instead, [add the Action to a GitHub workflow](https://getlanding.dev/get-started/): set the model, let your native checks run, and read the advisory review it publishes.

```yaml
- uses: bubbuild/landing@main
  continue-on-error: true
  env:
    GH_TOKEN: ${{ github.token }}
    LANDING_MODEL: ${{ vars.LANDING_MODEL }}
    LANDING_API_KEY: ${{ secrets.LANDING_API_KEY }}
  with:
    command: review
    instruction: Review this candidate against repository guidance and the native checks. Publish actionable findings.
```

The example follows `main`; pin a release tag or reviewed commit to keep the runtime fixed. `continue-on-error` keeps automatic feedback advisory, and the Action checks who may delegate before starting any work.

## Entry points

| Entry point | Use it when |
| --- | --- |
| CLI | You work against a local checkout, or your CI is not GitHub Actions. |
| [GitHub Action and events](https://getlanding.dev/guides/github/) | Work is triggered by repository events, comments, or schedules. |
| [HTTP service](https://getlanding.dev/guides/server/) | Several callers share one queue and prepared environment. |

All three accept the same modes, instructions, inputs, and checks, and record the same action lifecycle. [CLI reference](https://getlanding.dev/reference/cli/) and [HTTP reference](https://getlanding.dev/reference/http/) list the exact contracts. The service also ships as an image: [deploy](https://getlanding.dev/guides/deploy/) covers Compose and `ghcr.io/bubbuild/landing`.

## Configure and adapt

[Configuration](https://getlanding.dev/reference/configuration/) covers the model, skills, mode capabilities, MCP servers, and runner policy, including precedence and the values a settings file may set. [Make Landing work for you](https://getlanding.dev/make-it-yours/) covers project instructions, skills, tools, and required checks.

Keep in mind:

- Instructions and capability lists guide the agent; they do not sandbox it or grant authority. Enforce both in your tools and workflow permissions.
- Checks decide what a task may claim. Keep your required native checks independent of Landing's advice.
- Task completion, a published reply, and a passing check establish different things. Your team still accepts the work.

## Limits

Landing runs where you install it and uses the credentials you give it. It does not supply a model, isolate an untrusted checkout, or configure your CI. A fork pull request normally runs native checks while receiving no model or publication credentials.

## Learn more

[Working with Landing](https://getlanding.dev/working-with-landing/) explains how engineering context and verification make delegated work reliable. [CI integration](https://getlanding.dev/guides/ci/) covers scope, concurrency, and ordinary CI commands; [troubleshooting](https://getlanding.dev/guides/troubleshooting/) starts from an observed failure. [Develop and dogfood](https://getlanding.dev/development/) describes how Landing itself is built and tested.

Contributions are welcome; see [CONTRIBUTING.md](https://github.com/bubbuild/landing/blob/main/CONTRIBUTING.md). Licensed under [Apache-2.0](https://github.com/bubbuild/landing/blob/main/LICENSE). Built on [Bub](https://bub.build/).

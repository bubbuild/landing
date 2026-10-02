# Contributing to Landing

Questions, bug reports, documentation, and code contributions are welcome. You can report a problem without volunteering to implement the fix. Use [GitHub issues](https://github.com/bubbuild/landing/issues) to discuss the behavior you need.

## Report a problem

Describe what you expected, what happened, and the smallest way to reproduce it. Include the relevant environment, revision, and useful logs with credentials removed. Add new evidence to an existing issue when it describes the same problem.

## Propose a change

Explain the user-visible outcome and acceptance criteria. For a substantial change, discuss the approach before investing in implementation. Keep the patch focused, report the checks you actually ran, and update documentation when public behavior changes. [Develop and dogfood](docs/development.md) covers setup and validation.

AI-assisted contributions are welcome. Understand and take responsibility for the work you submit; be clear about material assistance and verification limits. Stay available for questions and discussion. Tools can handle engineering tasks, while the people involved maintain the relationships and decisions behind them.

## Improve the documentation

Pages live in `docs/`, with navigation in `zensical.toml`. Use English, direct prose, and examples that match the current interface. Tutorials lead to a working result, guides complete a task, reference defines contracts, and explanation describes the method. Keep parameter details in reference.

```bash
make docs
make docs-test
```

Verify prerequisites and expected results. Preserve readable commands and paragraphs on one line unless their format requires newlines.

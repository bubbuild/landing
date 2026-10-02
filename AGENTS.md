# Project instructions

Use English in code, documentation, commit messages and GitHub contributions. Write direct, present-tense prose. Keep paragraphs and commands on one line unless their format requires newlines.

Read the relevant documentation before changing a user contract. Keep CLI, HTTP and CI behavior consistent where they expose the same action. Reuse Bub 0.5.0 SDK and Pydantic Settings capabilities; do not discover external plugins or add a second agent runtime.

Exercise affected Landing contracts through their CLI, HTTP, SDK or CI entry points. Keep independent native CI authoritative for its recorded checkout and environment. The test fixture isolates ambient Landing and Bub environment settings; configuration tests supply their own inputs. The dogfood configuration in `.github/landing.yml` restricts agent capabilities and is not the test configuration.

For code review, load the `landing-review` skill. Use `friendly-python` and `piglet` for relevant Python work, `documentation-writer` for documentation structure, and `humanizer` when prose needs editing. Load only the guidance needed for the task.

For changes, run relevant tests, `uv run ty check`, `uv run pre-commit run --all-files`, and `uv run zensical build -s` when applicable. For review, reuse completed native checks; run a focused check only for an unresolved behavior or an explicit requirement. The native workflow owns the full Python matrix, quality, strict documentation and container acceptance checks.

For delegated GitHub fixes, use a task-named candidate branch and the applicable PR template. Verify the candidate before publication. When publishing with the prepared workflow token, explicitly start native CI using `gh workflow run main.yml --ref BRANCH -f number=PR_NUMBER -f head=CANDIDATE_SHA`; token-generated pushes do not automatically start CI. Main accepts these inputs and records its actual checkout revision. Use native reviews and inline locations for actionable review findings, and reply to follow-up questions in their existing review threads. Do not merge or change credentials.

# Project instructions

Use English in code, documentation, commit messages and GitHub contributions. Write direct, present-tense prose. Keep paragraphs and commands on one line unless their format requires newlines.

Read the relevant documentation before changing a user contract. Keep CLI, HTTP and CI behavior consistent where they expose the same action. Reuse Bub 0.5.0 SDK and Pydantic Settings capabilities; do not discover external plugins or add a second agent runtime.

Add tests for user-visible behavior or a demonstrated regression. Do not test helper layouts, exact prompt wording, argument order or internal bookkeeping. A rewrite that preserves the user's experience should keep the tests passing. Straightforward scripts need no test that repeats their implementation.

For code review, load the `landing-review` skill. Use `friendly-python` and `piglet` for relevant Python work, `documentation-writer` for documentation structure, and `humanizer` when prose needs editing. Load only the guidance needed for the task.

Use the repository's issue and PR templates. State actual validation and unresolved assumptions. Publication follows the user's delegation and the executing mode's permissions; project instructions do not grant additional authority.

Run relevant tests, `uv run ty check`, `uv run pre-commit run --all-files`, and `uv run zensical build -s` when applicable. Report checks you could not run. Do not weaken a check to make a candidate pass.

# Project instructions

Use English in code, documentation, commits, and GitHub contributions. Write direct, present-tense prose; keep paragraphs and commands on one line unless their format requires newlines.

Read the affected contract before changing it. CLI, HTTP, SDK, and CI share action semantics. Use one Bub 0.5.0 SDK runtime with native configuration, state, hooks, tools, and skill discovery; do not add external plugin discovery or a second runtime.

For Python work, use friendly-python and piglet. For documentation structure and prose, use documentation-writer and humanizer. For review, load `.agents/skills/landing-review/SKILL.md`; use relevant evidence rather than exhausting a checklist.

For changes, run affected behavior tests, `uv run ty check`, `uv run pre-commit run --all-files`, and strict docs builds when applicable. Follow `docs/development.md` for test philosophy and dogfooding. Reviews reuse native checks for their recorded revision and environment; focused checks resolve remaining questions.

GitHub fixes use task-named branches and the applicable contribution template. Verify before publishing. With a workflow token, start candidate CI explicitly: `gh workflow run main.yml --ref BRANCH -f number=PR_NUMBER -f head=CANDIDATE_SHA`. Main records its actual checkout revision. Publish native reviews with inline findings, reply in the original thread, and do not merge or change credentials.

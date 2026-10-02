"""Repository-owned contribution templates, read from the selected checkout."""

from pathlib import Path

from landing.models import Mode


def templates(workspace: Path, mode: Mode) -> str:
    if mode not in {"issuer", "fixer"}:
        return ""
    name = "issue_template" if mode == "issuer" else "pull_request_template"
    files = []
    for directory in (workspace / ".github", workspace, workspace / "docs"):
        if not directory.is_dir():
            continue
        for entry in sorted(directory.iterdir()):
            if entry.name.casefold() == name and entry.is_dir():
                files.extend(
                    file
                    for file in sorted(entry.iterdir())
                    if file.is_file()
                    and file.suffix.casefold() in {".md", ".markdown", ".txt", ".yaml", ".yml"}
                    and file.stem.casefold() != "config"
                )
            elif entry.is_file() and entry.name.casefold() in {name, name + ".md", name + ".markdown", name + ".txt"}:
                files.append(entry)
    if not files:
        return ""
    guidance = (
        "Choose the issue template matching the problem. Fill its requested sections with evidence. "
        "For YAML issue forms, use field labels as Markdown headings and answer the required fields; "
        "gh publishes Markdown, not an interactive form. Respect template title, labels and assignees "
        "when appropriate and authorized. Do not invent answers or claim unchecked acknowledgements."
        if mode == "issuer"
        else "Choose the applicable pull request template when publishing a candidate: "
        "fill the requested sections, report actual validation and leave unverified checklist items unchecked. "
        "Do not copy template front matter into the body. Include the issue link and relevant validation provenance."
    )
    return (
        "\n\nRepository contribution templates:\n"
        + guidance
        + "\n"
        + "\n\n".join(f"{file.relative_to(workspace)}:\n{file.read_text(encoding='utf-8')}" for file in files)
    )

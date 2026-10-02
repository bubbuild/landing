"""Pinned default skill sources; gh installs assets, the SDK loads instructions."""

import asyncio
import fcntl
import shutil
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from bub.builtin.agent import HINT_RE
from bub.skills import discover_skills


@dataclass(frozen=True)
class Source:
    repository: str
    path: str
    pin: str
    description: str

    def install(self, name: str, directory: Path) -> None:
        try:
            executable = shutil.which("gh") or "gh"
            if self.path == "SKILL.md":
                # gh skill does not discover a publisher's root-level SKILL.md.
                skill = directory / name
                skill.mkdir()
                for filename in ("SKILL.md", "LICENSE"):
                    content = subprocess.run(  # noqa: S603 -- pinned public sources; user's gh on PATH, no shell.
                        [
                            executable,
                            "api",
                            f"repos/{self.repository}/contents/{filename}?ref={self.pin}",
                            "--header",
                            "Accept: application/vnd.github.raw+json",
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                        timeout=120,
                    ).stdout
                    (skill / filename).write_text(content, encoding="utf-8")
            else:
                subprocess.run(  # noqa: S603 -- user's gh owns discovery, download and source metadata.
                    [
                        executable,
                        "skill",
                        "install",
                        self.repository,
                        self.path,
                        "--pin",
                        self.pin,
                        "--dir",
                        str(directory),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(exc.stderr.strip() or str(exc)) from exc


DEFAULTS = {
    "documentation-writer": Source(
        "github/awesome-copilot",
        "skills/documentation-writer",
        "143a3d976b3c1603cc8932984d5e1f28501cb5fc",
        "Structure user documentation with Diataxis.",
    ),
    "humanizer": Source(
        "blader/humanizer",
        "SKILL.md",
        "225a6f39ac85f76ee48dbad772ea4abe4ed6c9d8",
        "Edit prose for plain, natural language without changing facts.",
    ),
    "piglet": Source(
        "PsiACE/skills",
        "skills/piglet",
        "2265aed05caf199426a8062461e2c9901be996d8",
        "Review Python naming, control flow, functions and error handling.",
    ),
    "friendly-python": Source(
        "PsiACE/skills",
        "skills/friendly-python",
        "2265aed05caf199426a8062461e2c9901be996d8",
        "Design readable Python APIs, boundaries and reusable code.",
    ),
}


class DefaultSkills:
    def __init__(self, cache: Path, *, enabled: bool = True) -> None:
        self.cache = cache.expanduser().resolve()
        self.sources = DEFAULTS if enabled else {}

    @property
    def roots(self) -> tuple[Path, ...]:
        return tuple(self.cache / name / source.pin for name, source in self.sources.items())

    def available(self, workspace: Path, roots: Iterable[Path]) -> dict[str, Source]:
        installed = {skill.name for skill in discover_skills(workspace, skill_dirs=tuple(roots))}
        return {name: source for name, source in self.sources.items() if name not in installed}

    def prompt(self, workspace: Path, roots: Iterable[Path]) -> str:
        available = self.available(workspace, roots)
        if not available:
            return ""
        return "\nOptional default skills (load by name with skill only when relevant):\n" + "\n".join(
            f"- {name}: {source.description}" for name, source in available.items()
        )

    async def prepare(self, prompt: str, workspace: Path, roots: tuple[Path, ...]) -> None:
        for name in HINT_RE.findall(prompt):
            await asyncio.to_thread(self.load, name, workspace, roots)

    def load(self, name: str, workspace: Path, roots: Iterable[Path]) -> None:
        source = self.available(workspace, roots).get(name)
        if source is None:
            return
        root = self.cache / name / source.pin
        root.parent.mkdir(parents=True, exist_ok=True)
        with (root.parent / ".lock").open("a") as owner:
            fcntl.flock(owner, fcntl.LOCK_EX)
            if (root / name / "SKILL.md").is_file():
                return
            with TemporaryDirectory(dir=root.parent) as staging:
                source.install(name, Path(staging))
                root.mkdir(exist_ok=True)
                (Path(staging) / name).rename(root / name)

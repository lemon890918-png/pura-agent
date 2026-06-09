"""Skills system: load SKILL.md files and render to system prompt.

Phase 10. Each skill is a directory with SKILL.md (frontmatter + body).
Inspired by https://www.skills.sh/ — "reusable capabilities for AI agents".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Skill:
    """A loaded skill."""

    name: str
    description: str
    version: str
    source: str
    body: str
    allowed_tools: list[str] = field(default_factory=list)
    path: Path | None = None

    def render_prompt(self) -> str:
        """Render this skill for inclusion in the system prompt."""
        tools = ", ".join(self.allowed_tools) if self.allowed_tools else "(any)"
        header = (
            f"### Skill: {self.name}\n"
            f"**Description:** {self.description}\n"
            f"**Allowed tools:** {tools}\n"
            f"**Version:** {self.version}\n"
        )
        return header + "\n" + self.body


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


def parse_skill_md(content: str, path: Path | None = None) -> Skill:
    """Parse a SKILL.md file with YAML frontmatter."""
    m = _FRONTMATTER_RE.match(content)
    if m is None:
        # No frontmatter; treat whole thing as body
        # Use parent dir name (e.g. /tmp/my-skill/SKILL.md → "my-skill")
        if path is not None and path.parent != path:
            skill_name = path.parent.name
        elif path is not None:
            skill_name = path.stem
        else:
            skill_name = "unnamed"
        return Skill(
            name=skill_name,
            description="",
            version="0.0.0",
            source="local",
            body=content,
            path=path,
        )
    fm_text, body = m.group(1), m.group(2)
    # tiny YAML parser for the simple key: value fields we use
    fm: dict[str, Any] = {}
    for line in fm_text.splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        k = k.strip()
        v = v.strip()
        # strip quotes
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            v = v[1:-1]
        if k == "allowed_tools":
            # parse [a, b, c]
            v = [t.strip() for t in v.strip("[]").split(",") if t.strip()]
        fm[k] = v
    return Skill(
        name=fm.get("name") or (path.stem if path else "unnamed"),
        description=fm.get("description", ""),
        version=fm.get("version", "0.0.0"),
        source=fm.get("source", "local"),
        body=body.strip(),
        allowed_tools=fm.get("allowed_tools", []),
        path=path,
    )


def load_skill(path: Path) -> Skill:
    """Load a single SKILL.md from path."""
    return parse_skill_md(path.read_text(encoding="utf-8"), path=path)


def discover_skills(skills_dir: Path) -> list[Skill]:
    """Find all skills in a directory.

    A skill = subdirectory containing SKILL.md.
    """
    if not skills_dir.exists():
        return []
    skills: list[Skill] = []
    for child in sorted(skills_dir.iterdir()):
        if not child.is_dir():
            continue
        skill_md = child / "SKILL.md"
        if not skill_md.exists():
            continue
        try:
            skills.append(load_skill(skill_md))
        except Exception:
            # skip malformed skills
            continue
    return skills


def render_skills_prompt(skills: list[Skill]) -> str:
    """Render all skills as a single system-prompt section."""
    if not skills:
        return ""
    parts = ["## Active Skills\n"]
    parts.append(
        "The following skills are loaded. Follow each skill's instructions "
        "when its description matches the user's request.\n"
    )
    for s in skills:
        parts.append("---\n")
        parts.append(s.render_prompt())
    return "\n".join(parts)


__all__ = [
    "Skill",
    "parse_skill_md",
    "load_skill",
    "discover_skills",
    "render_skills_prompt",
]

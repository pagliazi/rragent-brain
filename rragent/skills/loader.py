"""
Skill Loader — load YAML+Markdown skill files.

Skill format:
---
name: skill_name
description: What this skill does
trigger: When to use this skill
tools: tool1, tool2
source: bundled | evolution_engine | hub
---

# Skill Name
Markdown body with steps, parameters, pitfalls, verification.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("rragent.skills.loader")


@dataclass
class Skill:
    """A loaded skill."""

    name: str
    description: str
    trigger: str
    tools: list[str]
    source: str
    body: str
    path: str = ""
    created: str = ""
    metadata: dict = field(default_factory=dict)

    @property
    def is_bundled(self) -> bool:
        return self.source == "bundled"

    @property
    def summary(self) -> str:
        """One-line summary for system prompt injection."""
        return f"{self.name}: {self.description}"


class SkillLoader:
    """
    Load skills from directories.

    Scans:
    1. Bundled skills: rragent/skills/bundled/
    2. User skills: ~/.rragent/skills/
    3. Workspace skills: ~/.rragent/workspace/skills/

    Skills are YAML frontmatter + Markdown body files.
    """

    def __init__(
        self,
        bundled_dir: str | Path | None = None,
        user_dir: str | Path | None = None,
        workspace_dir: str | Path | None = None,
    ):
        self._dirs: list[Path] = []

        if bundled_dir:
            self._dirs.append(Path(bundled_dir))
        else:
            # Default bundled location
            pkg_dir = Path(__file__).parent / "bundled"
            if pkg_dir.exists():
                self._dirs.append(pkg_dir)

        if user_dir:
            self._dirs.append(Path(user_dir))
        else:
            self._dirs.append(Path.home() / ".rragent" / "skills")

        if workspace_dir:
            self._dirs.append(Path(workspace_dir))
        else:
            ws = Path.home() / ".rragent" / "workspace" / "skills"
            if ws.exists():
                self._dirs.append(ws)

        self._skills: dict[str, Skill] = {}
        self._loaded = False

    def load_all(self) -> dict[str, Skill]:
        """Load all skills from configured directories."""
        self._skills.clear()

        for skill_dir in self._dirs:
            if not skill_dir.exists():
                continue

            for path in skill_dir.glob("*.md"):
                try:
                    skill = self._load_file(path)
                    if skill:
                        self._skills[skill.name] = skill
                except Exception as e:
                    logger.warning(f"Failed to load skill {path}: {e}")

        self._loaded = True
        logger.info(f"Loaded {len(self._skills)} skills from {len(self._dirs)} directories")
        return self._skills

    def get(self, name: str) -> Skill | None:
        """Get a skill by name."""
        if not self._loaded:
            self.load_all()
        return self._skills.get(name)

    def list_skills(self) -> list[Skill]:
        """List all loaded skills."""
        if not self._loaded:
            self.load_all()
        return list(self._skills.values())

    def reload(self):
        """Force reload all skills."""
        self._loaded = False
        self.load_all()

    def add_skill(self, skill: Skill):
        """Add a skill dynamically (e.g., from Evolution Engine)."""
        self._skills[skill.name] = skill

    def _load_file(self, path: Path) -> Skill | None:
        """Parse a single skill file."""
        content = path.read_text(encoding="utf-8")

        # Parse YAML frontmatter
        frontmatter, body = self._parse_frontmatter(content)
        if not frontmatter:
            logger.debug(f"No frontmatter in {path}, treating as plain skill")
            return Skill(
                name=path.stem,
                description="",
                trigger="",
                tools=[],
                source="unknown",
                body=content,
                path=str(path),
            )

        # Extract fields
        name = frontmatter.get("name", path.stem)
        tools_raw = frontmatter.get("tools", "")
        if isinstance(tools_raw, str):
            tools = [t.strip() for t in tools_raw.split(",") if t.strip()]
        elif isinstance(tools_raw, list):
            tools = tools_raw
        else:
            tools = []

        return Skill(
            name=name,
            description=frontmatter.get("description", ""),
            trigger=frontmatter.get("trigger", ""),
            tools=tools,
            source=frontmatter.get("source", "unknown"),
            body=body.strip(),
            path=str(path),
            created=frontmatter.get("created", ""),
            metadata={
                k: v for k, v in frontmatter.items()
                if k not in ("name", "description", "trigger", "tools", "source", "created")
            },
        )

    def _parse_frontmatter(self, content: str) -> tuple[dict, str]:
        """Parse YAML frontmatter from --- delimited content."""
        match = re.match(r'^---\s*\n(.*?)\n---\s*\n(.*)', content, re.DOTALL)
        if not match:
            return {}, content

        yaml_str = match.group(1)
        body = match.group(2)

        # Simple YAML parser (key: value per line)
        frontmatter = {}
        for line in yaml_str.split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                key, _, value = line.partition(":")
                frontmatter[key.strip()] = value.strip()

        return frontmatter, body

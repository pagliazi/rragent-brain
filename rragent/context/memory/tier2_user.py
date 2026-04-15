"""
Tier 2 User Memory — persistent user-level memory.

Stores user preferences, trading habits, knowledge profile.
Persisted to USER.md in the workspace directory.
Updated by Background Review (Loop 2) and direct user commands.

Format: Markdown sections with key-value pairs.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("rragent.context.memory.user")


@dataclass
class UserMemoryEntry:
    """A persistent user memory entry."""

    key: str
    value: str
    section: str  # "preferences" | "knowledge" | "habits" | "notes"
    updated_at: str = ""


class UserMemory:
    """
    Tier 2: User-level persistent memory.

    Stores information about the user that persists across sessions.
    Backed by USER.md in the workspace directory.

    Sections:
    - Preferences: trading style, risk tolerance, sectors of interest
    - Knowledge: expertise areas, experience level
    - Habits: typical workflows, command patterns, schedule
    - Notes: miscellaneous observations from Background Review

    File format:
    ```markdown
    ## Preferences
    - risk_tolerance: moderate
    - favorite_sectors: semiconductor, new energy

    ## Knowledge
    - expertise: quantitative analysis, factor investing
    - experience: 3 years A-share trading

    ## Habits
    - morning_routine: check limitup board at 9:30
    - analysis_style: prefers data-driven over narrative
    ```
    """

    SECTIONS = ["Preferences", "Knowledge", "Habits", "Notes"]

    def __init__(self, workspace_dir: str | Path | None = None):
        if workspace_dir:
            self._dir = Path(workspace_dir)
        else:
            # Try RRAgent workspace first, then RRAgent
            rragent_ws = Path.home() / ".rragent" / "workspace"
            if rragent_ws.exists():
                self._dir = rragent_ws
            else:
                self._dir = Path.home() / ".rragent"

        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "USER.md"
        self._entries: dict[str, UserMemoryEntry] = {}
        self._load()

    def _load(self):
        """Load entries from USER.md."""
        if not self._path.exists():
            return

        content = self._path.read_text(encoding="utf-8")
        current_section = "Notes"

        for line in content.split("\n"):
            line = line.strip()

            # Section header
            if line.startswith("## "):
                section = line[3:].strip()
                if section in self.SECTIONS:
                    current_section = section
                continue

            # Key-value entry
            match = re.match(r'^-\s+(\w+):\s+(.+)$', line)
            if match:
                key = match.group(1)
                value = match.group(2)
                self._entries[key] = UserMemoryEntry(
                    key=key,
                    value=value,
                    section=current_section,
                )

    def get(self, key: str) -> str | None:
        """Get a user memory value."""
        entry = self._entries.get(key)
        return entry.value if entry else None

    def set(self, key: str, value: str, section: str = "Notes"):
        """Set a user memory value and persist to disk."""
        self._entries[key] = UserMemoryEntry(
            key=key,
            value=value,
            section=section,
            updated_at=time.strftime("%Y-%m-%d"),
        )
        self._save()

    def delete(self, key: str):
        """Delete a user memory entry."""
        if key in self._entries:
            del self._entries[key]
            self._save()

    def get_section(self, section: str) -> dict[str, str]:
        """Get all entries in a section."""
        return {
            e.key: e.value
            for e in self._entries.values()
            if e.section == section
        }

    def get_context_string(self) -> str:
        """Build context string for system prompt injection."""
        if not self._entries:
            return ""

        sections: dict[str, list[str]] = {}
        for entry in self._entries.values():
            if entry.section not in sections:
                sections[entry.section] = []
            sections[entry.section].append(f"- {entry.key}: {entry.value}")

        parts = []
        for section in self.SECTIONS:
            if section in sections:
                parts.append(f"### {section}")
                parts.extend(sections[section])

        return "\n".join(parts)

    def _save(self):
        """Persist entries to USER.md."""
        sections: dict[str, list[str]] = {s: [] for s in self.SECTIONS}

        for entry in self._entries.values():
            section = entry.section if entry.section in self.SECTIONS else "Notes"
            sections[section].append(f"- {entry.key}: {entry.value}")

        lines = ["# User Profile\n"]
        for section in self.SECTIONS:
            if sections[section]:
                lines.append(f"## {section}")
                lines.extend(sections[section])
                lines.append("")

        content = "\n".join(lines)
        self._path.write_text(content, encoding="utf-8")
        logger.debug(f"Saved {len(self._entries)} user memory entries to {self._path}")

    @property
    def all_entries(self) -> dict[str, UserMemoryEntry]:
        return dict(self._entries)

    @property
    def stats(self) -> dict:
        sections = {}
        for entry in self._entries.values():
            sections[entry.section] = sections.get(entry.section, 0) + 1
        return {
            "total_entries": len(self._entries),
            "sections": sections,
            "path": str(self._path),
        }

"""
Skill Sync — bidirectional skill synchronization.

Syncs skills between:
- RRAgent local (~/.rragent/skills/)
- RRAgent workspace (~/.rragent/workspace/skills/)
- Hermes skills store (if available)

Ensures auto-created skills are available across all systems.
"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

logger = logging.getLogger("rragent.skills.sync")


class SkillSync:
    """
    Bidirectional skill synchronization.

    Strategy:
    - Source of truth: ~/.rragent/skills/ (RRAgent creates skills here)
    - Mirrors: RRAgent workspace, Hermes store
    - Sync direction: RRAgent -> mirrors (one-way for auto-created)
    - Manual skills from mirrors are imported on demand

    Conflict resolution: newer file wins (by mtime).
    """

    def __init__(
        self,
        rragent_dir: str | Path | None = None,
        hermes_dir: str | Path | None = None,
    ):
        self.rragent_dir = (
            Path(rragent_dir) if rragent_dir
            else Path.home() / ".rragent" / "skills"
        )
        self.hermes_dir = (
            Path(hermes_dir) if hermes_dir
            else Path.home() / ".hermes" / "skills"
        )

        # Ensure primary dir exists
        self.rragent_dir.mkdir(parents=True, exist_ok=True)

    async def sync_all(self):
        """Sync skills to all mirrors."""
        synced = 0
        synced += self._sync_to_dir(self.rragent_dir)
        synced += self._sync_to_dir(self.hermes_dir)

        if synced > 0:
            logger.info(f"Synced {synced} skill files to mirrors")
        return synced

    async def import_from_legacy(self):
        """Import new skills from RRAgent workspace."""
        return self._import_from_dir(self.rragent_dir)

    async def import_from_hermes(self):
        """Import new skills from Hermes store."""
        return self._import_from_dir(self.hermes_dir)

    def _sync_to_dir(self, target_dir: Path) -> int:
        """Copy new/updated skills from RRAgent to target directory."""
        if not target_dir.parent.exists():
            return 0

        target_dir.mkdir(parents=True, exist_ok=True)
        synced = 0

        for src_file in self.rragent_dir.glob("*.md"):
            dst_file = target_dir / src_file.name

            should_copy = False
            if not dst_file.exists():
                should_copy = True
            elif src_file.stat().st_mtime > dst_file.stat().st_mtime:
                should_copy = True

            if should_copy:
                try:
                    shutil.copy2(src_file, dst_file)
                    synced += 1
                except OSError as e:
                    logger.warning(f"Failed to sync {src_file.name} to {target_dir}: {e}")

        return synced

    def _import_from_dir(self, source_dir: Path) -> int:
        """Import new skills from a source directory."""
        if not source_dir.exists():
            return 0

        imported = 0
        for src_file in source_dir.glob("*.md"):
            dst_file = self.rragent_dir / src_file.name

            if not dst_file.exists():
                try:
                    shutil.copy2(src_file, dst_file)
                    imported += 1
                    logger.info(f"Imported skill: {src_file.name} from {source_dir}")
                except OSError as e:
                    logger.warning(f"Failed to import {src_file.name}: {e}")

        return imported

    def list_mirrors(self) -> dict[str, dict]:
        """List status of all mirror directories."""
        result = {}
        for name, path in [
            ("rragent", self.rragent_dir),
            ("rragent", self.rragent_dir),
            ("hermes", self.hermes_dir),
        ]:
            if path.exists():
                skills = list(path.glob("*.md"))
                result[name] = {
                    "path": str(path),
                    "exists": True,
                    "skill_count": len(skills),
                }
            else:
                result[name] = {
                    "path": str(path),
                    "exists": False,
                    "skill_count": 0,
                }
        return result

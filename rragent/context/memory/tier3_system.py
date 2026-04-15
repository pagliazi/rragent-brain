"""
Tier 3 System Memory — long-term system-level knowledge.

Stores cross-user, cross-session knowledge:
- Tool performance baselines
- Common error patterns and fixes
- Optimal parameter defaults
- Market behavior patterns (time-of-day effects, etc.)

Persisted to MEMORY.md with detail files.
Updated by Evolution Engine (Loop 3) and GEPA Pipeline (Loop 4).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("rragent.context.memory.system")


@dataclass
class SystemMemoryEntry:
    """A system-level memory entry."""

    key: str
    value: str
    category: str  # "performance" | "pattern" | "fix" | "config"
    confidence: float = 1.0  # 0.0-1.0, decays over time
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    source: str = ""  # "evolution" | "gepa" | "manual"
    metadata: dict = field(default_factory=dict)


class SystemMemory:
    """
    Tier 3: System-level persistent memory.

    Stores knowledge that transcends individual users and sessions.
    Backed by ~/.rragent/memory/ directory with JSON files.

    Categories:
    - performance: Tool latency baselines, success rates
    - pattern: Detected behavioral patterns (market time effects, etc.)
    - fix: Known error fixes and workarounds
    - config: Learned optimal configurations

    Confidence decay: entries lose 10% confidence per week if not reinforced.
    Entries below 0.3 confidence are pruned.
    """

    CONFIDENCE_DECAY_PER_DAY = 0.014  # ~10% per week
    PRUNE_THRESHOLD = 0.3
    MEMORY_DIR = Path.home() / ".rragent" / "memory"
    INDEX_FILE = "MEMORY.md"

    def __init__(self, memory_dir: str | Path | None = None):
        self._dir = Path(memory_dir) if memory_dir else self.MEMORY_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._entries: dict[str, SystemMemoryEntry] = {}
        self._load()

    def _load(self):
        """Load all memory entries from JSON files."""
        for path in self._dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                entry = SystemMemoryEntry(
                    key=data["key"],
                    value=data["value"],
                    category=data.get("category", "pattern"),
                    confidence=data.get("confidence", 1.0),
                    created_at=data.get("created_at", 0),
                    updated_at=data.get("updated_at", 0),
                    source=data.get("source", ""),
                    metadata=data.get("metadata", {}),
                )
                self._entries[entry.key] = entry
            except Exception as e:
                logger.warning(f"Failed to load memory {path}: {e}")

    def get(self, key: str) -> str | None:
        """Get a system memory value (with confidence decay)."""
        entry = self._entries.get(key)
        if not entry:
            return None

        # Apply confidence decay
        days_since_update = (time.time() - entry.updated_at) / 86400
        current_confidence = entry.confidence - (days_since_update * self.CONFIDENCE_DECAY_PER_DAY)

        if current_confidence < self.PRUNE_THRESHOLD:
            self.delete(key)
            return None

        return entry.value

    def set(
        self,
        key: str,
        value: str,
        category: str = "pattern",
        source: str = "evolution",
        confidence: float = 1.0,
        metadata: dict | None = None,
    ):
        """Set a system memory entry and persist."""
        now = time.time()

        if key in self._entries:
            # Reinforce: boost confidence, update value
            existing = self._entries[key]
            existing.value = value
            existing.confidence = min(1.0, confidence + 0.1)  # Reinforcement bonus
            existing.updated_at = now
            existing.source = source
            if metadata:
                existing.metadata.update(metadata)
        else:
            self._entries[key] = SystemMemoryEntry(
                key=key,
                value=value,
                category=category,
                confidence=confidence,
                created_at=now,
                updated_at=now,
                source=source,
                metadata=metadata or {},
            )

        self._save_entry(key)

    def delete(self, key: str):
        """Delete a memory entry."""
        if key in self._entries:
            del self._entries[key]
            path = self._dir / f"{self._safe_filename(key)}.json"
            if path.exists():
                path.unlink()

    def get_by_category(self, category: str) -> list[SystemMemoryEntry]:
        """Get all entries in a category."""
        return [e for e in self._entries.values() if e.category == category]

    def search(self, query: str) -> list[SystemMemoryEntry]:
        """Simple keyword search across entries."""
        query_lower = query.lower()
        results = []
        for entry in self._entries.values():
            if (
                query_lower in entry.key.lower()
                or query_lower in entry.value.lower()
            ):
                results.append(entry)
        return results

    def get_context_string(self, max_entries: int = 20) -> str:
        """Build context string for system prompt injection."""
        if not self._entries:
            return ""

        # Sort by confidence (highest first)
        sorted_entries = sorted(
            self._entries.values(),
            key=lambda e: self._effective_confidence(e),
            reverse=True,
        )[:max_entries]

        lines = []
        for entry in sorted_entries:
            conf = self._effective_confidence(entry)
            if conf >= self.PRUNE_THRESHOLD:
                lines.append(f"- [{entry.category}] {entry.key}: {entry.value}")

        return "\n".join(lines) if lines else ""

    def prune(self) -> int:
        """Remove entries below confidence threshold."""
        to_prune = []
        for key, entry in self._entries.items():
            if self._effective_confidence(entry) < self.PRUNE_THRESHOLD:
                to_prune.append(key)

        for key in to_prune:
            self.delete(key)

        if to_prune:
            logger.info(f"Pruned {len(to_prune)} low-confidence system memories")
        return len(to_prune)

    def update_index(self):
        """Update the MEMORY.md index file."""
        lines = ["# System Memory Index\n"]

        by_category: dict[str, list[SystemMemoryEntry]] = {}
        for entry in self._entries.values():
            if entry.category not in by_category:
                by_category[entry.category] = []
            by_category[entry.category].append(entry)

        for category in sorted(by_category.keys()):
            lines.append(f"\n## {category.title()}")
            for entry in by_category[category]:
                conf = self._effective_confidence(entry)
                lines.append(
                    f"- [{conf:.0%}] {entry.key}: {entry.value[:80]}"
                )

        index_path = self._dir / self.INDEX_FILE
        index_path.write_text("\n".join(lines), encoding="utf-8")

    def _save_entry(self, key: str):
        """Persist a single entry to disk."""
        entry = self._entries.get(key)
        if not entry:
            return

        data = {
            "key": entry.key,
            "value": entry.value,
            "category": entry.category,
            "confidence": entry.confidence,
            "created_at": entry.created_at,
            "updated_at": entry.updated_at,
            "source": entry.source,
            "metadata": entry.metadata,
        }

        path = self._dir / f"{self._safe_filename(key)}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _effective_confidence(self, entry: SystemMemoryEntry) -> float:
        """Calculate confidence with time decay."""
        days = (time.time() - entry.updated_at) / 86400
        return max(0, entry.confidence - days * self.CONFIDENCE_DECAY_PER_DAY)

    def _safe_filename(self, key: str) -> str:
        """Convert key to safe filename."""
        import re
        return re.sub(r'[^\w.-]', '_', key)[:100]

    @property
    def stats(self) -> dict:
        categories = {}
        for entry in self._entries.values():
            categories[entry.category] = categories.get(entry.category, 0) + 1
        return {
            "total_entries": len(self._entries),
            "categories": categories,
            "path": str(self._dir),
        }

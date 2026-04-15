"""
Tier 1 Session Memory — in-conversation working memory.

Short-lived, scoped to a single conversation session.
Stores intermediate results, discovered preferences, and context
that should persist across tool calls within one session.

Lost when session ends (promoted to Tier 2 by Background Review).
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass, field


@dataclass
class MemoryEntry:
    """A single memory entry."""

    key: str
    value: str
    category: str  # "context" | "preference" | "tool_result" | "discovery"
    timestamp: float = field(default_factory=time.time)
    source: str = ""  # Which tool/turn created this
    ttl: float = 0  # 0 = no expiry


class SessionMemory:
    """
    Tier 1: Session-scoped working memory.

    Fast key-value store for in-conversation context.
    Backed by an OrderedDict with LRU eviction.

    Usage:
        memory.set("user_focus", "semiconductor sector", category="preference")
        memory.set("last_limitup_count", "42", category="tool_result")
        focus = memory.get("user_focus")
        context = memory.get_context_string()  # For system prompt injection
    """

    MAX_ENTRIES = 100
    MAX_CONTEXT_TOKENS = 500  # Rough limit for prompt injection

    def __init__(self, session_id: str = ""):
        self.session_id = session_id
        self._store: OrderedDict[str, MemoryEntry] = OrderedDict()

    def set(
        self,
        key: str,
        value: str,
        category: str = "context",
        source: str = "",
        ttl: float = 0,
    ):
        """Set a memory entry."""
        entry = MemoryEntry(
            key=key,
            value=value,
            category=category,
            source=source,
            ttl=ttl,
        )

        # Move to end (most recent)
        if key in self._store:
            self._store.move_to_end(key)
        self._store[key] = entry

        # Evict oldest if over limit
        while len(self._store) > self.MAX_ENTRIES:
            self._store.popitem(last=False)

    def get(self, key: str) -> str | None:
        """Get a memory value by key."""
        entry = self._store.get(key)
        if not entry:
            return None

        # Check TTL
        if entry.ttl > 0 and time.time() - entry.timestamp > entry.ttl:
            del self._store[key]
            return None

        # Move to end (access = recent)
        self._store.move_to_end(key)
        return entry.value

    def get_entry(self, key: str) -> MemoryEntry | None:
        """Get a full memory entry."""
        return self._store.get(key)

    def delete(self, key: str):
        """Delete a memory entry."""
        self._store.pop(key, None)

    def get_by_category(self, category: str) -> list[MemoryEntry]:
        """Get all entries in a category."""
        self._expire()
        return [e for e in self._store.values() if e.category == category]

    def get_context_string(self) -> str:
        """
        Build a context string for system prompt injection.

        Prioritizes preferences and discoveries over tool results.
        Truncates to MAX_CONTEXT_TOKENS rough estimate.
        """
        self._expire()

        # Priority: preference > discovery > context > tool_result
        priority = {"preference": 0, "discovery": 1, "context": 2, "tool_result": 3}
        sorted_entries = sorted(
            self._store.values(),
            key=lambda e: (priority.get(e.category, 99), -e.timestamp),
        )

        lines = []
        total_chars = 0
        max_chars = self.MAX_CONTEXT_TOKENS * 4  # rough token->char

        for entry in sorted_entries:
            line = f"- {entry.key}: {entry.value}"
            if total_chars + len(line) > max_chars:
                break
            lines.append(line)
            total_chars += len(line)

        return "\n".join(lines) if lines else ""

    def get_promotable_entries(self) -> list[MemoryEntry]:
        """
        Get entries worth promoting to Tier 2 (user-level).

        Background Review uses this to decide what to persist.
        Only preferences and discoveries are promotable.
        """
        return [
            e for e in self._store.values()
            if e.category in ("preference", "discovery")
        ]

    def _expire(self):
        """Remove expired entries."""
        now = time.time()
        expired = [
            k for k, v in self._store.items()
            if v.ttl > 0 and now - v.timestamp > v.ttl
        ]
        for k in expired:
            del self._store[k]

    def clear(self):
        """Clear all memory."""
        self._store.clear()

    @property
    def size(self) -> int:
        return len(self._store)

    @property
    def stats(self) -> dict:
        self._expire()
        categories = {}
        for entry in self._store.values():
            categories[entry.category] = categories.get(entry.category, 0) + 1
        return {
            "total_entries": len(self._store),
            "categories": categories,
            "session_id": self.session_id,
        }

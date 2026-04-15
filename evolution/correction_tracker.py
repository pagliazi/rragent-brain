"""
Correction Tracker — record tool self-corrections for pattern extraction.

Tracks: what tool -> what error -> what correction -> success?
These records feed into Background Review (Loop 2) and Evolution Engine (Loop 3).
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CorrectionRecord:
    """A single correction event."""

    tool_name: str
    error_message: str
    correction_action: str  # What the LLM did to fix it
    success: bool
    timestamp: float = field(default_factory=time.time)
    attempt_number: int = 1
    context: dict = field(default_factory=dict)


@dataclass
class CorrectionPattern:
    """A detected pattern of repeated corrections."""

    tool_name: str
    common_error: str
    successful_strategy: str
    occurrence_count: int
    success_rate: float
    examples: list[CorrectionRecord] = field(default_factory=list)


class CorrectionTracker:
    """
    Track tool execution corrections for self-learning.

    Records every error and successful correction, then extracts
    patterns that can be turned into Skills or Recovery Recipes.
    """

    def __init__(self, max_records: int = 500):
        self.max_records = max_records
        self._records: list[CorrectionRecord] = []
        self._error_counts: dict[str, int] = defaultdict(int)
        self._success_counts: dict[str, int] = defaultdict(int)
        self._active_errors: dict[str, CorrectionRecord] = {}

    def record_error(self, tool_name: str, error_message: str, context: dict | None = None):
        """Record a tool execution error (before correction attempt)."""
        key = f"{tool_name}:{hash(error_message) % 10000}"
        self._active_errors[key] = CorrectionRecord(
            tool_name=tool_name,
            error_message=error_message,
            correction_action="",
            success=False,
            context=context or {},
        )
        self._error_counts[tool_name] += 1

    def record_correction(
        self,
        tool_name: str,
        correction_action: str,
        success: bool,
        original_error: str = "",
    ):
        """Record a correction attempt (after LLM retried)."""
        # Find matching active error
        matched_key = None
        for key, record in self._active_errors.items():
            if key.startswith(f"{tool_name}:"):
                record.correction_action = correction_action
                record.success = success
                self._records.append(record)
                matched_key = key
                break

        if matched_key:
            del self._active_errors[matched_key]
        else:
            # No matching error found, create standalone record
            self._records.append(CorrectionRecord(
                tool_name=tool_name,
                error_message=original_error,
                correction_action=correction_action,
                success=success,
            ))

        if success:
            self._success_counts[tool_name] += 1

        # Trim old records
        if len(self._records) > self.max_records:
            self._records = self._records[-self.max_records:]

    def record_success(self, tool_name: str):
        """Record a successful tool execution (no correction needed)."""
        self._success_counts[tool_name] += 1
        # Clear any active errors for this tool
        keys_to_remove = [k for k in self._active_errors if k.startswith(f"{tool_name}:")]
        for k in keys_to_remove:
            del self._active_errors[k]

    @property
    def corrections(self) -> list[CorrectionRecord]:
        """All recorded corrections."""
        return list(self._records)

    @property
    def recent_corrections(self) -> list[CorrectionRecord]:
        """Corrections from the last hour."""
        cutoff = time.time() - 3600
        return [r for r in self._records if r.timestamp > cutoff]

    @property
    def has_corrections(self) -> bool:
        """Whether any corrections have been recorded."""
        return len(self._records) > 0

    def get_correction_patterns(self, min_occurrences: int = 3) -> list[CorrectionPattern]:
        """
        Extract correction patterns from recorded data.

        Groups by (tool_name, error_type) and finds:
        - Which errors repeat
        - Which correction strategies work
        - Success rates per tool
        """
        # Group by tool + error similarity
        groups: dict[str, list[CorrectionRecord]] = defaultdict(list)
        for record in self._records:
            # Normalize error message for grouping
            error_key = self._normalize_error(record.error_message)
            group_key = f"{record.tool_name}:{error_key}"
            groups[group_key].append(record)

        patterns = []
        for group_key, records in groups.items():
            if len(records) < min_occurrences:
                continue

            tool_name = records[0].tool_name
            successes = [r for r in records if r.success]
            success_rate = len(successes) / len(records) if records else 0

            # Find most common successful strategy
            strategy_counts: dict[str, int] = defaultdict(int)
            for r in successes:
                if r.correction_action:
                    strategy_counts[r.correction_action] += 1

            best_strategy = ""
            if strategy_counts:
                best_strategy = max(strategy_counts, key=strategy_counts.get)

            patterns.append(CorrectionPattern(
                tool_name=tool_name,
                common_error=records[0].error_message[:200],
                successful_strategy=best_strategy,
                occurrence_count=len(records),
                success_rate=success_rate,
                examples=records[:3],
            ))

        return sorted(patterns, key=lambda p: p.occurrence_count, reverse=True)

    def get_tool_error_rate(self, tool_name: str) -> float:
        """Error rate for a specific tool."""
        errors = self._error_counts.get(tool_name, 0)
        successes = self._success_counts.get(tool_name, 0)
        total = errors + successes
        return errors / total if total > 0 else 0.0

    def get_summary(self) -> dict:
        """Summary stats for Background Review consumption."""
        return {
            "total_corrections": len(self._records),
            "recent_corrections": len(self.recent_corrections),
            "error_counts": dict(self._error_counts),
            "success_counts": dict(self._success_counts),
            "active_errors": len(self._active_errors),
            "patterns_detected": len(self.get_correction_patterns()),
        }

    def clear(self):
        """Clear all records (after successful review)."""
        self._records.clear()
        self._active_errors.clear()

    def _normalize_error(self, error: str) -> str:
        """Normalize error message for grouping similar errors."""
        # Remove variable parts: numbers, paths, timestamps
        import re
        normalized = re.sub(r'\d+', 'N', error)
        normalized = re.sub(r'/[\w/.-]+', '/PATH', normalized)
        normalized = re.sub(r'\b\w{32,}\b', 'HASH', normalized)
        # Take first 100 chars
        return normalized[:100]

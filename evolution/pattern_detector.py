"""
Pattern Detector — detect repeated tool chain patterns from execution history.

Consumes execution events from Redis Stream and identifies:
- Repeated tool call sequences (candidates for Skill creation)
- Common parameter combinations
- Tool co-occurrence patterns
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExecutionEvent:
    """A single tool execution event."""

    tool_name: str
    action: str
    params: dict
    result_summary: str
    success: bool
    latency_ms: float
    timestamp: float
    session_id: str = ""
    corrections: list[dict] = field(default_factory=list)


@dataclass
class ToolChainPattern:
    """A detected repeated tool chain."""

    chain: list[str]  # Ordered list of tool names
    occurrence_count: int
    avg_total_latency_ms: float
    common_params: dict  # Shared parameter patterns
    success_rate: float
    first_seen: float
    last_seen: float
    session_ids: list[str] = field(default_factory=list)

    def describe(self) -> str:
        chain_str = " -> ".join(self.chain)
        return (
            f"Tool chain: {chain_str}\n"
            f"Occurrences: {self.occurrence_count}\n"
            f"Success rate: {self.success_rate:.0%}\n"
            f"Avg latency: {self.avg_total_latency_ms:.0f}ms\n"
            f"Common params: {self.common_params}"
        )


class PatternDetector:
    """
    Detect repeated tool usage patterns for automatic Skill creation.

    Algorithm:
    1. Group events by session
    2. Extract ordered tool sequences per session
    3. Find common subsequences across sessions
    4. Score by frequency and success rate
    """

    def __init__(
        self,
        min_chain_length: int = 2,
        max_chain_length: int = 8,
        window_hours: int = 24,
    ):
        self.min_chain_length = min_chain_length
        self.max_chain_length = max_chain_length
        self.window_hours = window_hours

    def detect(self, events: list[ExecutionEvent]) -> list[ToolChainPattern]:
        """Detect patterns from a batch of execution events."""
        if not events:
            return []

        # Filter to recent window
        cutoff = time.time() - (self.window_hours * 3600)
        recent = [e for e in events if e.timestamp > cutoff]
        if not recent:
            return []

        # Group by session
        sessions: dict[str, list[ExecutionEvent]] = defaultdict(list)
        for event in recent:
            sessions[event.session_id].append(event)

        # Sort each session by timestamp
        for session_events in sessions.values():
            session_events.sort(key=lambda e: e.timestamp)

        # Extract tool sequences
        sequences: list[tuple[list[str], str, list[ExecutionEvent]]] = []
        for sid, session_events in sessions.items():
            tools = [e.tool_name for e in session_events]
            sequences.append((tools, sid, session_events))

        # Find common subsequences (n-gram approach)
        ngram_counts: dict[tuple[str, ...], list[dict]] = defaultdict(list)
        for tools, sid, session_events in sequences:
            for length in range(self.min_chain_length, self.max_chain_length + 1):
                for i in range(len(tools) - length + 1):
                    ngram = tuple(tools[i:i + length])
                    ngram_events = session_events[i:i + length]
                    ngram_counts[ngram].append({
                        "session_id": sid,
                        "events": ngram_events,
                    })

        # Convert to patterns
        patterns = []
        for ngram, occurrences in ngram_counts.items():
            if len(occurrences) < 2:
                continue

            # Calculate metrics
            all_events = [e for occ in occurrences for e in occ["events"]]
            success_rate = sum(1 for e in all_events if e.success) / len(all_events)

            latencies = []
            for occ in occurrences:
                total_lat = sum(e.latency_ms for e in occ["events"])
                latencies.append(total_lat)

            timestamps = [e.timestamp for e in all_events]
            session_ids = list(set(occ["session_id"] for occ in occurrences))

            # Extract common parameters
            common_params = self._extract_common_params(
                [e for occ in occurrences for e in occ["events"]]
            )

            patterns.append(ToolChainPattern(
                chain=list(ngram),
                occurrence_count=len(occurrences),
                avg_total_latency_ms=sum(latencies) / len(latencies) if latencies else 0,
                common_params=common_params,
                success_rate=success_rate,
                first_seen=min(timestamps),
                last_seen=max(timestamps),
                session_ids=session_ids,
            ))

        # Sort by occurrence count descending
        patterns.sort(key=lambda p: p.occurrence_count, reverse=True)

        # Remove subsumed patterns (if A->B->C exists, don't also report A->B)
        return self._remove_subsumed(patterns)

    def _extract_common_params(self, events: list[ExecutionEvent]) -> dict:
        """Find parameter values that appear in >50% of events for each tool."""
        tool_params: dict[str, list[dict]] = defaultdict(list)
        for event in events:
            tool_params[event.tool_name].append(event.params)

        common = {}
        for tool, param_list in tool_params.items():
            if len(param_list) < 2:
                continue

            # Find keys present in all param dicts
            all_keys = set()
            for p in param_list:
                all_keys.update(p.keys())

            tool_common = {}
            for key in all_keys:
                values = [p.get(key) for p in param_list if key in p]
                if not values:
                    continue

                # Check if >50% have the same value
                value_counts: dict[Any, int] = defaultdict(int)
                for v in values:
                    # Convert unhashable types
                    v_key = str(v) if isinstance(v, (list, dict)) else v
                    value_counts[v_key] += 1

                most_common_val = max(value_counts, key=value_counts.get)
                if value_counts[most_common_val] > len(values) * 0.5:
                    tool_common[key] = most_common_val

            if tool_common:
                common[tool] = tool_common

        return common

    def _remove_subsumed(self, patterns: list[ToolChainPattern]) -> list[ToolChainPattern]:
        """Remove shorter patterns that are subsets of longer ones."""
        if not patterns:
            return []

        result = []
        chains = [tuple(p.chain) for p in patterns]

        for i, pattern in enumerate(patterns):
            chain = chains[i]
            is_subsumed = False

            for j, other_pattern in enumerate(patterns):
                if i == j:
                    continue
                other_chain = chains[j]
                if len(other_chain) <= len(chain):
                    continue

                # Check if chain is a contiguous subsequence of other_chain
                other_str = ",".join(other_chain)
                chain_str = ",".join(chain)
                if chain_str in other_str and other_pattern.occurrence_count >= pattern.occurrence_count:
                    is_subsumed = True
                    break

            if not is_subsumed:
                result.append(pattern)

        return result

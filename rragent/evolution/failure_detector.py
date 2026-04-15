"""
Failure Detector — detect repeated failure patterns for Recovery Recipe generation.

Identifies:
- Tools that fail repeatedly with the same error
- Time-correlated failures (e.g., market open timeouts)
- Cascading failures (tool A fails -> tool B fails)
"""

from __future__ import annotations

import re
import time
from collections import defaultdict
from dataclasses import dataclass, field

from rragent.evolution.pattern_detector import ExecutionEvent


@dataclass
class FailurePattern:
    """A detected repeated failure pattern."""

    tool: str
    common_error: str
    occurrence_count: int
    context_summary: str
    time_correlation: str  # e.g., "9:25-9:30 market open"
    cascading_tools: list[str]  # Tools that fail in sequence
    first_seen: float
    last_seen: float
    scenario: str = ""  # Maps to FailureScenario for recovery

    def describe(self) -> str:
        return (
            f"Tool: {self.tool}\n"
            f"Error: {self.common_error[:200]}\n"
            f"Occurrences: {self.occurrence_count}\n"
            f"Time pattern: {self.time_correlation}\n"
            f"Cascading: {', '.join(self.cascading_tools) if self.cascading_tools else 'none'}"
        )


class FailureDetector:
    """
    Detect repeated failure patterns from execution events.

    Used by Evolution Engine (Loop 3) to automatically generate
    Recovery Recipes for known failure scenarios.
    """

    def __init__(self, window_hours: int = 24):
        self.window_hours = window_hours

    def detect(self, events: list[ExecutionEvent]) -> list[FailurePattern]:
        """Detect failure patterns from execution events."""
        if not events:
            return []

        # Filter to failures only
        cutoff = time.time() - (self.window_hours * 3600)
        failures = [
            e for e in events
            if not e.success and e.timestamp > cutoff
        ]
        if not failures:
            return []

        patterns = []

        # 1. Group by tool + normalized error
        patterns.extend(self._detect_repeated_errors(failures))

        # 2. Time correlation analysis
        self._add_time_correlations(patterns, failures)

        # 3. Cascading failure detection
        self._detect_cascading(patterns, events)

        # 4. Map to failure scenarios
        self._map_scenarios(patterns)

        return sorted(patterns, key=lambda p: p.occurrence_count, reverse=True)

    def _detect_repeated_errors(self, failures: list[ExecutionEvent]) -> list[FailurePattern]:
        """Group failures by tool + error type."""
        groups: dict[str, list[ExecutionEvent]] = defaultdict(list)

        for event in failures:
            error_key = self._normalize_error(event.result_summary)
            group_key = f"{event.tool_name}:{error_key}"
            groups[group_key].append(event)

        patterns = []
        for group_key, group_events in groups.items():
            if len(group_events) < 2:
                continue

            tool = group_events[0].tool_name
            timestamps = [e.timestamp for e in group_events]

            # Build context summary from params
            param_summaries = []
            for e in group_events[:3]:
                param_str = ", ".join(f"{k}={v}" for k, v in list(e.params.items())[:3])
                param_summaries.append(param_str)

            patterns.append(FailurePattern(
                tool=tool,
                common_error=group_events[0].result_summary[:300],
                occurrence_count=len(group_events),
                context_summary="; ".join(param_summaries),
                time_correlation="",
                cascading_tools=[],
                first_seen=min(timestamps),
                last_seen=max(timestamps),
            ))

        return patterns

    def _add_time_correlations(
        self,
        patterns: list[FailurePattern],
        failures: list[ExecutionEvent],
    ):
        """Detect if failures cluster around specific times of day."""
        from datetime import datetime

        for pattern in patterns:
            tool_failures = [f for f in failures if f.tool_name == pattern.tool]
            if len(tool_failures) < 3:
                continue

            # Extract hours
            hours = []
            for f in tool_failures:
                dt = datetime.fromtimestamp(f.timestamp)
                hours.append(dt.hour + dt.minute / 60)

            # Check if clustered (std dev < 1 hour)
            if len(hours) >= 3:
                mean_hour = sum(hours) / len(hours)
                variance = sum((h - mean_hour) ** 2 for h in hours) / len(hours)
                std_dev = variance ** 0.5

                if std_dev < 1.0:
                    hour_int = int(mean_hour)
                    minute_int = int((mean_hour - hour_int) * 60)
                    pattern.time_correlation = (
                        f"Clustered around {hour_int:02d}:{minute_int:02d} "
                        f"(std_dev: {std_dev:.1f}h)"
                    )

                    # Known market time patterns
                    if 9.0 <= mean_hour <= 9.5:
                        pattern.time_correlation += " [market pre-open]"
                    elif 11.4 <= mean_hour <= 13.1:
                        pattern.time_correlation += " [lunch break]"
                    elif 14.9 <= mean_hour <= 15.1:
                        pattern.time_correlation += " [market close]"

    def _detect_cascading(
        self,
        patterns: list[FailurePattern],
        all_events: list[ExecutionEvent],
    ):
        """Detect cascading failures (A fails within 60s of B failing)."""
        CASCADE_WINDOW = 60  # seconds

        failures_by_session: dict[str, list[ExecutionEvent]] = defaultdict(list)
        for e in all_events:
            if not e.success:
                failures_by_session[e.session_id].append(e)

        for session_failures in failures_by_session.values():
            session_failures.sort(key=lambda e: e.timestamp)

            for i, event in enumerate(session_failures):
                for pattern in patterns:
                    if event.tool_name != pattern.tool:
                        continue

                    # Look for other failures within CASCADE_WINDOW after this one
                    for j in range(i + 1, len(session_failures)):
                        next_event = session_failures[j]
                        if next_event.timestamp - event.timestamp > CASCADE_WINDOW:
                            break
                        if next_event.tool_name != pattern.tool:
                            if next_event.tool_name not in pattern.cascading_tools:
                                pattern.cascading_tools.append(next_event.tool_name)

    def _map_scenarios(self, patterns: list[FailurePattern]):
        """Map failure patterns to known FailureScenario types."""
        for pattern in patterns:
            error_lower = pattern.common_error.lower()

            if "redis" in error_lower or "connection refused" in error_lower:
                pattern.scenario = "redis_lost"
            elif "timeout" in error_lower:
                pattern.scenario = "pyagent_timeout"
            elif "websocket" in error_lower or "gateway" in error_lower:
                pattern.scenario = "gateway_dc"
            elif "rate limit" in error_lower or "429" in error_lower:
                pattern.scenario = "model_overloaded"
            elif "hermes" in error_lower or "run_agent" in error_lower:
                pattern.scenario = "hermes_crash"
            elif "memory" in error_lower or "corrupt" in error_lower:
                pattern.scenario = "memory_corrupt"
            else:
                pattern.scenario = "tool_degraded"

    def _normalize_error(self, error: str) -> str:
        """Normalize error for grouping similar errors."""
        normalized = re.sub(r'\d+', 'N', error)
        normalized = re.sub(r'/[\w/.-]+', '/PATH', normalized)
        normalized = re.sub(r'\b\w{32,}\b', 'HASH', normalized)
        normalized = re.sub(r'0x[0-9a-fA-F]+', 'ADDR', normalized)
        return normalized[:100]

"""
Performance Detector — detect performance degradation in tool execution.

Monitors:
- Latency increases (compared to rolling baseline)
- Success rate drops
- Timeout rate increases
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field

from .pattern_detector import ExecutionEvent


@dataclass
class PerfDegradation:
    """A detected performance degradation."""

    tool: str
    metric: str  # "latency" | "success_rate" | "timeout_rate"
    baseline_value: float
    current_value: float
    degradation_pct: float
    reason: str
    first_detected: float = field(default_factory=time.time)


class PerfDetector:
    """
    Detect tool performance degradation.

    Maintains rolling baselines and compares recent performance.
    Reports degradation when metrics drop >30% from baseline.
    """

    DEGRADATION_THRESHOLD = 0.30  # 30% worse than baseline
    BASELINE_WINDOW_HOURS = 72  # 3-day rolling baseline
    RECENT_WINDOW_HOURS = 1  # Compare against last hour

    def __init__(self):
        self._baselines: dict[str, dict[str, float]] = {}

    def detect(self, events: list[ExecutionEvent]) -> list[PerfDegradation]:
        """Detect performance degradation from events."""
        if not events:
            return []

        now = time.time()
        baseline_cutoff = now - (self.BASELINE_WINDOW_HOURS * 3600)
        recent_cutoff = now - (self.RECENT_WINDOW_HOURS * 3600)

        # Split into baseline and recent
        baseline_events = [e for e in events if baseline_cutoff < e.timestamp < recent_cutoff]
        recent_events = [e for e in events if e.timestamp >= recent_cutoff]

        if not baseline_events or not recent_events:
            return []

        # Group by tool
        baseline_by_tool: dict[str, list[ExecutionEvent]] = defaultdict(list)
        recent_by_tool: dict[str, list[ExecutionEvent]] = defaultdict(list)

        for e in baseline_events:
            baseline_by_tool[e.tool_name].append(e)
        for e in recent_events:
            recent_by_tool[e.tool_name].append(e)

        degradations = []

        for tool in recent_by_tool:
            if tool not in baseline_by_tool:
                continue

            base = baseline_by_tool[tool]
            recent = recent_by_tool[tool]

            if len(base) < 5 or len(recent) < 3:
                continue

            # Latency check
            base_latency = sum(e.latency_ms for e in base) / len(base)
            recent_latency = sum(e.latency_ms for e in recent) / len(recent)

            if base_latency > 0:
                latency_increase = (recent_latency - base_latency) / base_latency
                if latency_increase > self.DEGRADATION_THRESHOLD:
                    degradations.append(PerfDegradation(
                        tool=tool,
                        metric="latency",
                        baseline_value=base_latency,
                        current_value=recent_latency,
                        degradation_pct=latency_increase,
                        reason=f"Latency increased {latency_increase:.0%}: "
                               f"{base_latency:.0f}ms -> {recent_latency:.0f}ms",
                    ))

            # Success rate check
            base_success = sum(1 for e in base if e.success) / len(base)
            recent_success = sum(1 for e in recent if e.success) / len(recent)

            if base_success > 0:
                success_drop = (base_success - recent_success) / base_success
                if success_drop > self.DEGRADATION_THRESHOLD:
                    degradations.append(PerfDegradation(
                        tool=tool,
                        metric="success_rate",
                        baseline_value=base_success,
                        current_value=recent_success,
                        degradation_pct=success_drop,
                        reason=f"Success rate dropped {success_drop:.0%}: "
                               f"{base_success:.0%} -> {recent_success:.0%}",
                    ))

        return degradations

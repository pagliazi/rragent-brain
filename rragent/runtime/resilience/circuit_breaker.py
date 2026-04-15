"""
Circuit Breaker — prevents failure storms.

Inspired by claude-code autoCompact circuit breaker:
3 consecutive failures → stop attempting (saves 250K API calls/day).
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger("rragent.resilience.circuit_breaker")


class CircuitBreaker:
    """
    Generic circuit breaker.

    Applied to:
    - autocompact (3 failures → skip for session)
    - tool execution per-tool (5 failures → mark degraded)
    - evolution engine (3 failures → pause 1 hour)
    - Redis connection (5 failures → degrade to local)
    """

    def __init__(
        self,
        name: str,
        max_failures: int = 3,
        cooldown: float = 0,  # 0 = permanent skip (this session)
    ):
        self.name = name
        self.max_failures = max_failures
        self.cooldown = cooldown
        self.consecutive_failures = 0
        self.tripped_at: float | None = None
        self.total_trips = 0

    def is_open(self) -> bool:
        """True if circuit is open (should NOT attempt operation)."""
        if self.consecutive_failures < self.max_failures:
            return False

        # Check cooldown
        if self.cooldown > 0 and self.tripped_at:
            if time.time() - self.tripped_at >= self.cooldown:
                # Cooldown expired, half-open state
                return False
        return True

    def record_success(self):
        """Reset on success."""
        self.consecutive_failures = 0
        self.tripped_at = None

    def record_failure(self):
        """Increment failure count, trip if threshold reached."""
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.max_failures:
            if self.tripped_at is None:
                self.tripped_at = time.time()
                self.total_trips += 1
                logger.warning(
                    f"Circuit breaker [{self.name}] TRIPPED after "
                    f"{self.consecutive_failures} consecutive failures "
                    f"(total trips: {self.total_trips})"
                )

    def reset(self):
        """Force reset."""
        self.consecutive_failures = 0
        self.tripped_at = None

    def status(self) -> dict:
        if self.is_open():
            state = "open"
        elif self.consecutive_failures > 0:
            state = "half-open"
        else:
            state = "closed"
        return {
            "name": self.name,
            "state": state,
            "failures": self.consecutive_failures,
            "max_failures": self.max_failures,
            "total_trips": self.total_trips,
        }

"""
Evolution Engine — cross-session learning (Loop 3).

Background asyncio task that:
1. Consumes execution events from Redis Stream
2. Runs PatternDetector to find repeated tool chains -> Skill creation
3. Runs FailureDetector to find repeated failures -> Recovery Recipe generation
4. Monitors performance degradation -> health updates

Check interval: 5 minutes (configurable).
Circuit breaker: 3 consecutive failures -> pause 1 hour.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rragent.evolution.correction_tracker import CorrectionTracker
    from rragent.evolution.pattern_detector import PatternDetector, ExecutionEvent
    from rragent.evolution.failure_detector import FailureDetector
    from rragent.evolution.skill_creator import SkillCreator
    from rragent.runtime.resilience.circuit_breaker import CircuitBreaker
    from rragent.runtime.resilience.health_monitor import HealthMonitor

logger = logging.getLogger("rragent.evolution.engine")

STREAM_KEY = "harness:executions"


class EvolutionEngine:
    """
    Cross-session learning engine.

    Runs as a background asyncio task, consuming execution events
    and generating Skills + Recovery Recipes from detected patterns.
    """

    CHECK_INTERVAL = 300  # 5 minutes
    MIN_PATTERN_OCCURRENCES = 3
    MIN_FAILURE_OCCURRENCES = 3

    def __init__(
        self,
        redis_url: str = "redis://127.0.0.1:6379/0",
        pattern_detector: PatternDetector | None = None,
        failure_detector: FailureDetector | None = None,
        skill_creator: SkillCreator | None = None,
        health_monitor: HealthMonitor | None = None,
        circuit_breaker: CircuitBreaker | None = None,
    ):
        self._redis_url = redis_url
        self._redis: Any = None
        self._pattern_detector = pattern_detector
        self._failure_detector = failure_detector
        self._skill_creator = skill_creator
        self._health_monitor = health_monitor

        if circuit_breaker:
            self._breaker = circuit_breaker
        else:
            from rragent.runtime.resilience.circuit_breaker import CircuitBreaker
            self._breaker = CircuitBreaker(
                name="evolution_engine",
                max_failures=3,
                cooldown=3600,  # 1 hour cooldown
            )

        self._last_stream_id = "0-0"
        self._running = False
        self._task: asyncio.Task | None = None
        self._stats = {
            "checks": 0,
            "patterns_found": 0,
            "skills_created": 0,
            "failures_found": 0,
            "recipes_created": 0,
            "errors": 0,
        }

    async def start(self):
        """Start the evolution engine as a background task."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._run_forever())
        logger.info("Evolution Engine started")

    async def stop(self):
        """Stop the evolution engine."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Evolution Engine stopped")

    async def _run_forever(self):
        """Main loop: check for patterns every CHECK_INTERVAL seconds."""
        while self._running:
            try:
                await asyncio.sleep(self.CHECK_INTERVAL)
            except asyncio.CancelledError:
                break

            if self._breaker.is_open():
                logger.debug("Evolution engine circuit breaker is open, skipping")
                continue

            try:
                await self._check_cycle()
                self._breaker.record_success()
                self._stats["checks"] += 1
            except Exception as e:
                self._breaker.record_failure()
                self._stats["errors"] += 1
                logger.error(f"Evolution engine check failed: {e}")

    async def _check_cycle(self):
        """Single check cycle: read events, detect patterns, create skills."""
        events = await self._read_events()
        if not events:
            return

        # 1. Pattern detection -> Skill creation
        if self._pattern_detector and self._skill_creator:
            patterns = self._pattern_detector.detect(events)
            self._stats["patterns_found"] += len(patterns)

            for pattern in patterns:
                if pattern.occurrence_count >= self.MIN_PATTERN_OCCURRENCES:
                    skill = await self._skill_creator.create_from_pattern(pattern)
                    if skill:
                        self._stats["skills_created"] += 1
                        logger.info(
                            f"Evolution: created skill '{skill.name}' "
                            f"from {pattern.occurrence_count}x pattern"
                        )

        # 2. Failure detection -> Recovery Recipes
        if self._failure_detector and self._skill_creator:
            failures = self._failure_detector.detect(events)
            self._stats["failures_found"] += len(failures)

            for failure in failures:
                if failure.occurrence_count >= self.MIN_FAILURE_OCCURRENCES:
                    skill = await self._skill_creator.create_from_failure(failure)
                    if skill:
                        self._stats["recipes_created"] += 1
                        logger.info(
                            f"Evolution: created recovery skill '{skill.name}' "
                            f"from {failure.occurrence_count}x failure"
                        )

        # 3. Performance degradation -> health updates
        if self._health_monitor and self._failure_detector:
            for failure in failures:
                if failure.occurrence_count >= 5:
                    self._health_monitor.report_failure(
                        failure.tool,
                        f"Repeated failure: {failure.common_error[:100]}",
                    )

    async def _read_events(self) -> list[Any]:
        """Read execution events from Redis Stream."""
        if not self._redis:
            try:
                import redis.asyncio as aioredis
                self._redis = aioredis.from_url(self._redis_url)
            except Exception as e:
                logger.warning(f"Cannot connect to Redis for evolution: {e}")
                return []

        try:
            results = await self._redis.xread(
                {STREAM_KEY: self._last_stream_id},
                count=1000,
                block=0,
            )

            if not results:
                return []

            events = []
            for stream_name, messages in results:
                for msg_id, data in messages:
                    self._last_stream_id = msg_id
                    event = self._parse_event(data)
                    if event:
                        events.append(event)

            return events

        except Exception as e:
            logger.debug(f"Redis stream read failed: {e}")
            return []

    def _parse_event(self, data: dict) -> Any | None:
        """Parse a Redis stream message into an ExecutionEvent."""
        try:
            from rragent.evolution.pattern_detector import ExecutionEvent

            # Redis returns bytes, decode
            decoded = {}
            for k, v in data.items():
                key = k.decode() if isinstance(k, bytes) else k
                val = v.decode() if isinstance(v, bytes) else v
                decoded[key] = val

            return ExecutionEvent(
                tool_name=decoded.get("tool", ""),
                action=decoded.get("action", ""),
                params=json.loads(decoded.get("params", "{}")),
                result_summary=decoded.get("result_summary", ""),
                success=decoded.get("success", "true") == "true",
                latency_ms=float(decoded.get("latency_ms", "0")),
                timestamp=float(decoded.get("timestamp", str(time.time()))),
                session_id=decoded.get("session_id", ""),
                corrections=json.loads(decoded.get("corrections", "[]")),
            )
        except Exception as e:
            logger.debug(f"Failed to parse execution event: {e}")
            return None

    async def record_execution(
        self,
        tool_name: str,
        action: str,
        params: dict,
        result_summary: str,
        success: bool,
        latency_ms: float,
        session_id: str = "",
        corrections: list[dict] | None = None,
    ):
        """Record an execution event to Redis Stream for later analysis."""
        if not self._redis:
            try:
                import redis.asyncio as aioredis
                self._redis = aioredis.from_url(self._redis_url)
            except Exception:
                return

        try:
            await self._redis.xadd(
                STREAM_KEY,
                {
                    "tool": tool_name,
                    "action": action,
                    "params": json.dumps(params, ensure_ascii=False),
                    "result_summary": result_summary[:500],
                    "success": "true" if success else "false",
                    "latency_ms": str(latency_ms),
                    "timestamp": str(time.time()),
                    "session_id": session_id,
                    "corrections": json.dumps(corrections or []),
                },
                maxlen=10000,  # Keep last 10K events
            )
        except Exception as e:
            logger.debug(f"Failed to record execution: {e}")

    @property
    def stats(self) -> dict:
        return {
            **self._stats,
            "running": self._running,
            "circuit_breaker": self._breaker.status(),
            "last_stream_id": self._last_stream_id,
        }

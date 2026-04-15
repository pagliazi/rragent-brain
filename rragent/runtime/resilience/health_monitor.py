"""
Health Monitor — tracks component health and routes accordingly.

Checks every 10 seconds:
- Redis: PING
- PyAgent: heartbeat channel
- Gateway: WebSocket ping/pong
- LLM Provider: recent API call latency
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

logger = logging.getLogger("rragent.resilience.health")


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"


@dataclass
class ComponentHealth:
    name: str
    status: HealthStatus = HealthStatus.HEALTHY
    last_heartbeat: float = 0
    consecutive_failures: int = 0
    latency_p99_ms: float = 0
    last_error: str = ""


class RoutingDecision(str, Enum):
    NORMAL = "normal"
    WITH_WARNING = "with_warning"
    UNAVAILABLE = "unavailable"


class HealthMonitor:
    """Monitor component health and influence routing decisions."""

    HEARTBEAT_TIMEOUT = 30  # seconds

    def __init__(self, redis_url: str = "", check_interval: float = 10):
        self.redis_url = redis_url
        self.check_interval = check_interval
        self._components: dict[str, ComponentHealth] = {}
        self._running = False

        # Initialize known components
        for name in ["redis", "gateway", "pyagent", "hermes", "llm_provider"]:
            self._components[name] = ComponentHealth(name=name)

    async def run(self):
        """Background health check loop."""
        self._running = True
        while self._running:
            await self._check_all()
            await asyncio.sleep(self.check_interval)

    async def _check_all(self):
        """Run health checks for all components."""
        # Redis
        await self._check_redis()
        # Other components checked via heartbeat reports
        self._check_heartbeat_timeouts()

    async def _check_redis(self):
        """Check Redis connectivity."""
        comp = self._components["redis"]
        if not self.redis_url:
            return

        try:
            import redis.asyncio as aioredis
            r = aioredis.from_url(self.redis_url, decode_responses=True)
            start = time.time()
            await r.ping()
            latency = (time.time() - start) * 1000
            await r.aclose()

            comp.status = HealthStatus.HEALTHY
            comp.last_heartbeat = time.time()
            comp.consecutive_failures = 0
            comp.latency_p99_ms = latency

        except Exception as e:
            comp.consecutive_failures += 1
            comp.last_error = str(e)
            if comp.consecutive_failures >= 3:
                comp.status = HealthStatus.DOWN
            else:
                comp.status = HealthStatus.DEGRADED

    def _check_heartbeat_timeouts(self):
        """Check if any component hasn't sent a heartbeat recently."""
        now = time.time()
        for name, comp in self._components.items():
            if name == "redis":
                continue  # checked directly
            if comp.last_heartbeat > 0:
                if now - comp.last_heartbeat > self.HEARTBEAT_TIMEOUT:
                    comp.status = HealthStatus.DEGRADED
                    if now - comp.last_heartbeat > self.HEARTBEAT_TIMEOUT * 3:
                        comp.status = HealthStatus.DOWN

    def report_heartbeat(self, component: str):
        """Called when a component reports alive."""
        if component in self._components:
            comp = self._components[component]
            comp.last_heartbeat = time.time()
            comp.consecutive_failures = 0
            comp.status = HealthStatus.HEALTHY

    def report_failure(self, component: str, error: str = ""):
        """Record a failure for a component."""
        if component not in self._components:
            self._components[component] = ComponentHealth(name=component)
        comp = self._components[component]
        comp.consecutive_failures += 1
        comp.last_error = error
        if comp.consecutive_failures >= 5:
            comp.status = HealthStatus.DOWN
        elif comp.consecutive_failures >= 2:
            comp.status = HealthStatus.DEGRADED

    def report_success(self, component: str, latency_ms: float = 0):
        """Record a success for a component."""
        if component not in self._components:
            self._components[component] = ComponentHealth(name=component)
        comp = self._components[component]
        comp.consecutive_failures = 0
        comp.status = HealthStatus.HEALTHY
        comp.last_heartbeat = time.time()
        if latency_ms > 0:
            comp.latency_p99_ms = max(comp.latency_p99_ms * 0.9, latency_ms)

    def mark_degraded(self, component: str, reason: str = ""):
        if component in self._components:
            self._components[component].status = HealthStatus.DEGRADED
            self._components[component].last_error = reason

    def get_status(self, component: str) -> HealthStatus:
        comp = self._components.get(component)
        return comp.status if comp else HealthStatus.HEALTHY

    def get_routing_decision(self, component: str) -> RoutingDecision:
        status = self.get_status(component)
        if status == HealthStatus.HEALTHY:
            return RoutingDecision.NORMAL
        if status == HealthStatus.DEGRADED:
            return RoutingDecision.WITH_WARNING
        return RoutingDecision.UNAVAILABLE

    def get_all_status(self) -> dict[str, dict]:
        return {
            name: {
                "status": comp.status.value,
                "failures": comp.consecutive_failures,
                "latency_ms": round(comp.latency_p99_ms, 1),
                "last_error": comp.last_error,
            }
            for name, comp in self._components.items()
        }

    def stop(self):
        self._running = False

"""
Worker Boot — state machine for multi-agent startup and coordination.

Reference: claw-code Worker Boot state machine.

States:
  INIT -> DISCOVERING -> VALIDATING -> READY -> RUNNING -> SHUTDOWN

Each worker (PyAgent, Hermes, Gateway, Evolution) goes through
this lifecycle independently. The coordinator waits for all
workers to reach READY before entering RUNNING state.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine

logger = logging.getLogger("rragent.workers.boot")


class WorkerState(str, Enum):
    INIT = "init"
    DISCOVERING = "discovering"
    VALIDATING = "validating"
    READY = "ready"
    RUNNING = "running"
    DEGRADED = "degraded"
    SHUTDOWN = "shutdown"
    FAILED = "failed"


@dataclass
class WorkerStatus:
    """Current status of a worker."""

    name: str
    state: WorkerState = WorkerState.INIT
    started_at: float = 0
    ready_at: float = 0
    error: str = ""
    capabilities: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


class Worker:
    """
    A single worker with boot lifecycle.

    Subclass and implement:
    - _discover(): Find and validate dependencies
    - _validate(): Health check before accepting work
    - _run(): Main work loop
    - _shutdown(): Graceful cleanup
    """

    def __init__(self, name: str, required: bool = True):
        self.name = name
        self.required = required  # If False, system can run without it
        self.status = WorkerStatus(name=name)
        self._state_callbacks: list[Callable] = []

    @property
    def state(self) -> WorkerState:
        return self.status.state

    def _set_state(self, state: WorkerState):
        old = self.status.state
        self.status.state = state
        logger.info(f"Worker [{self.name}]: {old.value} -> {state.value}")
        for cb in self._state_callbacks:
            cb(self.name, old, state)

    def on_state_change(self, callback: Callable):
        self._state_callbacks.append(callback)

    async def boot(self) -> bool:
        """Execute the full boot sequence."""
        self.status.started_at = time.time()

        try:
            # DISCOVERING
            self._set_state(WorkerState.DISCOVERING)
            capabilities = await self._discover()
            self.status.capabilities = capabilities

            # VALIDATING
            self._set_state(WorkerState.VALIDATING)
            valid = await self._validate()
            if not valid:
                self._set_state(WorkerState.FAILED)
                return False

            # READY
            self._set_state(WorkerState.READY)
            self.status.ready_at = time.time()
            return True

        except Exception as e:
            self.status.error = str(e)
            self._set_state(WorkerState.FAILED)
            logger.error(f"Worker [{self.name}] boot failed: {e}")
            return False

    async def start(self):
        """Transition from READY to RUNNING."""
        if self.state != WorkerState.READY:
            return
        self._set_state(WorkerState.RUNNING)
        try:
            await self._run()
        except Exception as e:
            self.status.error = str(e)
            self._set_state(WorkerState.DEGRADED)

    async def shutdown(self):
        """Graceful shutdown."""
        self._set_state(WorkerState.SHUTDOWN)
        await self._shutdown()

    async def _discover(self) -> list[str]:
        """Discover dependencies. Return list of capabilities."""
        return []

    async def _validate(self) -> bool:
        """Validate health. Return True if ready."""
        return True

    async def _run(self):
        """Main run logic (called after boot)."""
        pass

    async def _shutdown(self):
        """Cleanup logic."""
        pass


class RedisWorker(Worker):
    """Worker for Redis connectivity."""

    def __init__(self, redis_url: str = "redis://127.0.0.1:6379/0"):
        super().__init__("redis", required=True)
        self.redis_url = redis_url
        self.redis: Any = None

    async def _discover(self) -> list[str]:
        import redis.asyncio as aioredis
        self.redis = aioredis.from_url(self.redis_url)
        return ["pubsub", "streams", "keyvalue"]

    async def _validate(self) -> bool:
        try:
            pong = await self.redis.ping()
            return pong
        except Exception as e:
            self.status.error = f"Redis ping failed: {e}"
            return False

    async def _run(self):
        """Verify Redis is connected via ping, then stay alive."""
        try:
            pong = await self.redis.ping()
            if pong:
                logger.info(f"Worker [redis]: ping OK, connection verified")
                self.status.metadata["last_ping"] = time.time()
            else:
                self.status.error = "Redis ping returned False"
                self._set_state(WorkerState.DEGRADED)
        except Exception as e:
            self.status.error = f"Redis run check failed: {e}"
            self._set_state(WorkerState.DEGRADED)

    async def _shutdown(self):
        if self.redis:
            await self.redis.close()


class PyAgentWorker(Worker):
    """Worker for PyAgent fleet."""

    def __init__(self, redis_url: str = "redis://127.0.0.1:6379/0"):
        super().__init__("pyagent", required=True)
        self.redis_url = redis_url
        self._discovered_agents: list[str] = []

    async def _discover(self) -> list[str]:
        import redis.asyncio as aioredis
        r = aioredis.from_url(self.redis_url)
        try:
            # Check which agents have heartbeats
            agents = []
            agent_names = [
                "market", "dev", "news", "backtest", "monitor",
                "calendar", "mail", "ssh", "browse", "translate",
                "ledger", "screen",
            ]
            for agent in agent_names:
                hb = await r.get(f"agent:{agent}:heartbeat")
                if hb:
                    agents.append(agent)

            self._discovered_agents = agents
            return [f"agent:{a}" for a in agents]
        finally:
            await r.close()

    async def _validate(self) -> bool:
        return len(self._discovered_agents) > 0

    async def _run(self):
        """Check heartbeats in Redis for each discovered agent."""
        import redis.asyncio as aioredis
        r = aioredis.from_url(self.redis_url)
        try:
            alive = []
            stale = []
            now = time.time()
            for agent in self._discovered_agents:
                hb = await r.get(f"agent:{agent}:heartbeat")
                if hb:
                    try:
                        hb_time = float(hb)
                        if now - hb_time < 120:  # 2 min freshness
                            alive.append(agent)
                        else:
                            stale.append(agent)
                    except (ValueError, TypeError):
                        alive.append(agent)  # non-numeric heartbeat = alive
                else:
                    stale.append(agent)
            self.status.metadata["alive_agents"] = alive
            self.status.metadata["stale_agents"] = stale
            logger.info(
                f"Worker [pyagent]: {len(alive)} alive, {len(stale)} stale "
                f"(alive: {alive})"
            )
            if not alive and self._discovered_agents:
                self._set_state(WorkerState.DEGRADED)
        except Exception as e:
            self.status.error = f"PyAgent heartbeat check failed: {e}"
            self._set_state(WorkerState.DEGRADED)
        finally:
            await r.close()


class HermesWorker(Worker):
    """Worker for Hermes runtime."""

    def __init__(self, hermes_path: str = "/opt/hermes-agent"):
        super().__init__("hermes", required=False)
        self.hermes_path = hermes_path
        self._runtime: Any = None

    async def _discover(self) -> list[str]:
        from pathlib import Path
        p = Path(self.hermes_path)
        caps = []
        if (p / "run_agent.py").exists():
            caps.append("agent_loop")
        if (p / "agent" / "tool_registry.py").exists():
            caps.append("tools")
        if (p / "agent" / "skills").exists():
            caps.append("skills")
        return caps

    async def _validate(self) -> bool:
        return "agent_loop" in self.status.capabilities

    def set_runtime(self, runtime: Any):
        """Set HermesNativeRuntime reference for run-time checks."""
        self._runtime = runtime

    async def _run(self):
        """Verify HermesNativeRuntime is loaded and available."""
        if self._runtime is not None:
            available = getattr(self._runtime, "available", False)
            self.status.metadata["runtime_available"] = available
            logger.info(f"Worker [hermes]: runtime available={available}")
            if not available:
                self.status.error = "HermesNativeRuntime not available"
                self._set_state(WorkerState.DEGRADED)
        else:
            # No runtime set, check path only
            from pathlib import Path
            p = Path(self.hermes_path)
            exists = (p / "run_agent.py").exists()
            self.status.metadata["path_exists"] = exists
            logger.info(f"Worker [hermes]: path exists={exists}")
            if not exists:
                self._set_state(WorkerState.DEGRADED)


class GatewayWorker(Worker):
    """Worker for IM Gateway connectivity."""

    def __init__(self, gateway_url: str = "ws://127.0.0.1:18789"):
        super().__init__("gateway", required=True)
        self.gateway_url = gateway_url
        self._channel: Any = None

    async def _discover(self) -> list[str]:
        # Just check URL is configured
        return ["websocket"]

    async def _validate(self) -> bool:
        try:
            import websockets
            async with websockets.connect(
                self.gateway_url,
                close_timeout=5,
                open_timeout=5,
            ) as ws:
                return True
        except Exception as e:
            self.status.error = f"Gateway connection failed: {e}"
            return False

    def set_channel(self, channel: Any):
        """Set GatewayChannel reference for run-time checks."""
        self._channel = channel

    async def _run(self):
        """Verify GatewayChannel is connected."""
        if self._channel is not None:
            connected = getattr(self._channel, "is_connected", False)
            self.status.metadata["channel_connected"] = connected
            logger.info(f"Worker [gateway]: channel connected={connected}")
            if not connected:
                self.status.error = "GatewayChannel not connected"
                self._set_state(WorkerState.DEGRADED)
        else:
            # No channel set, try a quick websocket check
            try:
                import websockets
                async with websockets.connect(
                    self.gateway_url,
                    close_timeout=3,
                    open_timeout=3,
                ) as ws:
                    self.status.metadata["ws_reachable"] = True
                    logger.info("Worker [gateway]: websocket reachable")
            except Exception as e:
                self.status.error = f"Gateway not reachable: {e}"
                self._set_state(WorkerState.DEGRADED)

"""
Worker Coordinator — orchestrate multi-worker boot and lifecycle.

Coordinates boot sequence:
1. Boot all workers concurrently
2. Wait for required workers to reach READY
3. Start all READY workers
4. Monitor health and handle failures
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from rragent.workers.boot import Worker, WorkerState, WorkerStatus

logger = logging.getLogger("rragent.workers.coordinator")


@dataclass
class CoordinatorStatus:
    """Overall system status."""

    state: str = "booting"  # booting | ready | running | degraded | shutdown
    workers: dict[str, WorkerStatus] = field(default_factory=dict)
    boot_started_at: float = 0
    boot_completed_at: float = 0
    degraded_workers: list[str] = field(default_factory=list)


class WorkerCoordinator:
    """
    Coordinate multi-worker lifecycle.

    Boot strategy:
    - All workers boot concurrently
    - Required workers must reach READY within timeout
    - Optional workers that fail boot enter DEGRADED
    - System enters RUNNING only when all required workers are READY

    Runtime monitoring:
    - Periodic health checks
    - Auto-restart on worker failure
    - Graceful degradation notification
    """

    BOOT_TIMEOUT = 30  # seconds

    def __init__(self):
        self._workers: dict[str, Worker] = {}
        self.status = CoordinatorStatus()
        self._health_task: asyncio.Task | None = None

    def register(self, worker: Worker):
        """Register a worker."""
        self._workers[worker.name] = worker
        worker.on_state_change(self._on_worker_state_change)

    async def boot_all(self) -> bool:
        """
        Boot all registered workers concurrently.

        Returns True if all required workers reached READY.
        """
        self.status.state = "booting"
        self.status.boot_started_at = time.time()

        logger.info(f"Booting {len(self._workers)} workers...")

        # Boot all concurrently with timeout
        boot_tasks = {
            name: asyncio.create_task(worker.boot())
            for name, worker in self._workers.items()
        }

        try:
            results = await asyncio.wait_for(
                asyncio.gather(*boot_tasks.values(), return_exceptions=True),
                timeout=self.BOOT_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.error(f"Boot timed out after {self.BOOT_TIMEOUT}s")
            results = []
            for name, task in boot_tasks.items():
                if not task.done():
                    task.cancel()
                    results.append(False)
                else:
                    results.append(task.result())

        # Check required workers
        all_required_ready = True
        for name, worker in self._workers.items():
            self.status.workers[name] = worker.status

            if worker.required and worker.state != WorkerState.READY:
                all_required_ready = False
                logger.error(
                    f"Required worker [{name}] failed to boot: "
                    f"{worker.status.error}"
                )
            elif not worker.required and worker.state == WorkerState.FAILED:
                self.status.degraded_workers.append(name)
                logger.warning(
                    f"Optional worker [{name}] failed, system degraded: "
                    f"{worker.status.error}"
                )

        self.status.boot_completed_at = time.time()
        boot_time = self.status.boot_completed_at - self.status.boot_started_at

        if all_required_ready:
            self.status.state = "ready"
            if self.status.degraded_workers:
                self.status.state = "degraded"
            logger.info(
                f"Boot complete in {boot_time:.1f}s. "
                f"State: {self.status.state}. "
                f"Degraded: {self.status.degraded_workers or 'none'}"
            )
            return True
        else:
            self.status.state = "failed"
            logger.error(f"Boot failed after {boot_time:.1f}s")
            return False

    async def start_all(self):
        """Start all READY workers."""
        self.status.state = "running"

        start_tasks = []
        for name, worker in self._workers.items():
            if worker.state == WorkerState.READY:
                start_tasks.append(worker.start())

        if start_tasks:
            await asyncio.gather(*start_tasks, return_exceptions=True)

        # Start health monitoring
        self._health_task = asyncio.create_task(self._health_loop())

    async def shutdown_all(self):
        """Graceful shutdown of all workers."""
        self.status.state = "shutdown"

        if self._health_task:
            self._health_task.cancel()

        shutdown_tasks = [
            worker.shutdown()
            for worker in self._workers.values()
            if worker.state in (WorkerState.RUNNING, WorkerState.READY, WorkerState.DEGRADED)
        ]

        if shutdown_tasks:
            await asyncio.gather(*shutdown_tasks, return_exceptions=True)

        logger.info("All workers shut down")

    async def _health_loop(self):
        """Periodic health checks for running workers."""
        while True:
            try:
                await asyncio.sleep(30)  # Check every 30s

                for name, worker in self._workers.items():
                    self.status.workers[name] = worker.status

                    if worker.state == WorkerState.DEGRADED and worker.required:
                        logger.warning(
                            f"Required worker [{name}] degraded, "
                            f"attempting restart..."
                        )
                        success = await worker.boot()
                        if success:
                            await worker.start()
                            logger.info(f"Worker [{name}] recovered")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health check error: {e}")

    def _on_worker_state_change(
        self,
        name: str,
        old_state: WorkerState,
        new_state: WorkerState,
    ):
        """Callback when a worker changes state."""
        if name in self._workers:
            self.status.workers[name] = self._workers[name].status

        # Update degraded list
        if new_state == WorkerState.DEGRADED and name not in self.status.degraded_workers:
            self.status.degraded_workers.append(name)
        elif new_state == WorkerState.RUNNING and name in self.status.degraded_workers:
            self.status.degraded_workers.remove(name)

    def get_worker(self, name: str) -> Worker | None:
        return self._workers.get(name)

    @property
    def all_running(self) -> bool:
        return all(
            w.state == WorkerState.RUNNING
            for w in self._workers.values()
            if w.required
        )

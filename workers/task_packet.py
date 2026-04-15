"""
Task Packet — structured task assignment for multi-agent coordination.

Reference: claw-code TaskPacket pattern.

A TaskPacket encapsulates:
- What needs to be done (task description)
- Who should do it (target worker)
- How to verify (acceptance test)
- Resource constraints (timeout, iteration budget)
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class TaskStatus(str, Enum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass
class AcceptanceTest:
    """Defines how to verify task completion."""

    description: str
    check_type: str  # "contains" | "regex" | "callable" | "tool_verify"
    expected: str = ""
    tool_name: str = ""  # For tool_verify type
    tool_params: dict = field(default_factory=dict)


@dataclass
class TaskPacket:
    """
    A structured task for worker assignment.

    Used by Coordinator Mode to distribute work across agents.
    Each packet is self-contained with everything needed to execute
    and verify the task.
    """

    # Identity
    task_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    parent_id: str = ""  # For subtask tracking

    # Task definition
    description: str = ""
    prompt: str = ""  # Full prompt for the worker
    target_worker: str = ""  # "hermes" | "pyagent" | specific agent name
    toolsets: list[str] = field(default_factory=lambda: ["core"])

    # Constraints
    priority: TaskPriority = TaskPriority.NORMAL
    timeout_s: int = 300
    max_iterations: int = 30
    iteration_budget: int = 90

    # Verification
    acceptance_tests: list[AcceptanceTest] = field(default_factory=list)

    # Context
    context: dict = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)  # task_ids that must complete first

    # Status tracking
    status: TaskStatus = TaskStatus.PENDING
    assigned_to: str = ""
    started_at: float = 0
    completed_at: float = 0
    result: Any = None
    error: str = ""

    def mark_assigned(self, worker: str):
        self.status = TaskStatus.ASSIGNED
        self.assigned_to = worker

    def mark_running(self):
        self.status = TaskStatus.RUNNING
        self.started_at = time.time()

    def mark_completed(self, result: Any = None):
        self.status = TaskStatus.COMPLETED
        self.completed_at = time.time()
        self.result = result

    def mark_failed(self, error: str = ""):
        self.status = TaskStatus.FAILED
        self.completed_at = time.time()
        self.error = error

    @property
    def duration_s(self) -> float:
        if self.started_at and self.completed_at:
            return self.completed_at - self.started_at
        return 0

    @property
    def is_terminal(self) -> bool:
        return self.status in (
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.TIMEOUT,
            TaskStatus.CANCELLED,
        )


class TaskQueue:
    """
    Priority queue for task packets.

    Used by Coordinator to manage pending tasks and dispatch
    to available workers.
    """

    def __init__(self):
        self._tasks: dict[str, TaskPacket] = {}
        self._priority_order = {
            TaskPriority.CRITICAL: 0,
            TaskPriority.HIGH: 1,
            TaskPriority.NORMAL: 2,
            TaskPriority.LOW: 3,
        }

    def enqueue(self, task: TaskPacket):
        """Add a task to the queue."""
        self._tasks[task.task_id] = task

    def dequeue(self, worker: str = "") -> TaskPacket | None:
        """
        Get highest priority pending task.

        If worker specified, prefer tasks targeted to that worker.
        """
        pending = [
            t for t in self._tasks.values()
            if t.status == TaskStatus.PENDING
            and self._deps_satisfied(t)
        ]

        if not pending:
            return None

        # Sort by priority, then by target worker match
        def sort_key(t: TaskPacket) -> tuple:
            priority_val = self._priority_order.get(t.priority, 99)
            worker_match = 0 if (worker and t.target_worker == worker) else 1
            return (worker_match, priority_val)

        pending.sort(key=sort_key)
        return pending[0]

    def get(self, task_id: str) -> TaskPacket | None:
        return self._tasks.get(task_id)

    def cancel(self, task_id: str):
        task = self._tasks.get(task_id)
        if task and not task.is_terminal:
            task.status = TaskStatus.CANCELLED
            task.completed_at = time.time()

    def _deps_satisfied(self, task: TaskPacket) -> bool:
        """Check if all dependencies are completed."""
        for dep_id in task.dependencies:
            dep = self._tasks.get(dep_id)
            if not dep or dep.status != TaskStatus.COMPLETED:
                return False
        return True

    @property
    def pending_count(self) -> int:
        return sum(1 for t in self._tasks.values() if t.status == TaskStatus.PENDING)

    @property
    def running_count(self) -> int:
        return sum(1 for t in self._tasks.values() if t.status == TaskStatus.RUNNING)

    def stats(self) -> dict:
        by_status: dict[str, int] = {}
        for t in self._tasks.values():
            by_status[t.status.value] = by_status.get(t.status.value, 0) + 1
        return {
            "total": len(self._tasks),
            "by_status": by_status,
        }

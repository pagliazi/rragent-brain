"""
TaskManager — 长期任务编排 + 进度追踪
支持多步任务定义、Redis 持久化、实时进度推送、暂停/恢复/取消。

使用方式:
1. 创建任务: task_manager.create_task(name, steps=[...])
2. 执行: task_manager.run_task(task_id)  (Orchestrator 内部调度)
3. 查询: task_manager.get_status(task_id)
4. 取消: task_manager.cancel_task(task_id)

每一步的执行结果和进度实时写入 Redis，
进度变化时自动通过 NotifyRouter 推送到系统话题。
"""

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime
from enum import Enum
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger("agent.task_manager")

TASK_KEY_PREFIX = "rragent:task:"
TASK_INDEX_KEY = "rragent:tasks"
TASK_TTL_DAYS = 7


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class TaskStep:
    """单个任务步骤"""

    def __init__(
        self,
        agent: str,
        action: str,
        params: dict | None = None,
        description: str = "",
        timeout: int = 300,
        on_fail: str = "stop",
    ):
        self.agent = agent
        self.action = action
        self.params = params or {}
        self.description = description or f"{agent}.{action}"
        self.timeout = timeout
        self.on_fail = on_fail  # "stop" | "skip" | "retry"
        self.status = StepStatus.PENDING
        self.result: Any = None
        self.error: str = ""
        self.started_at: float = 0
        self.finished_at: float = 0

    def to_dict(self) -> dict:
        return {
            "agent": self.agent,
            "action": self.action,
            "params": self.params,
            "description": self.description,
            "timeout": self.timeout,
            "on_fail": self.on_fail,
            "status": self.status.value,
            "result": self.result if isinstance(self.result, (str, int, float, bool, type(None))) else str(self.result)[:500],
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TaskStep":
        step = cls(
            agent=d["agent"],
            action=d["action"],
            params=d.get("params", {}),
            description=d.get("description", ""),
            timeout=d.get("timeout", 300),
            on_fail=d.get("on_fail", "stop"),
        )
        step.status = StepStatus(d.get("status", "pending"))
        step.result = d.get("result")
        step.error = d.get("error", "")
        step.started_at = d.get("started_at", 0)
        step.finished_at = d.get("finished_at", 0)
        return step


class Task:
    """长期任务"""

    def __init__(self, name: str, steps: list[TaskStep], created_by: str = "user"):
        self.id = f"task_{uuid.uuid4().hex[:8]}"
        self.name = name
        self.steps = steps
        self.status = TaskStatus.PENDING
        self.created_by = created_by
        self.created_at = time.time()
        self.started_at: float = 0
        self.finished_at: float = 0
        self.current_step: int = 0
        self.notify_on_progress = True
        self.retry_count: int = 0
        self.max_retries: int = 2

    @property
    def progress(self) -> float:
        if not self.steps:
            return 0
        done = sum(1 for s in self.steps if s.status in (StepStatus.COMPLETED, StepStatus.SKIPPED))
        return round(done / len(self.steps) * 100, 1)

    @property
    def elapsed(self) -> float:
        if self.started_at == 0:
            return 0
        end = self.finished_at if self.finished_at else time.time()
        return end - self.started_at

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status.value,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "current_step": self.current_step,
            "progress": self.progress,
            "elapsed": self.elapsed,
            "steps": [s.to_dict() for s in self.steps],
            "notify_on_progress": self.notify_on_progress,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Task":
        steps = [TaskStep.from_dict(s) for s in d.get("steps", [])]
        task = cls(name=d["name"], steps=steps, created_by=d.get("created_by", "user"))
        task.id = d["id"]
        task.status = TaskStatus(d.get("status", "pending"))
        task.created_at = d.get("created_at", 0)
        task.started_at = d.get("started_at", 0)
        task.finished_at = d.get("finished_at", 0)
        task.current_step = d.get("current_step", 0)
        task.notify_on_progress = d.get("notify_on_progress", True)
        task.retry_count = d.get("retry_count", 0)
        task.max_retries = d.get("max_retries", 2)
        return task

    def format_status(self) -> str:
        icons = {
            TaskStatus.PENDING: "⏳",
            TaskStatus.RUNNING: "🔄",
            TaskStatus.PAUSED: "⏸️",
            TaskStatus.COMPLETED: "✅",
            TaskStatus.FAILED: "❌",
            TaskStatus.CANCELLED: "🚫",
        }
        step_icons = {
            StepStatus.PENDING: "⬜",
            StepStatus.RUNNING: "🔵",
            StepStatus.COMPLETED: "✅",
            StepStatus.FAILED: "❌",
            StepStatus.SKIPPED: "⏭️",
        }

        lines = [
            f"{icons.get(self.status, '❓')} **{self.name}** [{self.status.value}]",
            f"进度: {self.progress}% ({self.current_step}/{len(self.steps)}) "
            f"| 耗时: {self.elapsed:.0f}s",
            "",
        ]
        for i, step in enumerate(self.steps):
            icon = step_icons.get(step.status, "❓")
            marker = "→ " if i == self.current_step and self.status == TaskStatus.RUNNING else "  "
            elapsed_str = ""
            if step.finished_at and step.started_at:
                elapsed_str = f" ({step.finished_at - step.started_at:.1f}s)"
            error_str = f"\n    ❗ {step.error}" if step.error else ""
            lines.append(f"{marker}{icon} {i + 1}. {step.description}{elapsed_str}{error_str}")

        return "\n".join(lines)


class TaskManager:
    """任务管理器 — 与 Orchestrator 集成"""

    def __init__(self, redis_client: aioredis.Redis):
        self._redis = redis_client
        self._running_tasks: dict[str, asyncio.Task] = {}

    async def create_task(self, name: str, steps: list[dict], created_by: str = "user") -> Task:
        task_steps = []
        for s in steps:
            task_steps.append(TaskStep(
                agent=s["agent"],
                action=s["action"],
                params=s.get("params", {}),
                description=s.get("description", ""),
                timeout=s.get("timeout", 300),
                on_fail=s.get("on_fail", "stop"),
            ))
        task = Task(name=name, steps=task_steps, created_by=created_by)
        await self._save_task(task)
        await self._redis.zadd(TASK_INDEX_KEY, {task.id: task.created_at})
        logger.info(f"Task created: {task.id} ({task.name}, {len(task.steps)} steps)")
        return task

    @staticmethod
    def _build_step_batches(steps: list[TaskStep], start_from: int = 0) -> list[list[int]]:
        """将步骤分为并行批次。同 agent 的步骤不会在同一批次内并行。"""
        n = len(steps)
        batches: list[list[int]] = []
        assigned = set(range(start_from))
        for i in range(start_from):
            assigned.add(i)

        remaining = set(range(start_from, n))
        while remaining:
            batch = []
            agents_in_batch = set()
            for i in sorted(remaining):
                agent = steps[i].agent
                if agent in agents_in_batch:
                    continue
                batch.append(i)
                agents_in_batch.add(agent)
            if not batch:
                batch = [min(remaining)]
            for i in batch:
                remaining.discard(i)
            batches.append(batch)
        return batches

    async def _publish_progress(self, task: "Task", text: str):
        """发布任务进度到 Redis channel 供前端 SSE 消费"""
        channel = f"rragent:task_progress:{task.id}"
        payload = json.dumps({
            "task_id": task.id, "text": text,
            "progress": task.progress, "status": task.status.value,
            "ts": time.time(),
        }, ensure_ascii=False)
        try:
            await self._redis.publish(channel, payload)
        except Exception:
            pass

    async def run_task(self, task_id: str, send_fn=None, notify_fn=None):
        """
        执行任务（支持步骤并行化）。
        send_fn: async (agent, action, params) -> AgentResponse
        notify_fn: async (text, topic, priority) -> None
        """
        task = await self.get_task(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")
        if task.status not in (TaskStatus.PENDING, TaskStatus.PAUSED):
            raise ValueError(f"Task {task_id} is {task.status.value}, cannot run")

        task.status = TaskStatus.RUNNING
        if not task.started_at:
            task.started_at = time.time()
        await self._save_task(task)

        if notify_fn and task.notify_on_progress:
            await notify_fn(
                f"🚀 任务开始: {task.name}\n{len(task.steps)} 个步骤",
                "system", "normal",
            )
        await self._publish_progress(task, f"🚀 任务开始: {task.name}")

        batches = self._build_step_batches(task.steps, start_from=task.current_step)

        for batch in batches:
            task = await self.get_task(task_id)
            if not task or task.status in (TaskStatus.CANCELLED, TaskStatus.PAUSED):
                return

            for i in batch:
                task.steps[i].status = StepStatus.RUNNING
                task.steps[i].started_at = time.time()
            task.current_step = batch[0]
            await self._save_task(task)

            if len(batch) > 1:
                step_labels = ", ".join(task.steps[i].description for i in batch)
                progress_text = f"⚡ [{task.name}] 并行执行: {step_labels}"
            else:
                progress_text = f"🔄 [{task.name}] 步骤 {batch[0]+1}/{len(task.steps)}: {task.steps[batch[0]].description}"

            if notify_fn and task.notify_on_progress:
                await notify_fn(progress_text, "system", "low")
            await self._publish_progress(task, progress_text)

            async def _exec_step(idx: int) -> tuple[int, bool]:
                step = task.steps[idx]
                try:
                    if send_fn is None:
                        raise RuntimeError("send_fn not provided")
                    resp = await asyncio.wait_for(
                        send_fn(step.agent, step.action, step.params),
                        timeout=step.timeout,
                    )
                    if hasattr(resp, "error") and resp.error:
                        raise RuntimeError(resp.error)
                    step.status = StepStatus.COMPLETED
                    step.result = resp.result if hasattr(resp, "result") else str(resp)
                    step.finished_at = time.time()
                    return idx, True
                except asyncio.TimeoutError:
                    step.status = StepStatus.FAILED
                    step.error = f"超时 ({step.timeout}s)"
                    step.finished_at = time.time()
                    return idx, False
                except Exception as e:
                    step.status = StepStatus.FAILED
                    step.error = str(e)[:300]
                    step.finished_at = time.time()
                    return idx, False

            if len(batch) == 1:
                results = [await _exec_step(batch[0])]
            else:
                results = await asyncio.gather(*[_exec_step(i) for i in batch], return_exceptions=True)
                results = [
                    r if not isinstance(r, Exception) else (batch[ri], False)
                    for ri, r in enumerate(results)
                ]

            has_fatal = False
            for idx, ok in results:
                step = task.steps[idx]
                if not ok and step.status == StepStatus.FAILED:
                    if step.on_fail == "skip":
                        step.status = StepStatus.SKIPPED
                    elif step.on_fail == "retry" and task.retry_count < task.max_retries:
                        task.retry_count += 1
                        step.status = StepStatus.PENDING
                        step.error += f" (重试 {task.retry_count}/{task.max_retries})"
                    else:
                        has_fatal = True

                if ok and isinstance(step.result, dict):
                    inject_key = f"_step_{idx}_result"
                    for future_step in task.steps[idx + 1:]:
                        future_step.params[inject_key] = step.result

            await self._save_task(task)
            await self._publish_progress(task, f"批次完成 ({len(batch)} 步)")

            if has_fatal:
                task.status = TaskStatus.FAILED
                task.finished_at = time.time()
                await self._save_task(task)
                fail_desc = "; ".join(
                    f"{task.steps[i].description}: {task.steps[i].error}"
                    for i, ok in results if not ok
                )
                if notify_fn:
                    await notify_fn(
                        f"❌ 任务失败: {task.name}\n{fail_desc}",
                        "system", "high",
                    )
                await self._publish_progress(task, f"❌ 任务失败")
                return

        task.status = TaskStatus.COMPLETED
        task.finished_at = time.time()
        await self._save_task(task)

        if notify_fn and task.notify_on_progress:
            await notify_fn(
                f"✅ 任务完成: {task.name}\n耗时: {task.elapsed:.0f}s | {len(task.steps)} 步全部成功",
                "system", "normal",
            )
        await self._publish_progress(task, f"✅ 任务完成，耗时 {task.elapsed:.0f}s")

    async def get_task(self, task_id: str) -> Task | None:
        raw = await self._redis.get(f"{TASK_KEY_PREFIX}{task_id}")
        if not raw:
            return None
        return Task.from_dict(json.loads(raw))

    async def list_tasks(self, limit: int = 10) -> list[Task]:
        task_ids = await self._redis.zrevrange(TASK_INDEX_KEY, 0, limit - 1)
        tasks = []
        for tid in task_ids:
            task = await self.get_task(tid)
            if task:
                tasks.append(task)
        return tasks

    async def cancel_task(self, task_id: str) -> bool:
        task = await self.get_task(task_id)
        if not task:
            return False
        if task.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
            return False
        task.status = TaskStatus.CANCELLED
        task.finished_at = time.time()
        for step in task.steps:
            if step.status in (StepStatus.PENDING, StepStatus.RUNNING):
                step.status = StepStatus.SKIPPED
        await self._save_task(task)
        if task_id in self._running_tasks:
            self._running_tasks[task_id].cancel()
        return True

    async def pause_task(self, task_id: str) -> bool:
        task = await self.get_task(task_id)
        if not task or task.status != TaskStatus.RUNNING:
            return False
        task.status = TaskStatus.PAUSED
        await self._save_task(task)
        return True

    async def _save_task(self, task: Task):
        data = json.dumps(task.to_dict(), ensure_ascii=False, default=str)
        await self._redis.set(
            f"{TASK_KEY_PREFIX}{task.id}",
            data,
            ex=TASK_TTL_DAYS * 86400,
        )

    async def cleanup_old_tasks(self, max_age_days: int = 7):
        cutoff = time.time() - max_age_days * 86400
        removed = await self._redis.zremrangebyscore(TASK_INDEX_KEY, "-inf", cutoff)
        if removed:
            logger.info(f"Cleaned up {removed} old tasks")


PRESET_TASKS = {
    "morning_prep": {
        "name": "盘前准备",
        "steps": [
            {"agent": "news", "action": "get_news", "params": {"keyword": ""}, "description": "拉取最新新闻"},
            {"agent": "market", "action": "get_all_raw", "description": "获取行情数据"},
            {"agent": "analysis", "action": "ask", "params": {"question": "综合新闻和行情，给出盘前预判"}, "description": "盘前分析"},
            {"agent": "strategist", "action": "ask_strategy", "params": {"question": "结合盘前数据，今日关注方向和风险点"}, "description": "策略预判"},
            {"agent": "orchestrator", "action": "notify", "params": {"topic": "market", "priority": "high"}, "description": "推送盘前简报"},
        ],
    },
    "close_review": {
        "name": "收盘全面复盘",
        "steps": [
            {"agent": "market", "action": "get_all_raw", "description": "获取收盘数据"},
            {"agent": "news", "action": "get_news", "params": {"keyword": ""}, "description": "拉取收盘新闻"},
            {"agent": "analysis", "action": "ask", "params": {"question": "收盘总结：今日整体表现、涨停梯队、板块轮动、情绪判断"}, "description": "市场分析"},
            {"agent": "strategist", "action": "daily_review", "description": "策略师复盘"},
            {"agent": "strategist", "action": "sector_thesis", "description": "板块研判更新"},
            {"agent": "strategist", "action": "risk_alert", "description": "风险扫描"},
            {"agent": "orchestrator", "action": "notify", "params": {"topic": "strategy", "priority": "high"}, "description": "推送复盘报告"},
        ],
    },
    "memory_maintenance": {
        "name": "记忆系统维护",
        "steps": [
            {"agent": "orchestrator", "action": "memory_health", "description": "记忆健康检查"},
            {"agent": "orchestrator", "action": "memory_remind", "description": "跨Agent记忆提醒"},
            {"agent": "orchestrator", "action": "soul_check", "description": "SOUL完整性检查"},
            {"agent": "orchestrator", "action": "memory_hygiene", "description": "记忆卫生报告"},
            {"agent": "orchestrator", "action": "channel_status", "description": "渠道健康状态"},
        ],
    },
    "deep_research": {
        "name": "深度研究",
        "steps": [
            {"agent": "market", "action": "get_all_raw", "description": "获取全量行情"},
            {"agent": "news", "action": "get_news", "params": {"keyword": ""}, "description": "新闻采集"},
            {"agent": "analysis", "action": "ask", "params": {"question": "深度分析当前市场结构和主要矛盾"}, "description": "市场结构分析", "timeout": 600},
            {"agent": "strategist", "action": "ask_strategy", "params": {"question": "结合所有数据给出中期策略建议"}, "description": "中期策略", "timeout": 600},
        ],
    },
    "quant_research": {
        "name": "量化策略研发",
        "steps": [
            {"agent": "market", "action": "get_all_raw", "description": "采集实时行情"},
            {"agent": "news", "action": "get_news", "params": {"keyword": ""}, "description": "采集新闻"},
            {"agent": "orchestrator", "action": "quant", "description": "Alpha→Coder→Backtest→Risk→PM 流水线", "timeout": 600},
            {"agent": "orchestrator", "action": "notify", "params": {"topic": "strategy", "priority": "high"}, "description": "推送研发结果"},
        ],
    },
}

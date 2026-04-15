"""
NotifyRouter — 统一消息路由 + 渠道健康检查 + 故障转移
位于 Orchestrator 与各 Bot (Telegram/Feishu) 之间。

职责:
- 根据渠道心跳存活状态分发消息
- 消息优先级 (critical/high/normal/low)
- 投递确认机制 (critical/high 消息需确认)
- 故障转移: 主渠道离线时切到备用
- pending 消息队列: 所有渠道离线时暂存
"""
from __future__ import annotations


import asyncio
import json
import logging
import time
import uuid
from enum import Enum

import redis.asyncio as aioredis

logger = logging.getLogger("agent.notify_router")

HEARTBEAT_KEY = "rragent:channel_heartbeats"
PENDING_QUEUE_KEY = "rragent:notify:pending"
ACK_CHANNEL = "rragent:notify:ack"
HEARTBEAT_TIMEOUT = 30
ACK_TIMEOUT = 15

CHANNELS = ["telegram", "feishu"]
PRIMARY_CHANNEL = "telegram"


class Priority(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class ChannelState:
    def __init__(self, name: str):
        self.name = name
        self.online = False
        self.degraded = False
        self.last_seen = 0.0
        self.consecutive_failures = 0

    def update(self, hb: dict):
        self.last_seen = hb.get("ts", 0)
        self.consecutive_failures = hb.get("consecutive_failures", 0)
        age = time.time() - self.last_seen
        self.online = age < HEARTBEAT_TIMEOUT
        self.degraded = self.consecutive_failures >= 3

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "online": self.online,
            "degraded": self.degraded,
            "last_seen": self.last_seen,
            "age": round(time.time() - self.last_seen, 1) if self.last_seen else None,
            "consecutive_failures": self.consecutive_failures,
        }


class NotifyRouter:
    """统一消息路由器 — Orchestrator 用此接口推送所有通知"""

    def __init__(self, redis_client: aioredis.Redis):
        self._redis = redis_client
        self._channels: dict[str, ChannelState] = {
            name: ChannelState(name) for name in CHANNELS
        }
        self._pending_acks: dict[str, asyncio.Event] = {}
        self._stats = {
            "total_sent": 0,
            "failovers": 0,
            "pending_queued": 0,
            "ack_received": 0,
            "ack_timeout": 0,
        }

    async def refresh_channel_states(self):
        """从 Redis 心跳数据刷新所有渠道状态"""
        try:
            all_hb = await self._redis.hgetall(HEARTBEAT_KEY)
            for name, state in self._channels.items():
                raw = all_hb.get(name)
                if raw:
                    state.update(json.loads(raw))
                else:
                    state.online = False
                    state.last_seen = 0
        except Exception as e:
            logger.debug(f"Refresh channel states error: {e}")

    def _get_active_channels(self) -> list[str]:
        """按优先级返回活跃渠道列表 (主渠道优先)"""
        active = []
        if self._channels.get(PRIMARY_CHANNEL, ChannelState("")).online:
            active.append(PRIMARY_CHANNEL)
        for name, state in self._channels.items():
            if name != PRIMARY_CHANNEL and state.online:
                active.append(name)
        return active

    async def broadcast(
        self,
        text: str,
        topic: str = "market",
        priority: Priority = Priority.NORMAL,
        source: str = "orchestrator",
    ):
        """
        统一推送接口。

        priority 行为:
        - critical: 广播到所有活跃渠道，需至少一个确认
        - high: 广播到所有活跃渠道
        - normal: 发到主渠道，失败时转备用
        - low: 仅发到主渠道
        """
        await self.refresh_channel_states()
        active = self._get_active_channels()

        msg_id = uuid.uuid4().hex[:12]
        payload = json.dumps({
            "id": msg_id,
            "text": text,
            "topic": topic,
            "priority": priority.value,
            "from": source,
            "timestamp": time.time(),
        }, ensure_ascii=False, default=str)

        if not active:
            logger.warning(f"All channels offline, queuing message (priority={priority.value})")
            await self._queue_pending(payload)
            return

        self._stats["total_sent"] += 1

        if priority in (Priority.CRITICAL, Priority.HIGH):
            for ch_name in active:
                await self._publish_to_channel(ch_name, payload)

            if priority == Priority.CRITICAL:
                acked = await self._wait_ack(msg_id, timeout=ACK_TIMEOUT)
                if not acked:
                    self._stats["ack_timeout"] += 1
                    logger.error(f"CRITICAL message {msg_id} not acked by any channel!")
                    await self._queue_pending(payload)

        elif priority == Priority.NORMAL:
            sent = False
            for ch_name in active:
                try:
                    await self._publish_to_channel(ch_name, payload)
                    sent = True
                    break
                except Exception:
                    self._stats["failovers"] += 1
                    continue
            if not sent:
                await self._queue_pending(payload)

        elif priority == Priority.LOW:
            if active:
                await self._publish_to_channel(active[0], payload)

    async def _publish_to_channel(self, channel_name: str, payload: str):
        redis_channel = f"rragent:notify:{channel_name}"
        await self._redis.publish(redis_channel, payload)

    async def _queue_pending(self, payload: str):
        """将无法投递的消息存入 pending 队列"""
        self._stats["pending_queued"] += 1
        await self._redis.lpush(PENDING_QUEUE_KEY, payload)
        await self._redis.ltrim(PENDING_QUEUE_KEY, 0, 499)

    async def _wait_ack(self, msg_id: str, timeout: float) -> bool:
        """等待投递确认"""
        event = asyncio.Event()
        self._pending_acks[msg_id] = event
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            self._stats["ack_received"] += 1
            return True
        except asyncio.TimeoutError:
            return False
        finally:
            self._pending_acks.pop(msg_id, None)

    async def handle_ack(self, data: dict):
        """处理来自 Bot 的投递确认"""
        msg_id = data.get("msg_id", "")
        event = self._pending_acks.get(msg_id)
        if event:
            event.set()

    async def flush_pending(self):
        """恢复后重发 pending 队列中的消息"""
        await self.refresh_channel_states()
        active = self._get_active_channels()
        if not active:
            return 0

        flushed = 0
        while True:
            raw = await self._redis.rpop(PENDING_QUEUE_KEY)
            if not raw:
                break
            for ch_name in active:
                try:
                    await self._publish_to_channel(ch_name, raw)
                    flushed += 1
                    break
                except Exception:
                    continue
        if flushed:
            logger.info(f"Flushed {flushed} pending messages")
        return flushed

    def get_status(self) -> dict:
        return {
            "channels": {
                name: state.to_dict() for name, state in self._channels.items()
            },
            "primary": PRIMARY_CHANNEL,
            "active": self._get_active_channels(),
            "stats": self._stats.copy(),
        }


_router: NotifyRouter | None = None


async def get_notify_router(redis_client: aioredis.Redis | None = None) -> NotifyRouter:
    global _router
    if _router is None:
        if redis_client is None:
            redis_client = aioredis.from_url(
                "redis://127.0.0.1:6379/0", decode_responses=True
            )
        _router = NotifyRouter(redis_client)
    return _router

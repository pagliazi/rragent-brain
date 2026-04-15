"""
跨 Agent 冗余记忆提醒 (Memory Reminder)
定期扫描各 Agent 新增记忆，检测与其他 Agent 的相关性，
通过 Redis 推送 remind 消息，实现记忆互相提醒。

工作流:
1. 扫描最近 N 小时内各 Agent 新增的记忆节点
2. 对每对 Agent 组合，计算新记忆与对方最近记忆的语义相似度
3. 超过阈值的记忆对，通过 Redis Pub/Sub 推送 remind
4. 接收方 Agent 在下次 recall 时自动纳入 remind 内容
"""

import asyncio
import json
import logging
import os
import time
from datetime import date, timedelta
from typing import Optional

from agents.memory.config import REMINDER_CONFIG, RELATION_TYPES
from agents.memory.embedding import EmbeddingClient
from agents.memory.knowledge_graph import get_shared_graph
from agents.memory.vector_store import VectorStore

logger = logging.getLogger("memory.reminder")

REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
REMIND_CHANNEL_PREFIX = "rragent:memory_remind:"
REMIND_LOG_KEY = "memory:remind_log"


class MemoryReminder:
    """
    冗余记忆提醒引擎
    在 Orchestrator 中以后台循环方式运行。
    """

    def __init__(self):
        self._embedder = EmbeddingClient()
        self._graph = get_shared_graph()
        self._stores: dict[str, VectorStore] = {}
        self._last_scan_ts: float = time.time()
        self._redis = None

    def _get_store(self, agent_name: str) -> VectorStore:
        if agent_name not in self._stores:
            self._stores[agent_name] = VectorStore(f"{agent_name}_memory")
        return self._stores[agent_name]

    async def _get_redis(self):
        if self._redis is None:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(REDIS_URL, decode_responses=True)
        return self._redis

    def _get_recent_memories(self, agent_name: str, since_ts: float) -> list[tuple[str, dict]]:
        """获取某 Agent 在 since_ts 之后新增的记忆节点"""
        results = []
        for node_id, data in self._graph.G.nodes(data=True):
            if data.get("source_agent") != agent_name:
                continue
            created = data.get("created_at", 0)
            if created >= since_ts:
                results.append((node_id, data))
        return results

    async def scan_and_remind(self) -> dict:
        """
        主扫描逻辑：检测跨 Agent 记忆相关性，推送提醒
        返回统计: {"scanned": N, "reminded": M, "pairs": [...]}
        """
        if not REMINDER_CONFIG.get("enabled"):
            return {"scanned": 0, "reminded": 0, "disabled": True}

        pairs = REMINDER_CONFIG.get("cross_agent_pairs", [])
        threshold = REMINDER_CONFIG.get("similarity_threshold", 0.75)
        max_reminders = REMINDER_CONFIG.get("max_reminders_per_cycle", 5)
        since_ts = self._last_scan_ts

        scanned = 0
        reminded = 0
        remind_details = []

        for agent_a, agent_b in pairs:
            new_a = self._get_recent_memories(agent_a, since_ts)
            new_b = self._get_recent_memories(agent_b, since_ts)

            if new_a:
                hits = await self._cross_check(
                    new_a, agent_b, threshold, max_reminders - reminded,
                )
                for hit in hits:
                    await self._push_remind(agent_b, hit)
                    self._establish_remind_edge(hit["source_id"], hit["target_id"])
                    reminded += 1
                    remind_details.append(hit)
                scanned += len(new_a)

            if new_b and reminded < max_reminders:
                hits = await self._cross_check(
                    new_b, agent_a, threshold, max_reminders - reminded,
                )
                for hit in hits:
                    await self._push_remind(agent_a, hit)
                    self._establish_remind_edge(hit["source_id"], hit["target_id"])
                    reminded += 1
                    remind_details.append(hit)
                scanned += len(new_b)

            if reminded >= max_reminders:
                break

        self._last_scan_ts = time.time()

        if reminded > 0:
            await self._log_reminds(remind_details)
            self._graph.save()

        return {"scanned": scanned, "reminded": reminded, "details": remind_details}

    async def _cross_check(
        self,
        source_memories: list[tuple[str, dict]],
        target_agent: str,
        threshold: float,
        max_hits: int,
    ) -> list[dict]:
        """检查 source 记忆与 target agent 最近记忆的相似度"""
        hits = []
        target_store = self._get_store(target_agent)

        for mem_id, mem_data in source_memories:
            if len(hits) >= max_hits:
                break

            source_agent = mem_data.get("source_agent", "")
            source_store = self._get_store(source_agent)
            doc = source_store.get(mem_id)
            if not doc or not doc.get("content"):
                continue

            content = doc["content"]
            embedding = await self._embedder.embed(content[:500])
            if embedding is None:
                continue

            try:
                target_hits = target_store.query(embedding, n=3)
            except Exception:
                continue

            for th in target_hits:
                if th.cosine_sim >= threshold:
                    already_linked = self._graph.G.has_edge(mem_id, th.id) or self._graph.G.has_edge(th.id, mem_id)
                    if already_linked:
                        continue

                    hits.append({
                        "source_id": mem_id,
                        "source_agent": source_agent,
                        "source_preview": content[:150],
                        "target_id": th.id,
                        "target_agent": target_agent,
                        "target_preview": th.content[:150],
                        "similarity": round(th.cosine_sim, 4),
                        "ts": time.time(),
                    })
                    break

        return hits

    def _establish_remind_edge(self, source_id: str, target_id: str):
        """在图谱中建立 reminds 双向关系"""
        self._graph.add_relation(source_id, target_id, "reminds")

    async def _push_remind(self, target_agent: str, hit: dict):
        """通过 Redis 推送 remind 消息给目标 Agent"""
        try:
            r = await self._get_redis()
            channel = f"{REMIND_CHANNEL_PREFIX}{target_agent}"
            await r.publish(channel, json.dumps(hit, ensure_ascii=False))

            remind_key = f"memory:reminds:{target_agent}"
            await r.lpush(remind_key, json.dumps(hit, ensure_ascii=False))
            await r.ltrim(remind_key, 0, 49)
            await r.expire(remind_key, 86400)

            logger.info(
                f"Remind pushed: {hit['source_agent']}→{target_agent} "
                f"sim={hit['similarity']} [{hit['source_preview'][:50]}]"
            )
        except Exception as e:
            logger.warning(f"Push remind failed: {e}")

    async def _log_reminds(self, details: list[dict]):
        """记录 remind 日志到 Redis"""
        try:
            r = await self._get_redis()
            await r.lpush(REMIND_LOG_KEY, json.dumps({
                "ts": time.time(),
                "count": len(details),
                "pairs": [(d["source_agent"], d["target_agent"]) for d in details],
            }))
            await r.ltrim(REMIND_LOG_KEY, 0, 99)
        except Exception:
            pass

    async def get_pending_reminds(self, agent_name: str, max_items: int = 5) -> list[dict]:
        """获取并消费待处理的 remind 消息"""
        try:
            r = await self._get_redis()
            key = f"memory:reminds:{agent_name}"
            items = []
            for _ in range(max_items):
                raw = await r.rpop(key)
                if raw is None:
                    break
                items.append(json.loads(raw))
            return items
        except Exception:
            return []

    def format_reminds_context(self, reminds: list[dict]) -> str:
        """格式化 remind 为可注入 prompt 的上下文"""
        if not reminds:
            return ""
        lines = ["[跨Agent记忆提醒 — 其他Agent发现相关内容]\n"]
        for i, r in enumerate(reminds, 1):
            lines.append(
                f"{i}. [{r.get('source_agent', '?')}] (相似度={r.get('similarity', 0):.2f}): "
                f"{r.get('source_preview', '')[:200]}"
            )
        return "\n".join(lines)


async def run_reminder_loop(interval: int = 0):
    """在 Orchestrator 中以后台循环方式运行"""
    interval = interval or REMINDER_CONFIG.get("interval_seconds", 600)
    reminder = MemoryReminder()
    logger.info(f"Memory reminder loop started (interval={interval}s)")

    while True:
        try:
            result = await reminder.scan_and_remind()
            if result.get("reminded", 0) > 0:
                logger.info(f"Remind cycle: {result}")
        except Exception as e:
            logger.warning(f"Remind cycle error: {e}")
        await asyncio.sleep(interval)

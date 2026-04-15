"""
MemoryMixin — BaseAgent 记忆能力混入
提供 remember / recall / connect / get_timeline / topology_health 接口。
支持跨 Agent 冗余记忆提醒 (remind)。
所有操作容错降级：失败时不阻塞 Agent 主功能。
"""

import logging
import time
import uuid
from datetime import date
from typing import Optional

from agents.memory.config import (
    AGENT_FUSION_WEIGHTS,
    DEFAULT_FUSION_WEIGHTS,
    MEMORY_TTL_DAYS,
    REMINDER_CONFIG,
)
from agents.memory.embedding import EmbeddingClient
from agents.memory.vector_store import VectorStore
from agents.memory.knowledge_graph import get_shared_graph, KnowledgeGraph
from agents.memory.timeline import Timeline, DailyAnchor, parse_date
from agents.memory.retriever import CompositeRetriever, MemoryItem

logger = logging.getLogger("memory.mixin")


class MemoryMixin:
    """
    混入到 BaseAgent 子类中。
    要求宿主类有 self.name: str 属性。
    """

    memory_ttl_days: int = MEMORY_TTL_DAYS
    fusion_weights: Optional[dict] = None

    def _init_memory(self):
        agent_name = getattr(self, "name", "unknown")
        collection = f"{agent_name}_memory"

        self._mem_embedder = EmbeddingClient()
        try:
            self._mem_vector_store = VectorStore(collection)
        except Exception as e:
            logger.error(f"[{agent_name}] VectorStore init failed, memory disabled: {e}")
            self._mem_vector_store = None

        self._mem_graph: KnowledgeGraph = get_shared_graph()
        self._mem_timeline = Timeline(agent_name)
        self._mem_degradation_count = 0

        weights = self.fusion_weights or AGENT_FUSION_WEIGHTS.get(agent_name, DEFAULT_FUSION_WEIGHTS)
        if self._mem_vector_store:
            self._mem_retriever = CompositeRetriever(
                vector_store=self._mem_vector_store,
                graph=self._mem_graph,
                timeline=self._mem_timeline,
                agent_name=agent_name,
                fusion_weights=weights,
            )
        else:
            self._mem_retriever = None

        logger.info(
            f"[{agent_name}] Memory initialized: "
            f"vector={'OK' if self._mem_vector_store else 'DISABLED'}, "
            f"graph={self._mem_graph.G.number_of_nodes()} nodes, "
            f"weights={weights}"
        )

    async def remember(
        self,
        content: str,
        metadata: Optional[dict] = None,
        relations: Optional[list[tuple[str, str]]] = None,
    ) -> Optional[str]:
        """
        存入记忆: L1写向量 + L2建边(共享图谱) + L3更新时序
        失败时静默跳过，返回 None。
        """
        agent_name = getattr(self, "name", "unknown")
        metadata = metadata or {}
        today_str = metadata.get("date", date.today().isoformat())
        mem_id = f"{agent_name}_{today_str.replace('-', '')}_{uuid.uuid4().hex[:6]}"

        try:
            embedding = await self._mem_embedder.embed(content)
            if embedding is None:
                self._on_degradation(agent_name, "embedding_failed")
                return None

            if self._mem_vector_store:
                store_meta = {k: str(v) if not isinstance(v, (str, int, float, bool)) else v
                              for k, v in metadata.items()}
                store_meta["source_agent"] = agent_name
                store_meta["date"] = today_str
                self._mem_vector_store.add(mem_id, embedding, content, store_meta)

            self._mem_graph.add_memory(
                mem_id, source_agent=agent_name, date=today_str,
                mem_type=metadata.get("type", ""), metadata=metadata,
            )

            mem_date = parse_date(today_str)
            self._mem_timeline.append(mem_id, mem_date)

            prev_id = self._mem_timeline.get_previous_mem_id(mem_date)
            if prev_id:
                self._mem_graph.add_relation(prev_id, mem_id, "temporal_next")

            if relations:
                for target_id, rel_type in relations:
                    self._mem_graph.add_relation(mem_id, target_id, rel_type)

            self._mem_graph.save()
            self._mem_degradation_count = 0
            return mem_id

        except Exception as e:
            logger.warning(f"[{agent_name}] remember failed, skipped: {e}")
            self._on_degradation(agent_name, str(e))
            return None

    async def recall(
        self,
        query: str,
        top_k: int = 5,
        time_range_days: Optional[int] = None,
        relation_filter: Optional[str] = None,
        cross_agent: bool = False,
    ) -> list[MemoryItem]:
        """
        三层融合检索。
        cross_agent=True 时沿共享图谱扩展到其他 Agent 的记忆。
        失败时返回空列表。
        """
        agent_name = getattr(self, "name", "unknown")
        try:
            if not self._mem_retriever:
                return []

            embedding = await self._mem_embedder.embed(query)
            if embedding is None:
                return []

            return self._mem_retriever.recall(
                query_embedding=embedding,
                top_k=top_k,
                time_range_days=time_range_days,
                relation_filter=relation_filter,
                cross_agent=cross_agent,
            )
        except Exception as e:
            logger.warning(f"[{agent_name}] recall failed, returning empty: {e}")
            return []

    async def connect(self, mem_id_a: str, mem_id_b: str, rel: str):
        """在共享图谱中建立关系"""
        try:
            self._mem_graph.add_relation(mem_id_a, mem_id_b, rel)
            self._mem_graph.save()
        except Exception as e:
            logger.warning(f"connect failed: {e}")

    async def get_timeline(self, days: int = 7) -> list[DailyAnchor]:
        return self._mem_timeline.get_recent(days)

    async def topology_health(self) -> dict:
        return self._mem_graph.health_report()

    async def get_reminds(self, max_items: int = 3) -> list[dict]:
        """获取来自其他 Agent 的冗余记忆提醒"""
        if not REMINDER_CONFIG.get("enabled"):
            return []
        try:
            from agents.memory.reminder import MemoryReminder
            reminder = MemoryReminder()
            return await reminder.get_pending_reminds(
                getattr(self, "name", "unknown"), max_items=max_items,
            )
        except Exception as e:
            logger.debug(f"Get reminds failed: {e}")
            return []

    def format_recall_context(self, memories: list[MemoryItem], max_items: int = 3,
                              reminds: Optional[list[dict]] = None) -> str:
        """将 recall 结果 + remind 提醒格式化为可注入 prompt 的上下文文本"""
        if not memories and not reminds:
            return ""
        lines = []
        if memories:
            lines.append("[历史记忆参考 — 基于拓扑相似度排序]\n")
            for i, mem in enumerate(memories[:max_items], 1):
                date_str = mem.metadata.get("date", "?")
                score = f"{mem.score:.2f}"
                src = f" ({mem.source_agent})" if mem.source_agent != getattr(self, "name", "") else ""
                snippet = mem.content[:300].replace("\n", " ")
                lines.append(f"{i}. [{date_str}] (score={score}){src}: {snippet}")
        if reminds:
            lines.append("\n[跨Agent记忆提醒 — 其他Agent发现相关内容]\n")
            for i, r in enumerate(reminds, 1):
                lines.append(
                    f"  💡 [{r.get('source_agent', '?')}] "
                    f"(相似度={r.get('similarity', 0):.2f}): "
                    f"{r.get('source_preview', '')[:200]}"
                )
        return "\n".join(lines)

    def _on_degradation(self, agent_name: str, reason: str):
        self._mem_degradation_count += 1
        logger.warning(f"[{agent_name}] Memory degradation #{self._mem_degradation_count}: {reason}")
        if self._mem_degradation_count >= 3:
            self._report_degradation_to_redis(agent_name, reason)

    def _report_degradation_to_redis(self, agent_name: str, reason: str):
        """连续 3 次降级时写入 Redis 告警（异步安全）"""
        try:
            import asyncio
            import json
            import redis.asyncio as aioredis
            from agents.memory.config import OLLAMA_BASE_URL
            import os

            async def _report():
                r = aioredis.from_url(os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"), decode_responses=True)
                await r.lpush("memory:degradation_log", json.dumps({
                    "agent": agent_name,
                    "reason": reason,
                    "count": self._mem_degradation_count,
                    "ts": time.time(),
                }))
                await r.ltrim("memory:degradation_log", 0, 99)
                await r.aclose()

            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(_report())
            else:
                loop.run_until_complete(_report())
        except Exception:
            pass

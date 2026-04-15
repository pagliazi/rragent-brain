"""
融合排序器 — 三层 recall 合一
L1 向量候选 + L2 图谱扩展 + L3 时序加权 → 最终 top_k
"""

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from agents.memory.config import DEFAULT_FUSION_WEIGHTS, AGENT_FUSION_WEIGHTS
from agents.memory.vector_store import VectorStore, VectorHit
from agents.memory.knowledge_graph import KnowledgeGraph
from agents.memory.timeline import Timeline, time_decay, parse_date

logger = logging.getLogger("memory.retriever")


@dataclass
class MemoryItem:
    id: str
    content: str
    metadata: dict = field(default_factory=dict)
    score: float = 0.0
    source_agent: str = ""
    cosine_sim: float = 0.0
    graph_proximity: float = 0.0
    time_weight: float = 0.0


class CompositeRetriever:
    def __init__(
        self,
        vector_store: VectorStore,
        graph: KnowledgeGraph,
        timeline: Timeline,
        agent_name: str,
        fusion_weights: Optional[dict] = None,
    ):
        self.vector_store = vector_store
        self.graph = graph
        self.timeline = timeline
        self.agent_name = agent_name
        self.weights = fusion_weights or AGENT_FUSION_WEIGHTS.get(agent_name, DEFAULT_FUSION_WEIGHTS)

    def recall(
        self,
        query_embedding: list[float],
        top_k: int = 5,
        time_range_days: Optional[int] = None,
        relation_filter: Optional[str] = None,
        cross_agent: bool = False,
    ) -> list[MemoryItem]:

        vector_hits = self.vector_store.query(query_embedding, n=top_k * 3)
        if not vector_hits:
            return []

        hit_map: dict[str, MemoryItem] = {}
        for vh in vector_hits:
            hit_map[vh.id] = MemoryItem(
                id=vh.id,
                content=vh.content,
                metadata=vh.metadata,
                cosine_sim=vh.cosine_sim,
                source_agent=self.agent_name,
            )

        expanded_ids = set()
        for vh in vector_hits[:top_k]:
            neighbors = self.graph.get_neighbors(vh.id, depth=2, rel_filter=relation_filter)
            for nid in neighbors:
                node_data = self.graph.get_node(nid)
                if not node_data:
                    continue
                src = node_data.get("source_agent", "")
                if not cross_agent and src != self.agent_name:
                    continue
                if nid not in hit_map:
                    expanded_ids.add(nid)

        for nid in expanded_ids:
            node_data = self.graph.get_node(nid)
            if not node_data:
                continue
            src = node_data.get("source_agent", "")
            content = self._fetch_content_from_other_store(nid, src) if src != self.agent_name else ""
            if not content:
                doc = self.vector_store.get(nid)
                content = doc["content"] if doc else ""
            hit_map[nid] = MemoryItem(
                id=nid,
                content=content,
                metadata=node_data,
                cosine_sim=0.0,
                source_agent=src,
            )

        today = date.today()
        for item in hit_map.values():
            date_str = item.metadata.get("date", "")
            if date_str:
                try:
                    mem_date = parse_date(date_str)
                    item.time_weight = time_decay(mem_date, today)
                except ValueError:
                    item.time_weight = 0.5
            else:
                item.time_weight = 0.5

            ref_hit = next((vh for vh in vector_hits if vh.id == item.id), None)
            if ref_hit:
                hops = 0
            else:
                hops = self.graph.shortest_path_length(
                    vector_hits[0].id if vector_hits else "", item.id
                )
            item.graph_proximity = 1.0 / max(hops, 1) if hops >= 0 else 0.0

            item.score = (
                self.weights["vector"] * item.cosine_sim
                + self.weights["graph"] * item.graph_proximity
                + self.weights["time"] * item.time_weight
            )

        if time_range_days:
            cutoff_ids = set(self.timeline.get_mem_ids_in_range(time_range_days))
            hit_map = {k: v for k, v in hit_map.items() if k in cutoff_ids or v.source_agent != self.agent_name}

        ranked = sorted(hit_map.values(), key=lambda m: m.score, reverse=True)
        return ranked[:top_k]

    def _fetch_content_from_other_store(self, mem_id: str, agent_name: str) -> str:
        """尝试从其他 Agent 的 VectorStore 取内容"""
        try:
            other_store = VectorStore(f"{agent_name}_memory")
            doc = other_store.get(mem_id)
            return doc["content"] if doc else ""
        except Exception:
            return ""

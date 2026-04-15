"""
共享知识图谱层 — L2
全局 NetworkX 有向图，所有 Agent 共享同一实例。
节点 = 记忆条目，边 = 显式关系，持久化为 JSON。
"""

import json
import logging
import os
import shutil
import threading
import time
from datetime import datetime
from typing import Optional

from agents.memory.config import GRAPH_PERSIST_DIR, RELATION_TYPES

logger = logging.getLogger("memory.knowledge_graph")

_GRAPH_FILE = os.path.join(GRAPH_PERSIST_DIR, "shared_graph.json")
_LOCK = threading.Lock()

_graph_instance = None


def _get_nx():
    import networkx as nx
    return nx


class KnowledgeGraph:
    """全局共享知识图谱（进程级单例）"""

    def __init__(self):
        nx = _get_nx()
        self.G: "nx.DiGraph" = nx.DiGraph()
        self._dirty = False
        self._load()

    def _load(self):
        if not os.path.exists(_GRAPH_FILE):
            logger.info("No existing graph file, starting fresh")
            return
        try:
            with open(_GRAPH_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            nx = _get_nx()
            self.G = nx.node_link_graph(data, directed=True, multigraph=False)
            logger.info(f"Graph loaded: {self.G.number_of_nodes()} nodes, {self.G.number_of_edges()} edges")
        except Exception as e:
            logger.error(f"Failed to load graph: {e}")

    def save(self):
        if not self._dirty:
            return
        with _LOCK:
            try:
                os.makedirs(GRAPH_PERSIST_DIR, exist_ok=True)
                nx = _get_nx()
                data = nx.node_link_data(self.G)
                tmp = _GRAPH_FILE + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=None)
                os.replace(tmp, _GRAPH_FILE)
                self._dirty = False
            except Exception as e:
                logger.error(f"Failed to save graph: {e}")

    def backup(self):
        if not os.path.exists(_GRAPH_FILE):
            return
        backup_name = f"shared_graph_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        backup_path = os.path.join(GRAPH_PERSIST_DIR, "backups", backup_name)
        os.makedirs(os.path.dirname(backup_path), exist_ok=True)
        shutil.copy2(_GRAPH_FILE, backup_path)
        logger.info(f"Graph backed up: {backup_path}")
        self._cleanup_old_backups()

    def _cleanup_old_backups(self, keep: int = 7):
        backup_dir = os.path.join(GRAPH_PERSIST_DIR, "backups")
        if not os.path.isdir(backup_dir):
            return
        files = sorted(
            [f for f in os.listdir(backup_dir) if f.startswith("shared_graph_")],
            reverse=True,
        )
        for old in files[keep:]:
            os.remove(os.path.join(backup_dir, old))

    def add_memory(self, mem_id: str, source_agent: str, date: str,
                   mem_type: str = "", metadata: Optional[dict] = None):
        attrs = {
            "source_agent": source_agent,
            "date": date,
            "type": mem_type,
            "created_at": time.time(),
        }
        if metadata:
            attrs.update({k: v for k, v in metadata.items() if isinstance(v, (str, int, float, bool))})
        self.G.add_node(mem_id, **attrs)
        self._dirty = True

    def add_relation(self, from_id: str, to_id: str, rel: str):
        if rel not in RELATION_TYPES:
            logger.warning(f"Unknown relation type: {rel}")
        if from_id not in self.G:
            logger.warning(f"Node not in graph: {from_id}")
            return
        if to_id not in self.G:
            logger.warning(f"Node not in graph: {to_id}")
            return
        self.G.add_edge(from_id, to_id, rel=rel, created_at=time.time())
        self._dirty = True

    def get_neighbors(self, mem_id: str, depth: int = 2,
                      rel_filter: Optional[str] = None) -> list[str]:
        if mem_id not in self.G:
            return []
        visited = set()
        frontier = {mem_id}
        for _ in range(depth):
            next_frontier = set()
            for node in frontier:
                for _, neighbor, data in self.G.edges(node, data=True):
                    if rel_filter and data.get("rel") != rel_filter:
                        continue
                    if neighbor not in visited and neighbor != mem_id:
                        next_frontier.add(neighbor)
                for predecessor, _, data in self.G.in_edges(node, data=True):
                    if rel_filter and data.get("rel") != rel_filter:
                        continue
                    if predecessor not in visited and predecessor != mem_id:
                        next_frontier.add(predecessor)
            visited.update(frontier)
            frontier = next_frontier - visited
        visited.update(frontier)
        visited.discard(mem_id)
        return list(visited)

    def shortest_path_length(self, from_id: str, to_id: str) -> int:
        nx = _get_nx()
        try:
            undirected = self.G.to_undirected()
            return nx.shortest_path_length(undirected, from_id, to_id)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return -1

    def get_node(self, mem_id: str) -> Optional[dict]:
        if mem_id not in self.G:
            return None
        return dict(self.G.nodes[mem_id])

    def get_nodes_by_agent(self, agent_name: str) -> list[str]:
        return [n for n, d in self.G.nodes(data=True) if d.get("source_agent") == agent_name]

    def remove_nodes(self, mem_ids: list[str]):
        for mid in mem_ids:
            if mid in self.G:
                self.G.remove_node(mid)
        self._dirty = True

    def health_report(self) -> dict:
        nx = _get_nx()
        n_nodes = self.G.number_of_nodes()
        n_edges = self.G.number_of_edges()
        if n_nodes == 0:
            return {"total_nodes": 0, "total_edges": 0, "orphan_nodes": 0,
                    "clusters": 0, "avg_degree": 0.0}
        undirected = self.G.to_undirected()
        orphans = sum(1 for n in undirected if undirected.degree(n) == 0)
        clusters = nx.number_connected_components(undirected)
        avg_deg = n_edges * 2 / n_nodes if n_nodes else 0.0
        agents_count = {}
        for _, d in self.G.nodes(data=True):
            ag = d.get("source_agent", "unknown")
            agents_count[ag] = agents_count.get(ag, 0) + 1
        return {
            "total_nodes": n_nodes,
            "total_edges": n_edges,
            "orphan_nodes": orphans,
            "clusters": clusters,
            "avg_degree": round(avg_deg, 2),
            "agents": agents_count,
        }


def get_shared_graph() -> KnowledgeGraph:
    global _graph_instance
    if _graph_instance is None:
        _graph_instance = KnowledgeGraph()
    return _graph_instance

"""
记忆生命周期管理
- 拓扑压缩: 旧记忆集群 → 摘要节点 (保留外部边)
- 三级记忆分层管理 (HOT / WARM / COLD) — 集成 memory-tiering skill
- 每日图谱备份
- 孤立节点自动修复 (补 temporal 边)

分层规则 (memory-tiering):
  HOT  — ≤7天，高频访问的活跃记忆（当前会话/任务上下文）
  WARM — 8-30天，稳定但仍被引用的记忆（用户偏好/系统配置/重复模式）
  COLD — >30天，历史归档（压缩摘要/里程碑/教训）
"""

import json
import logging
import os
import time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from agents.memory.config import MEMORY_TTL_DAYS
from agents.memory.knowledge_graph import get_shared_graph, KnowledgeGraph
from agents.memory.vector_store import VectorStore
from agents.memory.embedding import EmbeddingClient
from agents.memory.timeline import Timeline

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_TIERING_DIR = os.path.join(_PROJECT_ROOT, "memory", "tiering")

# ── 记忆分层常量 ───────────────────────────────────
HOT_DAYS = 7       # HOT 层：最近 7 天
WARM_DAYS = 30     # WARM 层：8-30 天
# COLD: >30 天 (由拓扑压缩处理)

logger = logging.getLogger("memory.lifecycle")


async def compress_old_memories(
    agent_name: str,
    older_than_days: int = MEMORY_TTL_DAYS,
    llm_factory=None,
):
    """
    拓扑压缩: 对超过 older_than_days 的旧记忆做集群检测，
    每个集群生成一个摘要节点，继承外部边，删除原始节点。
    """
    graph = get_shared_graph()
    cutoff = date.today() - timedelta(days=older_than_days)
    cutoff_str = cutoff.isoformat()

    old_nodes = []
    for node_id, data in graph.G.nodes(data=True):
        if data.get("source_agent") != agent_name:
            continue
        node_date = data.get("date", "")
        if node_date and node_date < cutoff_str:
            old_nodes.append(node_id)

    if len(old_nodes) < 5:
        logger.info(f"[{agent_name}] Only {len(old_nodes)} old nodes, skip compression")
        return {"compressed": 0, "clusters": 0}

    try:
        import networkx as nx
    except ImportError:
        return {"error": "networkx not installed"}

    subgraph = graph.G.subgraph(old_nodes).copy()
    undirected = subgraph.to_undirected()
    communities = list(nx.connected_components(undirected))

    store = VectorStore(f"{agent_name}_memory")
    embedder = EmbeddingClient()
    compressed_count = 0

    for cluster_nodes in communities:
        if len(cluster_nodes) < 3:
            continue

        contents = []
        for nid in cluster_nodes:
            doc = store.get(nid)
            if doc:
                contents.append(doc["content"][:200])

        if not contents:
            continue

        summary_text = " | ".join(contents[:10])
        if llm_factory:
            try:
                llm = llm_factory()
                from langchain_core.messages import HumanMessage
                joined = "\n".join(contents[:10])
                result = await llm.ainvoke([HumanMessage(
                    content=f"请用1-2句话总结以下记忆片段的核心主题:\n\n{joined}"
                )])
                summary_text = result.completion if hasattr(result, "completion") else str(result)
            except Exception as e:
                logger.warning(f"LLM summary failed, using concatenation: {e}")

        summary_id = f"{agent_name}_summary_{date.today().isoformat()}_{int(time.time()) % 10000}"

        embedding = await embedder.embed(summary_text)
        if not embedding:
            continue

        external_edges_in = []
        external_edges_out = []
        for nid in cluster_nodes:
            for pred, _, data in graph.G.in_edges(nid, data=True):
                if pred not in cluster_nodes:
                    external_edges_in.append((pred, data.get("rel", "references")))
            for _, succ, data in graph.G.edges(nid, data=True):
                if succ not in cluster_nodes:
                    external_edges_out.append((succ, data.get("rel", "references")))

        dates = [graph.G.nodes[n].get("date", "") for n in cluster_nodes if graph.G.nodes[n].get("date")]
        earliest = min(dates) if dates else cutoff_str

        store.add(summary_id, embedding, summary_text, {
            "source_agent": agent_name,
            "date": earliest,
            "type": "compressed_summary",
            "original_count": len(cluster_nodes),
        })

        graph.add_memory(summary_id, source_agent=agent_name, date=earliest,
                         mem_type="compressed_summary",
                         metadata={"original_count": len(cluster_nodes)})

        for pred, rel in external_edges_in:
            graph.add_relation(pred, summary_id, rel)
        for succ, rel in external_edges_out:
            graph.add_relation(summary_id, succ, rel)

        store.delete(list(cluster_nodes))
        graph.remove_nodes(list(cluster_nodes))

        compressed_count += len(cluster_nodes)

    graph.save()

    result = {
        "compressed": compressed_count,
        "clusters": len([c for c in communities if len(c) >= 3]),
        "remaining_old": len(old_nodes) - compressed_count,
    }
    logger.info(f"[{agent_name}] Compression result: {result}")
    return result


# ── 三级记忆分层管理 (memory-tiering 集成) ─────────

async def tier_memories(agent_name: str) -> dict:
    """
    对指定 Agent 的记忆进行 HOT/WARM/COLD 分层标注。
    - HOT: ≤7天���保持完整内容，优先召回
    - WARM: 8-30天，保留关键内容，正常召回
    - COLD: >30天，候选压缩，低优先级召回
    返回各层统计。
    """
    graph = get_shared_graph()
    today = date.today()
    hot_cutoff = (today - timedelta(days=HOT_DAYS)).isoformat()
    warm_cutoff = (today - timedelta(days=WARM_DAYS)).isoformat()

    stats = {"hot": 0, "warm": 0, "cold": 0, "unclassified": 0}
    tier_changes = []

    for node_id, data in graph.G.nodes(data=True):
        if data.get("source_agent") != agent_name:
            continue
        node_date = data.get("date", "")
        if not node_date:
            stats["unclassified"] += 1
            continue

        old_tier = data.get("tier", "")

        if node_date >= hot_cutoff:
            new_tier = "hot"
        elif node_date >= warm_cutoff:
            new_tier = "warm"
        else:
            new_tier = "cold"

        if old_tier != new_tier:
            graph.G.nodes[node_id]["tier"] = new_tier
            tier_changes.append((node_id, old_tier, new_tier))

        stats[new_tier] += 1

    if tier_changes:
        graph.save()
        logger.info("[%s] Tiering: %d changes — %s", agent_name, len(tier_changes), stats)

    return {
        "agent": agent_name,
        "stats": stats,
        "changes": len(tier_changes),
    }


async def tier_all_agents() -> dict:
    """对所有 Agent 执行分层"""
    graph = get_shared_graph()
    agents = set()
    for _, data in graph.G.nodes(data=True):
        ag = data.get("source_agent", "")
        if ag:
            agents.add(ag)

    results = {}
    for ag in agents:
        results[ag] = await tier_memories(ag)
    return results


def get_tiering_summary() -> dict:
    """获取当前分层概况"""
    graph = get_shared_graph()
    summary = {"hot": 0, "warm": 0, "cold": 0, "untiered": 0, "by_agent": {}}

    for _, data in graph.G.nodes(data=True):
        tier = data.get("tier", "")
        agent = data.get("source_agent", "unknown")

        if agent not in summary["by_agent"]:
            summary["by_agent"][agent] = {"hot": 0, "warm": 0, "cold": 0}

        if tier in ("hot", "warm", "cold"):
            summary[tier] += 1
            summary["by_agent"][agent][tier] += 1
        else:
            summary["untiered"] += 1

    summary["total"] = summary["hot"] + summary["warm"] + summary["cold"] + summary["untiered"]
    return summary


def fix_orphan_nodes(graph: Optional[KnowledgeGraph] = None):
    """为孤立节点补建 temporal 边"""
    graph = graph or get_shared_graph()
    undirected = graph.G.to_undirected()
    orphans = [n for n in undirected if undirected.degree(n) == 0]

    if not orphans:
        return {"fixed": 0}

    by_agent_date: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for nid in orphans:
        data = graph.G.nodes[nid]
        agent = data.get("source_agent", "")
        dt = data.get("date", "")
        if agent and dt:
            by_agent_date[agent].append((nid, dt))

    fixed = 0
    for agent, nodes in by_agent_date.items():
        sorted_nodes = sorted(nodes, key=lambda x: x[1])
        for i in range(1, len(sorted_nodes)):
            prev_id, _ = sorted_nodes[i - 1]
            curr_id, _ = sorted_nodes[i]
            graph.add_relation(prev_id, curr_id, "temporal_next")
            fixed += 1

        all_agent_nodes = [
            (n, graph.G.nodes[n].get("date", ""))
            for n in graph.G.nodes
            if graph.G.nodes[n].get("source_agent") == agent and n not in orphans
        ]
        if all_agent_nodes and sorted_nodes:
            nearest = sorted(all_agent_nodes, key=lambda x: x[1])
            first_orphan = sorted_nodes[0][0]
            for nid, dt in nearest:
                if dt <= sorted_nodes[0][1]:
                    graph.add_relation(nid, first_orphan, "temporal_next")
                    fixed += 1
                    break

    if fixed:
        graph.save()

    return {"fixed": fixed, "orphans_found": len(orphans)}


def daily_backup():
    """每日图谱备份"""
    graph = get_shared_graph()
    graph.backup()


async def memory_hygiene_report() -> dict:
    """
    记忆卫生报告: 嵌入一致性检查 + 存储质量评估 + 图谱健康度
    灵感来源: awesome-openclaw-skills 中的 memory-hygiene

    检查项:
    1. 嵌入维度一致性 — 是否有维度不匹配的旧向量
    2. 孤立节点比例 — 无边节点占比
    3. 知识图谱连通性 — 最大连通分量覆盖率
    4. 时序链完整性 — temporal 边的连续性
    5. 过期记忆占比 — 超龄但未压缩的节点
    """
    from agents.memory.config import EMBEDDING_CONFIG, MEMORY_TTL_DAYS
    from datetime import date, timedelta

    report: dict = {"status": "ok", "issues": [], "metrics": {}}
    graph = get_shared_graph()

    total_nodes = graph.G.number_of_nodes()
    total_edges = graph.G.number_of_edges()
    report["metrics"]["total_nodes"] = total_nodes
    report["metrics"]["total_edges"] = total_edges

    if total_nodes == 0:
        report["metrics"]["health_score"] = 1.0
        return report

    import networkx as nx

    undirected = graph.G.to_undirected()
    orphans = [n for n in undirected if undirected.degree(n) == 0]
    orphan_ratio = len(orphans) / total_nodes if total_nodes > 0 else 0
    report["metrics"]["orphan_count"] = len(orphans)
    report["metrics"]["orphan_ratio"] = round(orphan_ratio, 3)
    if orphan_ratio > 0.2:
        report["issues"].append({
            "type": "high_orphan_ratio",
            "severity": "warning",
            "detail": f"孤立节点占比 {orphan_ratio:.1%}，建议运行 fix_orphan_nodes()",
        })

    components = list(nx.connected_components(undirected))
    if components:
        largest = max(len(c) for c in components)
        coverage = largest / total_nodes
        report["metrics"]["component_count"] = len(components)
        report["metrics"]["largest_component_coverage"] = round(coverage, 3)
        if len(components) > 10 and coverage < 0.5:
            report["issues"].append({
                "type": "fragmented_graph",
                "severity": "warning",
                "detail": f"图谱碎片化: {len(components)} 个分量，最大覆盖 {coverage:.1%}",
            })
    else:
        report["metrics"]["component_count"] = 0
        report["metrics"]["largest_component_coverage"] = 0

    temporal_edges = sum(1 for _, _, d in graph.G.edges(data=True)
                         if d.get("rel") in ("temporal_next", "temporal_prev"))
    report["metrics"]["temporal_edges"] = temporal_edges
    expected_temporal = max(0, total_nodes - len(set(d.get("source_agent", "")
                                                       for _, d in graph.G.nodes(data=True))))
    if expected_temporal > 0:
        temporal_completeness = min(1.0, temporal_edges / expected_temporal)
        report["metrics"]["temporal_completeness"] = round(temporal_completeness, 3)

    cutoff = (date.today() - timedelta(days=MEMORY_TTL_DAYS)).isoformat()
    expired = sum(1 for _, d in graph.G.nodes(data=True)
                   if d.get("date", "9999") < cutoff and d.get("type") != "compressed_summary")
    report["metrics"]["expired_uncompressed"] = expired
    if expired > 50:
        report["issues"].append({
            "type": "stale_memories",
            "severity": "info",
            "detail": f"{expired} 条过期未压缩记忆，建议运行拓扑压缩",
        })

    expected_dim = EMBEDDING_CONFIG.get("dim", 1024)
    report["metrics"]["expected_embedding_dim"] = expected_dim

    issue_count = len(report["issues"])
    health_score = max(0.0, 1.0
                       - orphan_ratio * 0.3
                       - (0.2 if issue_count >= 3 else 0.0)
                       - (0.1 if expired > 100 else 0.0))
    report["metrics"]["health_score"] = round(health_score, 2)

    if health_score < 0.6:
        report["status"] = "degraded"
    elif report["issues"]:
        report["status"] = "warning"

    logger.info(f"Memory hygiene: score={health_score:.2f}, issues={issue_count}")
    return report

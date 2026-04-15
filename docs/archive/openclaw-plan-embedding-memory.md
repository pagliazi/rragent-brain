---
name: "OpenClaw — Agent Embedding Memory"
overview: "[OpenClaw] 为 4 个 Agent 构建拓扑稳定的三层记忆架构：HNSW 向量图（语义邻近）+ 共享知识图谱（显式关系）+ 时序索引（因果链），基于 Ollama bge-m3 + ChromaDB 调优 HNSW + NetworkX 关系图，含跨 Agent 记忆、可配融合权重、模型版本追踪、容错降级。已增强为混合云 Embedding + 跨 Agent 冗余互提醒。"
todos:
  - id: mem-infra
    content: 安装 chromadb + networkx + ollama pull bge-m3
    status: completed
  - id: mem-config
    content: 编写 agents/memory/config.py（EMBEDDING_CONFIG + AGENT_FUSION_WEIGHTS + 版本追踪常量 + 混合云配置）
    status: completed
  - id: mem-core
    content: 编写 agents/memory/ 核心模块（embedding + vector_store + knowledge_graph + timeline + retriever + memory_mixin），含容错降级 + 共享图谱 + 可配权重
    status: completed
  - id: mem-analysis-news
    content: AnalysisAgent + NewsAgent 接入三层记忆（含跨 Agent recall）
    status: completed
  - id: mem-dev
    content: DevAgent 接入操作链记忆（causal 链 + references 跨 Agent 边）
    status: completed
  - id: mem-orchestrator
    content: Orchestrator 接入 Redis 行为画像 + 全局图谱拓扑健康监控 + 降级告警
    status: completed
  - id: mem-lifecycle
    content: 记忆生命周期管理 + 拓扑压缩 + 图谱每日备份 + rules.yaml 自动维护
    status: completed
  - id: mem-hybrid-cloud
    content: 混合云 Embedding — 本地 Ollama bge-m3 优先 + SiliconFlow/OpenAI/DashScope 云端回退 + 磁盘缓存
    status: completed
  - id: mem-cross-remind
    content: 跨 Agent 冗余记忆互提醒 — Redis Pub/Sub + reminds 关系 + recall 自动纳入
    status: completed
  - id: mem-hygiene
    content: 记忆卫生报告 — 嵌入一致性、孤立节点比例、图谱连通性、过期记忆占比
    status: completed
isProject: false
---

# Agent 拓扑稳定向量记忆系统（v2 含精化 + 混合云增强）

> **状态: ✅ 全部完成**
> 最后更新: 2026-02-25

## 已实施模块

### 核心三层架构
- **L1 HNSW 向量图**: ChromaDB M=32 ef=200 cosine, 每 Agent 独立 collection
- **L2 共享知识图谱**: NetworkX DiGraph, JSON 持久化, 跨 Agent references 边
- **L3 时序索引**: 每日锚点, exp(-0.03 * days) 时间衰减
- **融合排序器**: 按 Agent 可配权重 (vector/graph/time)

### 增强功能
- **混合云 Embedding**: 本地 Ollama bge-m3 优先 → 云端回退 (SiliconFlow/OpenAI/DashScope) → 磁盘缓存
- **跨 Agent 冗余互提醒**: reminder.py — Redis Pub/Sub 扫描新记忆 → 关联 Agent 自动收到提醒 → 图谱建立 reminds 边
- **容错降级**: Level 0-3, 记忆故障不阻塞主任务
- **模型版本追踪**: collection metadata 记录 embedding_model, 迁移时自动创建 _v2
- **记忆卫生报告**: memory_hygiene — 嵌入一致性、孤立节点、连通性、过期占比
- **SOUL Guardian**: 哈希基线 + 篡改检测 + 即时告警

### 文件结构
```
agents/memory/
  __init__.py
  config.py           # EMBEDDING_CONFIG + AGENT_FUSION_WEIGHTS + 混合云 + 版本追踪
  embedding.py        # 混合 Embedding (本地 Ollama + 云端回退 + 磁盘缓存)
  vector_store.py     # ChromaDB 封装 + HNSW 调优 + 版本检查
  knowledge_graph.py  # NetworkX 共享关系图谱 + JSON 持久化 + 备份
  timeline.py         # 时序索引 + 每日锚点 + 衰减函数
  retriever.py        # 融合排序器 (三层合一 + 按 Agent 权重)
  memory_mixin.py     # BaseAgent 记忆混入 (含容错降级 + remind 纳入)
  reminder.py         # 跨 Agent 冗余记忆互提醒
```

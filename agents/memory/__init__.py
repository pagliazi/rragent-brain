"""
agents.memory — 三层拓扑稳定记忆系统 v3.0
L1: HNSW 向量图 (ChromaDB)
L2: 共享知识图谱 (NetworkX)
L3: 时序索引 (每日锚点 + 时间衰减)
Embedding: 本地 Ollama bge-m3 优先 → 云端 API 回退 → 磁盘缓存
Remind: 跨 Agent 冗余记忆互相提醒
SoulGuard: SOUL.md 身份漂移检测 + 基线完整性守护
Hygiene: 记忆质量评估 + 嵌入一致性检查
"""

from agents.memory.memory_mixin import MemoryMixin

__all__ = ["MemoryMixin"]

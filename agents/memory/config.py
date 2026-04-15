"""
记忆系统配置 — embedding 模型、HNSW 参数、融合权重、存储路径、
混合云回退、冗余提醒、AKShare 备用数据源

架构定位:
  - 本地 Ollama 仅负责记忆嵌入 (bge-m3)，不参与 LLM 推理
  - 所有推理分析走云端 API（见 agents/llm_router.py）
"""

import os

# ── Ollama 本地引擎（仅用于嵌入） ──────────────────
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")

EMBEDDING_CONFIG = {
    "model": "bge-m3",
    "dim": 1024,
    "version": "bge-m3:567m",
    "space": "cosine",
}

# ── 混合云 Embedding 回退链 ────────────────────────
# 策略: 本地 bge-m3 → 云端 API → 缓存兜底
# 支持的 provider: "siliconflow" | "openai" | "dashscope" | "none"
CLOUD_EMBEDDING_CONFIG = {
    "enabled": os.getenv("CLOUD_EMBED_ENABLED", "true").lower() == "true",
    "provider": os.getenv("CLOUD_EMBED_PROVIDER", "siliconflow"),
    "providers": {
        "siliconflow": {
            "api_key": os.getenv("SILICONFLOW_API_KEY", ""),
            "base_url": "https://api.siliconflow.cn/v1/embeddings",
            "model": "BAAI/bge-m3",
            "dim": 1024,
        },
        "openai": {
            "api_key": os.getenv("OPENAI_API_KEY", ""),
            "base_url": os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1/embeddings"),
            "model": "text-embedding-3-small",
            "dim": 1536,
        },
        "dashscope": {
            "api_key": os.getenv("DASHSCOPE_API_KEY", ""),
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings",
            "model": "text-embedding-v3",
            "dim": 1024,
        },
    },
    "cache_ttl_hours": 24,
    "max_cache_entries": 10000,
}

# ── HNSW 向量图参数 ────────────────────────────────
HNSW_CONFIG = {
    "hnsw:M": 32,
    "hnsw:construction_ef": 200,
    "hnsw:search_ef": 100,
    "hnsw:space": "cosine",
}

# ── 融合排序权重 ───────────────────────────────────
DEFAULT_FUSION_WEIGHTS = {
    "vector": 0.6,
    "graph": 0.25,
    "time": 0.15,
}

AGENT_FUSION_WEIGHTS = {
    "analysis": {"vector": 0.50, "graph": 0.35, "time": 0.15},
    "news":     {"vector": 0.45, "graph": 0.20, "time": 0.35},
    "dev":      {"vector": 0.40, "graph": 0.45, "time": 0.15},
}

# ── 知识图谱关系类型 ───────────────────────────────
RELATION_TYPES = (
    "temporal_next",
    "temporal_prev",
    "causal",
    "same_topic",
    "follow_up",
    "contradicts",
    "references",
    "reminds",
)

# ── 存储路径 ───────────────────────────────────────
MEMORY_BASE_DIR = os.getenv(
    "MEMORY_BASE_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "memory"),
)
CHROMA_PERSIST_DIR = os.path.join(MEMORY_BASE_DIR, "chroma")
GRAPH_PERSIST_DIR = os.path.join(MEMORY_BASE_DIR, "graph")
EMBED_CACHE_DIR = os.path.join(MEMORY_BASE_DIR, "embed_cache")

MEMORY_TTL_DAYS = 90
TIME_DECAY_LAMBDA = 0.03

# ── 冗余记忆提醒配置 ───────────────────────────────
REMINDER_CONFIG = {
    "enabled": True,
    "interval_seconds": 600,
    "similarity_threshold": 0.75,
    "max_reminders_per_cycle": 5,
    "cross_agent_pairs": [
        ("news", "analysis"),
        ("analysis", "dev"),
        ("news", "dev"),
    ],
}

# ── Bridge API 数据源 (ReachRich 139) ────────────────
BRIDGE_API_BASE = os.getenv("BRIDGE_BASE_URL", "http://192.168.1.139:8001/api/bridge")

# ── 旧版 API 数据源 (兼容回退) ────────────────────────
PRIMARY_API_BASE = os.getenv("STOCK_API_BASE", "http://192.168.1.138/api")

AKSHARE_CONFIG = {
    "enabled": os.getenv("AKSHARE_ENABLED", "true").lower() == "true",
    "failover_threshold": 3,
    "retry_primary_after_seconds": 300,
}

# ── 输入安全防护 ───────────────────────────────────
INPUT_GUARD_CONFIG = {
    "enabled": True,
    "max_input_length": 32000,
    "blocked_patterns": [
        # 经典 prompt injection
        r"ignore\s+(previous|above|all)\s+(instructions?|prompts?|context|rules?)",
        r"disregard\s+(all|previous|above|prior)\s+(instructions?|prompts?|rules?)",
        r"forget\s+(all|previous|prior|your)\s+(instructions?|rules?|context)",
        # 角色覆盖攻击
        r"you\s+are\s+now\s+(a|an|the)\s+",
        r"act\s+as\s+(a|an|the)\s+",
        r"pretend\s+(you\s+are|to\s+be)\s+",
        r"roleplay\s+as\s+",
        r"from\s+now\s+on\s+(you\s+(are|will)|act)",
        # 系统提示泄露
        r"system\s*prompt",
        r"reveal\s+(your|the)\s+(system|instructions?|prompt)",
        r"show\s+me\s+(your|the)\s+(system|instructions?|prompt)",
        r"print\s+(your|the)\s+(system|instructions?|rules?)",
        r"what\s+(are|is)\s+your\s+(system\s+prompt|instructions?|rules?)",
        # 越狱模式
        r"jailbreak",
        r"\bDAN\b",
        r"developer\s+mode",
        r"sudo\s+mode",
        r"god\s+mode",
        # 特殊 token 注入
        r"<\|.*?\|>",
        r"\[INST\]",
        r"<<SYS>>",
        r"<\|im_start\|>",
        r"<\|im_end\|>",
        # 指令注入模式
        r"new\s+instructions?:",
        r"#\s*system\s*:",
        r"\[system\]",
        r"---\s*system\s*---",
    ],
}

# ── 反思引擎配置 ────────────────────────────────────
REFLECTION_CONFIG = {
    "enabled": True,
    # 注入 L1 分诊 prompt 的最低样本量阈值
    "min_samples_for_hint": 3,
    # 将路由提示注入 L1 prompt（提升分诊准确性）
    "inject_hints_into_l1": True,
    # 自动检测重复查询（用户未满足信号）
    "detect_repeated_queries": True,
    # 重复查询检测窗口（最近 N 条）
    "repeat_detection_window": 8,
    # 每日洞察推送时间（规则引擎触发）
    "daily_insight_schedule": "30 22 * * *",
    # 周报时间（每周一早）
    "weekly_report_schedule": "0 9 * * 1",
}

# OpenClaw 多智能体系统 — 文档索引

> **版本**: v6.0 | **最后更新**: 2026-02-25
> **项目**: OpenClaw A 股多智能体协作系统
> **运行环境**: Mac Mini M4 / macOS / `clawagent` 用户

## 核心文档

| 文件 | 说明 |
|------|------|
| [openclaw-technical-doc.md](openclaw-technical-doc.md) | **项目技术文档 (v6.0)** — 完整架构、Agent 角色（含量化回测）、消息流、API、部署说明 |
| [openclaw-deployment-guide.md](openclaw-deployment-guide.md) | **部署与运维指南** — 服务一览、命令参考、故障自救 |
| [openclaw-security-report.md](openclaw-security-report.md) | **安全分析报告** — 三用户隔离、权限矩阵、风险评估 |

## 进阶设计

| 文件 | 说明 |
|------|------|
| [openclaw-v1.2-advanced-design.md](openclaw-v1.2-advanced-design.md) | **架构演进方向** — PCEC 进化引擎、共享黑板、动态 Kanban、MCP 协议 |
| [openclaw-v1.3-quant-system.md](openclaw-v1.3-quant-system.md) | **量化回测系统设计** — Virtual Quant Fund、5 角色 Pipeline、安全沙箱、决策账本 |

## 实施计划

| 文件 | 说明 | 状态 |
|------|------|------|
| [openclaw-plan-multi-agent.md](openclaw-plan-multi-agent.md) | 多 Agent 拆分方案 — 9 Agent + Orchestrator + Redis 总线 | ✅ 完成 |
| [openclaw-plan-multichannel.md](openclaw-plan-multichannel.md) | 多渠道消息架构 — TG + 飞书冗余 + 双向健康检查 | ✅ 完成 |
| [openclaw-plan-telegram-routing.md](openclaw-plan-telegram-routing.md) | Telegram Topics 分流 + LLM 意图柔性路由 | ✅ 完成 |
| [openclaw-plan-embedding-memory.md](openclaw-plan-embedding-memory.md) | 三层拓扑记忆 — HNSW + 知识图谱 + 时序索引 | ✅ 完成 |

## 系统架构图

```
┌─────────── 用户交互层 ───────────┐
│  Telegram (4 Topics)             │
│  飞书 (Webhook 推送)             │
│  Web Dashboard (FastAPI+React)   │
└──────────┬───────────────────────┘
           │
┌──────────▼───────────────────────┐
│  Orchestrator (编排 + 意图路由)   │
│  NotifyRouter (消息分发 + ACK)    │
│  TaskManager (长任务调度)         │
│  Rules Engine (19 条定时规则)     │
│  Quant Pipeline (量化编排)       │
└──────────┬───────────────────────┘
           │ Redis Pub/Sub
┌──────────▼───────────────────────┐
│  MarketAgent    │  NewsAgent     │
│  AnalysisAgent  │  StrategistAgent│
│  DevAgent       │  BrowserAgent  │
│  DesktopAgent   │  BacktestAgent │
└──────────┬───────────────────────┘
           │
┌──────────▼───────────────────────┐
│  三层拓扑记忆 (ChromaDB+NetworkX)│
│  LLM Router (百炼→DeepSeek→...)  │
│  Data Router (138 API → AKShare) │
│  SOUL Guardian (身份守护)        │
│  Input Guard (安全防护)          │
│  Backtest Sandbox (代码沙箱)     │
└──────────────────────────────────┘
```

## 技术栈

| 层级 | 技术 |
|------|------|
| 前端 | React 18 + Tailwind CSS (CDN) |
| API | FastAPI + uvicorn (SSE 流式) |
| 通信 | Redis Pub/Sub + asyncio |
| 推理 | 百炼 (qwen3.5-plus) / DeepSeek / SiliconFlow / DashScope / OpenAI |
| 嵌入 | Ollama bge-m3 (本地) + 混合云回退 |
| 存储 | ChromaDB (向量) + NetworkX (图谱) + Redis (状态) |
| 量化 | backtrader + pandas-ta + vectorbt + akshare |
| 部署 | macOS LaunchAgent (clawagent 用户) |
| 硬件 | Mac Mini M4, macOS Sequoia |

---
name: "OpenClaw — Multi-Agent Architecture"
overview: "[OpenClaw] 将当前单体 telegram_agent.py (999行) 拆分为 9 个专项 Agent 独立进程 + 1 个 Orchestrator 统筹调度，通过 Redis 消息总线通信，Telegram Bot / Feishu Bot / Web Chat 作为统一前端。"
todos:
  - id: redis-base
    content: 安装 Redis + 编写 BaseAgent 基类 + 消息协议定义
    status: completed
  - id: market-analysis
    content: 拆出 MarketAgent 和 AnalysisAgent，接入 Redis 通信
    status: completed
  - id: dev-browser-desktop
    content: 拆出 DevAgent / BrowserAgent / DesktopAgent
    status: completed
  - id: orchestrator-rules
    content: 实现 Orchestrator 统筹 + rules.yaml 规则引擎
    status: completed
  - id: news-refactor
    content: 拆出 NewsAgent + 重构 Telegram Bot 和 Web Chat 为薄前端
    status: completed
  - id: strategist
    content: 新增 StrategistAgent 策略分析师角色
    status: completed
  - id: multichannel
    content: 多渠道架构 — NotifyRouter + Feishu Bot + 双向健康检查
    status: completed
isProject: false
---

# OpenClaw Multi-Agent 架构方案

> **状态: ✅ 全部完成**
> 最后更新: 2026-02-25

## 当前架构总览

```
前端入口:
  - Telegram Bot (主渠道, Supergroup + 4 Topics)
  - Feishu Bot (冗余渠道, 4 群映射)
  - Web Chat (Chainlit)

统筹层:
  - Orchestrator (命令路由 + LLM 意图识别 + 规则引擎 + NotifyRouter)

专项 Agent (8个):
  - MarketAgent — 行情监控
  - AnalysisAgent — AI 分析
  - DevAgent — 远程开发
  - BrowserAgent — 浏览器自动化
  - DesktopAgent — 桌面控制
  - NewsAgent — 舆情监控
  - StrategistAgent — 策略分析师

基础设施:
  - Redis 消息总线
  - ChromaDB + NetworkX 三层拓扑记忆
  - Ollama bge-m3 (仅 embedding)
  - 云 LLM 路由: Bailian → SiliconFlow → DeepSeek → DashScope → OpenAI
  - AKShare 备用数据源 (主: 192.168.1.138 API)
  - NotifyRouter 多渠道消息路由 + 健康检查 + 故障转移
```

## 已实施增强

1. **LLM 意图识别柔性路由** — 精确命令走快速路径，自由文本走 LLM 意图识别 *Done*
2. **三层拓扑记忆系统** — HNSW + 知识图谱 + 时序索引 + 混合云 Embedding *Done*
3. **跨 Agent 冗余记忆互提醒** — Redis Pub/Sub + reminds 关系 *Done*
4. **SOUL Guardian 身份守护** — 哈希基线 + 篡改告警 *Done*
5. **LLM Router 云端路由** — 本地仅 bge-m3 embedding，推理全走云 API *Done*
6. **Input Guard 安全防护** — 注入检测 + 长度限制 + 控制字符过滤 *Done*
7. **AKShare 备用数据源** — 主 API 故障时自动切换 *Done*
8. **NotifyRouter 多渠道路由** — TG + 飞书冗余，消息优先级，故障转移 *Done*
9. **双向健康检查** — 互检 + 自动 kickstart 修复 + pending 消息队列 *Done*

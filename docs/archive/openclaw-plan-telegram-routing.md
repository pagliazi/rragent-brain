---
name: "OpenClaw — Telegram Topics + Flexible Routing"
overview: "[OpenClaw] Telegram 通知重构为 Topics 话题分区、硬编码路由替换为 LLM 意图识别柔性路由、新增策略分析师角色、采纳 awesome-openclaw-usecases 高自动化模式。"
todos:
  - id: tg-topics
    content: "Telegram 话题分流: 改造 telegram_bot.py 和 orchestrator 通知推送，实现 4 话题分区"
    status: completed
  - id: flex-routing
    content: "柔性路由: 新增 _intent_route + MessageHandler，精确命令保留快速路径，自然语言走 LLM 意图识别"
    status: completed
  - id: strategist-agent
    content: "新建 StrategistAgent: agent + SOUL + skills + strategy_state.yaml，集成 daily_review 和策略问答"
    status: completed
  - id: rules-integration
    content: "rules.yaml 集成: 策略师收盘复盘、策略推送到 TOPIC_STRATEGY、财报事件跟踪规则"
    status: completed
isProject: false
---

# Telegram Topics + 柔性路由 + 策略分析师角色

> **状态: ✅ 全部完成**
> 最后更新: 2026-02-25

## 一、Telegram 消息分流 — Done

- Supergroup + Forum Topics 模式
- 4 话题分区: 行情推送(3) / 查询响应(4) / 策略分析(5) / 系统运维(7)
- `TELEGRAM_GROUP_ID=-1003731324560` 已配置
- telegram_bot.py `_send_topic_msg()` 按 topic 路由到对应话题
- Orchestrator 所有通知推送已改为通过 NotifyRouter 统一分发

## 二、柔性任务路由 — Done

- `_intent_route()`: 从 skills/*.yaml 动态构建能力清单 → LLM 意图识别 → JSON 输出 agent.action
- 精确命令 (`/zt`, `/lb` 等) 保留快速路径不走 LLM
- 自由文本消息通过 `MessageHandler` → `handle_free_text` → chat 命令 → `_intent_route`
- 支持多步编排: LLM 返回 `[{step1}, {step2}]` 顺序执行

## 三、StrategistAgent — Done

- `agents/strategist_agent.py`: daily_review / sector_thesis / risk_alert / ask_strategy / update_state
- `agents/souls/strategist.md`: 首席策略分析师 SOUL 定义
- `agents/skills/strategist_skills.yaml`: 5 项能力注册
- `agents/memory/strategy_state.yaml`: 策略状态持久化 (market_phase / themes / watchlist)
- rules.yaml: 收盘复盘(15:15) / 盘中风险监测(*/15) / 午盘板块研判(11:35) / 财报事件跟踪(8:00)

## 四、awesome-openclaw-usecases 采纳 — Done

| 模式 | 实现 |
|------|------|
| Multi-Agent Team (tag-based) | _intent_route LLM 柔性路由 |
| Dynamic Dashboard (parallel) | Strategist daily_review 并行采集 |
| AI Earnings Tracker (event-driven) | rules.yaml 财报季事件跟踪规则 |
| STATE.yaml coordination | strategy_state.yaml 持久化 |

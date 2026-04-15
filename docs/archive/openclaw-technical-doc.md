# OpenClaw 多智能体系统 — 项目技术文档

> **版本**: v6.0 | **最后更新**: 2026-02-25
> **运行环境**: Mac Mini M4 / macOS / `clawagent` 用户
> **部署目录**: `/Users/clawagent/openclaw/`

---

## 目录

1. [系统概览](#1-系统概览)
2. [整体架构](#2-整体架构)
3. [Agent 角色分工](#3-agent-角色分工)
4. [消息处理流程](#4-消息处理流程)
5. [三层拓扑记忆系统](#5-三层拓扑记忆系统)
6. [LLM 路由策略](#6-llm-路由策略)
7. [数据源架构](#7-数据源架构)
8. [多渠道通知系统](#8-多渠道通知系统)
9. [长期任务管理](#9-长期任务管理)
10. [规则引擎](#10-规则引擎)
11. [安全体系](#11-安全体系)
12. [部署与运维](#12-部署与运维)
13. [文件清单](#13-文件清单)
14. [Web Dashboard](#14-web-dashboard)
15. [量化回测系统](#15-量化回测系统)

---

## 1. 系统概览

OpenClaw 是一个面向 A 股投资分析的 **多智能体协作系统**，在本地 Mac Mini M4 上运行。

### 核心能力

| 能力 | 说明 |
|------|------|
| 实时行情 | 涨停、连板、板块、热股查询（主 API + AKShare 备用） |
| 深度分析 | LLM 驱动的市场结构分析、情绪判断、板块轮动研判 |
| 策略研判 | 收盘复盘、中期策略、风险预警、持仓建议 |
| 量化回测 | Alpha 策略灵感 → 代码生成 → 沙箱回测 → 风控审核 → 决策账本 |
| 新闻监控 | 舆情采集、AI 摘要、影响评级 |
| 远程开发 | SSH 操控远程服务器、AI 辅助代码生成 |
| 多渠道交互 | Telegram（主）+ 飞书 Webhook（冗余）+ Web Dashboard |
| 长期记忆 | 三层拓扑记忆（向量图 + 知识图谱 + 时间线） |
| 自动化作业 | 规则引擎驱动的定时任务 + 多步长期任务编排 |

---

## 2. 整体架构

```
┌──────────────────────────────────────────────────────────┐
│                      用户交互层                           │
│                                                          │
│   [Telegram Bot]    [飞书 Bot]     [Web Dashboard]        │
│   主通道 · 4话题     Webhook 冗余    http://ip:7789        │
│   心跳 10s          心跳 10s        暗色 5Tab 控制台       │
│       │                │                │                │
│       └────────────────┴────────────────┘                │
│                        │ Redis Pub/Sub                   │
└────────────────────────┼─────────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────────┐
│                   编排调度层                               │
│                                                          │
│  ┌──────────────────────────────────────────────────┐    │
│  │              Orchestrator (编排器)                 │    │
│  │                                                  │    │
│  │  ┌────────────┐ ┌────────────┐ ┌──────────────┐  │    │
│  │  │ 命令路由    │ │ 规则引擎    │ │ NotifyRouter │  │    │
│  │  │ 精确+意图   │ │ 定时+事件   │ │ 多渠道推送    │  │    │
│  │  └────────────┘ └────────────┘ └──────────────┘  │    │
│  │  ┌────────────┐ ┌────────────┐ ┌──────────────┐  │    │
│  │  │ TaskManager │ │ InputGuard │ │ SoulGuardian │  │    │
│  │  │ 长期任务    │ │ 输入安全    │ │ 身份守护     │  │    │
│  │  └────────────┘ └────────────┘ └──────────────┘  │    │
│  └──────────────────────────────────────────────────┘    │
│                         │                                │
│              ┌──────────┼──────────┐                     │
│              ▼          ▼          ▼                     │
└──────────────────────────────────────────────────────────┘
                         │
┌──────────────────────────────────────────────────────────┐
│                    专业 Agent 层                          │
│                                                          │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐    │
│  │ Market   │ │ Analysis │ │ News     │ │Strategist│    │
│  │ 行情数据  │ │ 深度分析  │ │ 新闻舆情  │ │ 策略研判  │    │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘    │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐    │
│  │ Backtest │ │ Dev      │ │ Browser  │ │ Desktop  │    │
│  │ 量化回测  │ │ 远程开发  │ │ 浏览器    │ │ 桌面操控  │    │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘    │
│                                                          │
└──────────────────────────────────────────────────────────┘
                         │
┌──────────────────────────────────────────────────────────┐
│                    基础设施层                              │
│                                                          │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐    │
│  │ 三层记忆  │ │ LLM路由   │ │ 数据源    │ │ 时间感知  │    │
│  │ ChromaDB │ │ 云端多链路 │ │ 主API+AK │ │ 交易时段  │    │
│  │ NetworkX │ │ 5级回退   │ │ 自动切换  │ │ prompt注入│    │
│  │ Timeline │ │          │ │          │ │          │    │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘    │
│  ┌──────────┐ ┌──────────┐                               │
│  │ Redis    │ │ Ollama   │                               │
│  │ 消息总线  │ │ bge-m3   │                               │
│  │ 状态存储  │ │ 本地嵌入  │                               │
│  └──────────┘ └──────────┘                               │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

### 通信机制

所有 Agent 间通信基于 **Redis Pub/Sub**，消息格式统一为 `AgentMessage`:

```json
{
  "id": "uuid",
  "sender": "orchestrator",
  "target": "market",
  "action": "get_limitup",
  "params": {},
  "reply_to": "reply:uuid",
  "timestamp": 1740000000,
  "result": null,
  "error": null
}
```

- 每个 Agent 监听 `openclaw:{agent_name}` 频道
- 回复通过 `reply_to` 指定的临时频道返回
- 心跳通过 Redis Hash `openclaw:heartbeats` 维护，间隔 10 秒

---

## 3. Agent 角色分工

### 3.1 Orchestrator (编排器)

| 属性 | 值 |
|------|-----|
| **进程** | `com.openclaw.orchestrator` |
| **职责** | 统一入口、命令路由、跨 Agent 编排、规则引擎、全局监控 |

**核心能力：**

| 能力 | 说明 |
|------|------|
| 精确路由 | `/zt` → `market.get_limitup`，直接映射 |
| 意图路由 | 自由文本经 LLM 识别意图，动态分配给合适的 Agent |
| 多步编排 | 需要多个 Agent 协作的复杂请求（如收盘复盘） |
| 规则引擎 | 定时/事件/条件触发，自动执行 Agent 动作 |
| 任务管理 | 长期任务创建、进度追踪、暂停/恢复/取消 |
| 渠道管理 | NotifyRouter 推送、渠道健康监控、故障自修复 |
| 安全防护 | 输入过滤、SOUL 身份完整性校验 |

**支持的动作：**
`route` `notify` `status` `reload_rules` `memory_health` `memory_backup` `memory_compress` `memory_remind` `memory_hygiene` `data_source_status` `embed_status` `embed_cache_prune` `soul_check` `soul_accept` `llm_status` `daily_briefing` `task_create` `task_status` `task_list` `task_cancel` `channel_status` `channel_flush` `backtest` `bt_cache` `ledger` `quant_research`

---

### 3.2 Market Agent (行情数据中心)

| 属性 | 值 |
|------|-----|
| **进程** | `com.openclaw.market-agent` |
| **职责** | A 股实时行情查询与聚合，不使用 LLM |
| **数据源** | 主: `http://192.168.1.138/api/` / 备: AKShare |

**支持的动作：**

| 动作 | 说明 |
|------|------|
| `get_limitup` | 今日涨停板列表 |
| `get_limitstep` | 连板股梯队 |
| `get_concepts` | 概念板块涨幅排行 |
| `get_hot` | 同花顺热股排行 |
| `get_summary` | 行情摘要（含时间戳 + 数据新鲜度标记） |
| `get_all_raw` | 全量原始数据（供 Analysis / Strategist 使用） |

---

### 3.3 Analysis Agent (AI 分析师)

| 属性 | 值 |
|------|-----|
| **进程** | `com.openclaw.analysis-agent` |
| **职责** | 基于行情数据的深度分析、板块轮动研判 |
| **LLM** | DeepSeek (云端) |
| **记忆** | 三层拓扑 + 跨 Agent 记忆提醒 |

**分析框架**: 市场温度计 → 情绪周期 → 主线研判 → 赚钱效应

**时间感知**: 盘中优先实时数据（recall 7天），非盘中扩展历史（recall 30天）

| 动作 | 说明 |
|------|------|
| `ask` | 深度分析问答 (500-1000字) |
| `brief_analysis` | 市场速览 (100-200字) |

---

### 3.4 News Agent (舆情监控)

| 属性 | 值 |
|------|-----|
| **进程** | `com.openclaw.news-agent` |
| **职责** | 新闻/公告/舆情采集与 AI 摘要 |
| **数据源** | 主: `192.168.1.138/sentiment/` / 备: AKShare |

| 动作 | 说明 |
|------|------|
| `get_news` | 按关键词检索新闻 |
| `summarize_news` | AI 舆情摘要 (3-5 要点) |

---

### 3.5 Strategist Agent (策略分析师)

| 属性 | 值 |
|------|-----|
| **进程** | `com.openclaw.strategist-agent` |
| **职责** | 收盘复盘、板块研判、风险监测、策略问答 |
| **依赖** | Market + News 数据，三层记忆 |

**时间感知**: 根据交易时段调整分析视角和风险敏感度

| 动作 | 说明 |
|------|------|
| `daily_review` | 收盘全面复盘 |
| `sector_thesis` | 板块研判与趋势更新 |
| `risk_alert` | 风险扫描 (alert_level: none/low/medium/high) |
| `ask_strategy` | 策略问答 |
| `update_state` | 更新策略状态文件 |

---

### 3.6 Dev Agent (远程开发)

| 属性 | 值 |
|------|-----|
| **进程** | `com.openclaw.dev-agent` |
| **职责** | SSH 远程操控 192.168.1.138，AI 辅助代码生成 |

| 动作 | 说明 |
|------|------|
| `ssh_exec` | 远程执行 shell 命令 |
| `read_file` | 远程读取文件 |
| `write_file` | 远程写入文件 |
| `ai_dev` | AI 理解指令 → 生成命令计划 → 可选自动执行 |

**安全限制**: 禁止 `rm -rf`、`mkfs`、`dd`、`shutdown`、`reboot`，单次最多 10 条命令，超时 30s。

---

### 3.7 Backtest Agent (量化回测沙箱)

| 属性 | 值 |
|------|-----|
| **进程** | `com.openclaw.backtest-agent` |
| **职责** | 量化策略代码安全执行、回测结果管理、决策账本归档 |
| **LLM** | 不使用（纯 Python 沙箱） |

**核心能力：**

| 能力 | 说明 |
|------|------|
| 代码安全检查 | 禁止 `os`/`sys`/`subprocess`/`shutil` 等危险模块 |
| 沙箱执行 | `subprocess` 隔离，10 分钟超时，内存限制 |
| 数据缓存 | 本地缓存历史行情 (`data/history_cache/`)，避免重复拉取 |
| 决策账本 | Markdown 格式策略归档 (`data/decision_ledger/`) |

**支持的动作：**

| 动作 | 说明 |
|------|------|
| `run_backtest` | 执行量化策略代码并返回回测结果 |
| `validate_code` | 安全检查代码（不执行） |
| `list_cache` | 列出本地行情缓存文件 |
| `save_ledger` | 保存策略研究结果到决策账本 |
| `list_ledger` | 列出决策账本条目 |

**安全机制**: 代码白名单验证 → subprocess 沙箱执行 → 超时熔断 → 结果提取。禁止网络访问、文件系统操作、进程管理。

---

### 3.8 前端 Bot

| Bot | 进程 | 说明 |
|-----|------|------|
| Telegram Bot | `com.openclaw.telegram-bot` | 主通道，4 个 Forum Topics (行情/查询/策略/系统) |
| 飞书 Bot | `com.openclaw.feishu-bot` | 冗余通道，Webhook 推送 + 可选应用机器人双向交互 |
| Web Dashboard | `com.openclaw.webchat` | FastAPI + React 三栏控制台，端口 7789 (对话/行情/任务/系统) |

---

## 4. 消息处理流程

### 4.1 用户命令处理流程

```
用户输入 "/ask 大盘怎么看"
          │
          ▼
   ┌─────────────┐
   │ Telegram Bot │  解析命令前缀
   └──────┬──────┘
          │ Redis Pub → openclaw:orchestrator
          ▼
   ┌─────────────┐
   │ Orchestrator │
   │              │
   │ 1. InputGuard 安全检查
   │ 2. 精确路由: /ask → analysis.ask
   │ 3. 发送 AgentMessage
   └──────┬──────┘
          │ Redis Pub → openclaw:analysis
          ▼
   ┌─────────────┐
   │  Analysis    │
   │              │
   │ 1. 获取 Market 全量数据
   │ 2. 注入交易时段上下文
   │ 3. recall 相关记忆
   │ 4. 获取跨 Agent 提醒
   │ 5. 构造 prompt → LLM
   │ 6. remember 存储结果
   │ 7. 返回分析结果
   └──────┬──────┘
          │ Redis Pub → reply:{msg_id}
          ▼
   ┌─────────────┐
   │ Orchestrator │  接收结果 → 格式化
   └──────┬──────┘
          │ NotifyRouter.broadcast()
          ▼
   ┌─────────────┐
   │ Telegram Bot │  发送到 🔍查询响应 话题
   └─────────────┘
```

### 4.2 意图路由 (自由文本)

```
用户输入 "帮我看看今天哪些板块比较强"
          │
          ▼
   ┌─────────────┐
   │ Orchestrator │
   │              │
   │ 1. 无精确命令匹配
   │ 2. LLM 意图识别:
   │    → agent: "analysis"
   │    → action: "ask"
   │    → params: {question: "..."}
   │ 3. 转发给 Analysis Agent
   └──────┬──────┘
          ▼
      (同上流程)
```

### 4.3 多步编排 (长期任务)

```
用户输入 "/task_new close_review"
          │
          ▼
   ┌─────────────────┐
   │   Orchestrator   │
   │   TaskManager    │
   │                  │
   │  Step 1: market.get_all_raw    ──► Market Agent
   │  Step 2: news.get_news         ──► News Agent
   │  Step 3: analysis.ask          ──► Analysis Agent
   │  Step 4: strategist.review     ──► Strategist Agent
   │  Step 5: strategist.sector     ──► Strategist Agent
   │  Step 6: strategist.risk       ──► Strategist Agent
   │  Step 7: notify (推送报告)      ──► NotifyRouter
   │                  │
   │  每步完成后:
   │  · 更新 Redis 进度
   │  · 推送进度到系统话题
   │  · 前步结果注入后步参数
   └─────────────────┘
```

### 4.4 规则引擎触发

```
rules.yaml 定义:
  "盘前提醒" → cron: "0 9 15 * * 1-5"
          │
          ▼
   ┌─────────────┐
   │ Orchestrator │
   │ 规则引擎循环  │
   │              │
   │ 1. 检查 cron 表达式
   │ 2. 匹配到 → 执行 action
   │ 3. action: market.get_summary
   │ 4. 结果 → notify (topic: market)
   └─────────────┘
```

---

## 5. 三层拓扑记忆系统

### 架构

```
┌────────────────────────────────────────────────┐
│              MemoryMixin (混入各 Agent)          │
│                                                │
│  remember()  recall()  connect()  get_timeline()│
│       │         │          │           │       │
│       ▼         ▼          ▼           ▼       │
│  ┌─────────┐ ┌─────────┐ ┌──────────────────┐  │
│  │ Layer 1 │ │ Layer 2 │ │     Layer 3      │  │
│  │ HNSW    │ │ 知识图谱 │ │    时间索引       │  │
│  │ 向量图   │ │NetworkX │ │   时序日志        │  │
│  │ChromaDB │ │         │ │                  │  │
│  └─────────┘ └─────────┘ └──────────────────┘  │
│       │              │                         │
│       ▼              ▼                         │
│  ┌───────────────────────┐                     │
│  │   Fusion Retriever    │                     │
│  │   多层融合检索          │                     │
│  │   权重: vector 0.5    │                     │
│  │         graph  0.3    │                     │
│  │         time   0.2    │                     │
│  └───────────────────────┘                     │
└────────────────────────────────────────────────┘
```

### Embedding 链路

```
文本 → 本地 Ollama bge-m3 (优先)
         │ 失败
         ▼
     SiliconFlow API (回退 1)
         │ 失败
         ▼
     OpenAI API (回退 2)
         │ 失败
         ▼
     DashScope API (回退 3)

     ＋ 磁盘缓存 (SHA256 → 向量，避免重复计算)
```

### 跨 Agent 记忆提醒

当 Agent A 存储新记忆时，`MemoryReminder` 会扫描其他 Agent 的向量库，找到语义相似度超过阈值的条目，通过 Redis 推送 `remind` 通知。被提醒的 Agent 在下次 `recall()` 时自动纳入这些提醒。

### 记忆卫生

定期检查项：嵌入一致性、孤立节点比率、图连通性、时间链完整性、过期记忆占比。

---

## 6. LLM 路由策略

**原则**: 本地 Ollama **仅用于 bge-m3 嵌入**，所有 LLM 推理走云端。

### 回退链

```
百炼 (Bailian/阿里云) → SiliconFlow → DeepSeek → DashScope → OpenAI
```

### 任务-模型映射

| 任务类型 | 首选模型 | 说明 |
|---------|---------|------|
| `brief` | 轻量模型 | 简报、摘要、简短回复 |
| `analysis` | deepseek-chat | 深度分析、策略研判 |
| `code` | deepseek-coder | 代码生成、AI 辅助开发 |
| `default` | deepseek-chat | 通用任务 |

### 运行时偏好（Runtime Preference）

用户可通过 Web Dashboard 系统面板动态锁定 Provider/Model：
- **设置偏好**: `POST /api/llm/config {provider, model}` → 存入 Redis `openclaw:llm_preference`
- **清除偏好**: Provider 为空 → 恢复自动回退链路由
- **优先级**: 锁定的 Provider 被提升到回退链第一位，失败后仍走正常回退

### 限流处理

- 检测 HTTP 429 / rate limit 错误
- 指数退避: 60s → 120s → 240s
- 自动切换到下一个 provider
- `llm_status` 命令可查看各 provider 状态

---

## 7. 数据源架构

### 双源切换

```
Market / News Agent
        │
        ▼
  DataSourceRouter
        │
   ┌────┴────┐
   ▼         ▼
主 API      AKShare
192.168.    (备用)
1.138
```

- **主 API** 连续 3 次失败 → 自动切换到 AKShare
- 切换后每 **5 分钟** 尝试回切主 API
- `data_source_status` 命令可查看当前源状态

### 时间感知

`market_time.py` 为所有分析提供交易时段上下文：

| 阶段 | 时间 | 数据标签 |
|------|------|---------|
| `PRE_MARKET` | 09:00-09:15 | 盘前预热数据 |
| `AUCTION` | 09:15-09:30 | 集合竞价数据 |
| `MORNING` | 09:30-11:30 | 实时盘中数据 |
| `LUNCH_BREAK` | 11:30-13:00 | 上午收盘数据 |
| `AFTERNOON` | 13:00-15:00 | 实时盘中数据 |
| `POST_CLOSE` | 15:00-17:00 | 今日收盘数据 |
| `NIGHT` | 17:00+ | 今日收盘数据 |
| `NON_TRADING_DAY` | 周末/节假日 | 最近交易日数据 |

盘中分析自动注入"侧重实时动态变化和短线信号"的 prompt 指引。

---

## 8. 多渠道通知系统

### NotifyRouter 架构

```
Orchestrator / 规则 / Agents
         │
         ▼
  ┌─────────────────┐
  │   NotifyRouter   │
  │   4 级优先级      │
  │   pending 队列   │
  │   ACK 确认       │
  └───┬─────────┬───┘
      │         │
      ▼         ▼
┌──────────┐ ┌──────────┐
│ Telegram │ │  飞书     │
│ (主通道)  │ │ (Webhook) │
│ 心跳 10s │ │ 心跳 10s │
│ 4 Topics │ │ 单群推送  │
└──────────┘ └──────────┘
      ↕           ↕
   双向健康检查 (30s)
   离线 >60s → launchctl kickstart
```

### 消息优先级

| 级别 | 说明 | 示例 |
|------|------|------|
| `CRITICAL` | 必须到达 | 系统故障告警 |
| `HIGH` | 重要 | 盘前提醒、收盘复盘 |
| `NORMAL` | 一般 | 常规查询回复 |
| `LOW` | 低优 | 任务进度更新 |

### Telegram Topics 映射

| 话题 | 内容 |
|------|------|
| 📊 行情推送 | 涨停板、板块、热股、定时行情 |
| 🔍 查询响应 | 用户主动查询的回复 |
| 📈 策略分析 | 复盘、策略研判、风险预警 |
| ⚙️ 系统运维 | 健康检查、任务进度、系统告警 |

### 故障自修复

两个 Bot 每 30 秒互相检查对方心跳，如果对方离线超过 60 秒，自动执行 `launchctl kickstart` 重启。

---

## 9. 长期任务管理

### 命令

| Telegram 命令 | 说明 |
|--------------|------|
| `/task_new <预设名>` | 启动预设任务 |
| `/jobs` 或 `/task_list` | 查看最近任务列表 |
| `/task_status <task_id>` | 查看具体任务详细进度 |
| `/task_cancel <task_id>` | 取消正在执行的任务 |

### 预设任务

| 预设 | 名称 | 步骤数 | 说明 |
|------|------|--------|------|
| `morning_prep` | 盘前准备 | 5 | 新闻 → 行情 → 分析 → 策略 → 推送 |
| `close_review` | 收盘复盘 | 7 | 行情 → 新闻 → 分析 → 复盘 → 板块 → 风险 → 推送 |
| `deep_research` | 深度研究 | 4 | 全量行情 → 新闻 → 深度分析 → 中期策略 |
| `memory_maintenance` | 记忆维护 | 5 | 健康检查 → 提醒 → SOUL → 卫生 → 渠道 |
| `quant_research` | 量化研究 | 6 | Alpha 策略 → 代码生成 → 回测 → 风控 → 决策 → 归档 |

### 特性

- **Redis 持久化**: 任务状态保存 7 天，Bot 重启不丢失
- **实时进度推送**: 每步开始/完成/失败均推送到系统话题
- **失败策略**: 每步可配 `stop`(停止) / `skip`(跳过) / `retry`(重试最多 2 次)
- **结果传递**: 前步输出自动注入后步参数

---

## 10. 规则引擎

### 规则类型

| 类型 | 说明 | 示例 |
|------|------|------|
| `cron` | 定时触发 | `"0 9 15 * * 1-5"` (工作日 9:15) |
| `event` | 事件触发 | 涨停数量超阈值 |
| `interval` | 间隔触发 | 每 4 小时 |

### 已配置规则

| 规则 | 触发 | 动作 |
|------|------|------|
| 涨停异常报警 | 事件: 涨停数骤增 | market.get_limitup → notify (高优先级) |
| 盘前提醒 | Cron: 09:15 工作日 | market.get_summary → notify |
| 盘中行情 | Cron: 10:00, 14:00 | market.get_all_raw → notify |
| 收盘总结 | Cron: 15:05 | market.get_summary → notify |
| 策略师复盘 | Cron: 15:10 | strategist.daily_review → notify |
| 板块研判 | Cron: 15:20 | strategist.sector_thesis → notify |
| 每日简报 | Cron: 08:30 | orchestrator.daily_briefing |
| 记忆备份 | Cron: 03:00 | orchestrator.memory_backup |
| 记忆压缩 | Cron: 04:00 | orchestrator.memory_compress |
| 记忆健康 | Interval: 6h | orchestrator.memory_health |
| 记忆提醒 | Interval: 2h | orchestrator.memory_remind |
| SOUL 检查 | Interval: 1h | orchestrator.soul_check |
| 渠道健康 | Interval: 4h | orchestrator.channel_status |
| 记忆卫生 | Cron: 02:00 | orchestrator.memory_hygiene |
| 财报跟踪 | Cron: 20:00 | strategist.ask_strategy |

---

## 11. 安全体系

### InputGuard (输入防护)

- **Prompt 注入检测**: 正则匹配常见注入模式
- **长度限制**: 防止超长输入消耗资源
- **控制字符过滤**: 移除不可见/恶意字符

### SoulGuardian (身份守护)

- 对 `SOUL.md` 和 Skills YAML 文件计算 SHA256 基线
- 定时检查文件哈希是否变化
- 检测到篡改 → Redis 告警 + Telegram 系统话题通知
- `soul_accept` 命令可确认合法更新

### 部署隔离

- 所有 Agent 运行在 `clawagent` 用户下
- 日志目录 `/Users/clawagent/logs/`
- 配置文件权限 `600` (仅所有者可读写)
- Dev Agent SSH 命令黑名单: `rm -rf`, `mkfs`, `dd`, `shutdown`, `reboot`

---

## 12. 部署与运维

### 一键部署

```bash
sudo bash /Users/zayl/openclaw-deploy/install-agents.sh
```

### 部署流程

1. 检查/启动 Redis
2. 创建目录结构 (`/Users/clawagent/openclaw/`)
3. 复制所有 Agent 代码、配置、SOUL、Skills
4. 创建 Python 虚拟环境、安装依赖
5. 拉取 Ollama bge-m3 模型
6. 部署 LaunchAgent plist
7. 启动所有服务
8. 验证服务状态

### 服务管理

```bash
# 查看所有服务
sudo launchctl print gui/503 | grep openclaw

# 重启指定服务
sudo launchctl kickstart -k gui/503/com.openclaw.<name>

# 查看日志
tail -f /Users/clawagent/logs/<name>.log    # 标准输出
tail -f /Users/clawagent/logs/<name>.err    # 错误日志
```

### 服务列表

| 服务 | 进程名 |
|------|--------|
| 编排器 | `com.openclaw.orchestrator` |
| 行情 Agent | `com.openclaw.market-agent` |
| 分析 Agent | `com.openclaw.analysis-agent` |
| 新闻 Agent | `com.openclaw.news-agent` |
| 策略 Agent | `com.openclaw.strategist-agent` |
| 开发 Agent | `com.openclaw.dev-agent` |
| 浏览器 Agent | `com.openclaw.browser-agent` |
| 桌面 Agent | `com.openclaw.desktop-agent` |
| Telegram Bot | `com.openclaw.telegram-bot` |
| 飞书 Bot | `com.openclaw.feishu-bot` |
| Web Dashboard | `com.openclaw.webchat` |

### 配置文件

| 文件 | 说明 |
|------|------|
| `telegram.env` | Telegram Bot Token、群 ID、话题 ID、LLM 配置 |
| `feishu.env` | 飞书 Webhook URL、可选应用凭证 |
| `webchat.env` | Dashboard 端口、登录凭据 |
| `.env` | 全局环境变量（百炼 API Key 等） |
| `rules.yaml` | 规则引擎配置 |

---

## 13. 文件清单

### 代码结构

```
openclaw-deploy/
├── telegram_bot.py          # Telegram 前端
├── feishu_bot.py            # 飞书前端
├── webchat_api.py           # Web Dashboard 后端 (FastAPI + Uvicorn)
├── webchat_frontend.html    # Web Dashboard 前端 (React 18 SPA)
├── webchat_agent.py         # Web Chat 旧版 (Chainlit, 备用)
├── webchat_app.py           # Web Dashboard 旧版 (aiohttp, 备用)
├── rules.yaml               # 规则引擎配置
├── install-agents.sh        # 一键部署脚本
├── telegram.env             # Telegram + LLM API Key 配置
├── feishu.env               # 飞书配置
├── webchat.env              # Web Chat 配置
│
├── agents/
│   ├── base.py              # Agent 基类 (BaseAgent, AgentMessage)
│   ├── orchestrator.py      # 编排器
│   ├── market_agent.py      # 行情数据
│   ├── analysis_agent.py    # 深度分析
│   ├── news_agent.py        # 新闻舆情
│   ├── strategist_agent.py  # 策略研判
│   ├── dev_agent.py         # 远程开发
│   ├── backtest_agent.py    # 量化回测沙箱
│   ├── quant_pipeline.py    # 量化研究 Pipeline 编排
│   ├── notify_router.py     # 多渠道通知路由
│   ├── task_manager.py      # 长期任务管理
│   ├── llm_router.py        # LLM 云端路由 (含运行时偏好)
│   ├── market_time.py       # 交易时段感知
│   │
│   ├── memory/
│   │   ├── __init__.py      # 记忆系统入口
│   │   ├── config.py        # 集中配置
│   │   ├── embedding.py     # 混合 Embedding 客户端
│   │   ├── memory_mixin.py  # Agent 记忆混入
│   │   ├── reminder.py      # 跨 Agent 记忆提醒
│   │   ├── vector_store.py  # HNSW 向量存储 (ChromaDB)
│   │   ├── knowledge_graph.py # 知识图谱 (NetworkX)
│   │   ├── timeline.py      # 时间索引
│   │   ├── retriever.py     # 融合检索器
│   │   ├── lifecycle.py     # 记忆生命周期
│   │   ├── input_guard.py   # 输入安全防护
│   │   └── soul_guardian.py # SOUL 身份守护
│   │
│   ├── data_sources/
│   │   ├── akshare_source.py # AKShare 数据适配
│   │   └── source_router.py  # 数据源路由器
│   │
│   ├── souls/               # Agent 身份定义
│   │   ├── orchestrator.md
│   │   ├── market.md
│   │   ├── analysis.md
│   │   ├── news.md
│   │   ├── strategist.md
│   │   ├── dev.md
│   │   └── backtest.md
│   │
│   └── skills/              # Agent 技能定义
│       ├── orchestrator_skills.yaml
│       ├── market_skills.yaml
│       ├── analysis_skills.yaml
│       ├── news_skills.yaml
│       ├── strategist_skills.yaml
│       └── dev_skills.yaml
│
├── data/                    # 数据目录
│   ├── history_cache/       # 历史行情缓存 (Parquet/CSV)
│   └── decision_ledger/     # 量化决策账本 (Markdown)
│
├── workspace/
│   └── strategies/          # 量化策略代码
│
├── docs/                    # 项目文档
│   ├── openclaw-index.md                  # 文档总入口
│   ├── openclaw-technical-doc.md          # 项目技术文档 (本文件)
│   ├── openclaw-deployment-guide.md       # 部署与运维指南
│   ├── openclaw-security-report.md        # 安全审计报告
│   ├── openclaw-v1.2-advanced-design.md   # 进阶架构设计
│   ├── openclaw-v1.3-quant-system.md      # 量化系统设计方案
│   ├── openclaw-plan-multi-agent.md       # 多 Agent 拆分方案
│   ├── openclaw-plan-multichannel.md      # 多渠道消息架构方案
│   ├── openclaw-plan-telegram-routing.md  # Telegram 路由方案
│   └── openclaw-plan-embedding-memory.md  # 三层记忆系统方案
│
└── launchagents/            # macOS LaunchAgent plist
    ├── com.openclaw.orchestrator.plist
    ├── com.openclaw.market-agent.plist
    ├── com.openclaw.analysis-agent.plist
    ├── com.openclaw.news-agent.plist
    ├── com.openclaw.strategist-agent.plist
    ├── com.openclaw.dev-agent.plist
    ├── com.openclaw.backtest-agent.plist
    ├── com.openclaw.browser-agent.plist
    ├── com.openclaw.desktop-agent.plist
    ├── com.openclaw.telegram-bot.plist
    ├── com.openclaw.feishu-bot.plist
    └── com.openclaw.webchat.plist
```

---

---

## 14. Web Dashboard

### 技术栈

- **后端**: FastAPI + uvicorn（SSE 流式响应）
- **前端**: React 18 + Tailwind CSS（CDN 加载，无构建步骤）
- **认证**: HTTP Basic Auth（配置在 `webchat.env`）
- **设计**: 对标 Claude Coworker 三栏布局

### 界面布局

```
┌──────────┬──────────────────────┬────────────┐
│ Sidebar  │    Main Area         │  Artifact  │
│          │                      │  Panel     │
│ 新对话    │  💬 Chat / 📈 行情   │            │
│ ─────    │  📋 任务 / ⚙️ 系统    │  长文本    │
│ 导航     │                      │  自动展开   │
│ ─────    │  Messages...         │            │
│ 对话历史  │                      │            │
│ ─────    │  ┌──────────────┐    │            │
│ Agent    │  │ Input        │    │            │
│ 状态灯   │  └──────────────┘    │            │
└──────────┴──────────────────────┴────────────┘
```

### 功能

| 视图 | 功能 |
|------|------|
| 💬 对话 | SSE 流式响应、气泡消息、快捷命令、多对话线程 |
| 📈 行情 | 涨停/连板/板块/热股、自由提问分析 |
| 📋 任务 | 5 个预设任务创建（含量化研究）、列表刷新、详情/取消 |
| ⚙️ 系统 | LLM/Embedding/数据源/SOUL/记忆 6 面板、LLM Provider/Model 动态选择、自定义命令 |
| 📊 量化 | 策略研究启动、回测结果查看、决策账本浏览、缓存管理 |

### API 端点

| 路径 | 方法 | 说明 |
|------|------|------|
| `/` | GET | React SPA 页面 |
| `/api/overview` | GET | Agent + 渠道状态 JSON |
| `/api/command` | POST | 执行命令 `{cmd, args}` |
| `/api/chat` | POST | SSE 流式对话 `{message}` |
| `/api/tasks` | GET | 任务列表 |
| `/api/tasks/create` | POST | 创建预设任务 `{preset}` |
| `/api/tasks/{id}` | GET | 任务详情 |
| `/api/tasks/{id}/cancel` | POST | 取消任务 |
| `/api/llm/config` | GET | LLM Provider/Model 配置与状态 |
| `/api/llm/config` | POST | 设置/清除 LLM 首选 Provider `{provider, model}` |

### 特性

- **SSE 流式响应** — 输入后立即显示思考动画，结果逐块流入
- **三栏布局** — 侧边栏 + 主区域 + Artifact 面板（长文本自动展开）
- **多对话线程** — 可创建多个独立对话，侧边栏切换
- **Agent 状态灯** — 侧边栏底部 9 个 Agent（含 Backtest）实时状态，15 秒自动刷新
- **LLM Provider 选择** — 系统面板中可动态切换 Provider/Model，支持自动路由与手动锁定
- **暗色主题** — Zinc 色系，SF Pro 字体

---

## 15. 量化回测系统

### 整体架构

```
                          用户发起 /quant <主题>
                                  │
                                  ▼
                        ┌──────────────────┐
                        │   Orchestrator   │
                        │   启动 Pipeline   │
                        └────────┬─────────┘
                                 │
                   ┌─────────────┼─────────────┐
                   ▼             ▼              ▼
            ┌─────────┐  ┌───────────┐  ┌───────────┐
            │  Alpha   │  │   Quant   │  │   Risk    │
            │Researcher│  │   Coder   │  │  Analyst  │
            │ (LLM)    │  │  (LLM)    │  │  (LLM)    │
            └────┬────┘  └─────┬─────┘  └─────┬─────┘
                 │             │              │
                 │        ┌────▼────┐         │
                 │        │Backtest │         │
                 │        │ Agent   │◄────────┘
                 │        │ (沙箱)   │
                 │        └────┬────┘
                 │             │
                 ▼             ▼
            ┌─────────────────────────────┐
            │     Portfolio Manager       │
            │     (LLM 最终决策)          │
            └──────────────┬──────────────┘
                           │
                           ▼
                    Decision Ledger
                    (Markdown 归档)
```

### 量化 Pipeline 流程

| 步骤 | 角色 | 引擎 | 说明 |
|------|------|------|------|
| 1 | Alpha Researcher | LLM | 根据主题生成 3-5 个 Alpha 策略灵感 |
| 2 | Quant Coder | LLM | 将策略灵感转化为 Python 回测代码 |
| 3 | Backtest Engine | Python 沙箱 | 在隔离环境中执行回测代码 |
| 4 | Risk Analyst | LLM | 审核回测结果，评估风险指标 |
| 5 | Portfolio Manager | LLM | 综合所有结果给出最终投资建议 |
| 6 | Decision Ledger | 文件系统 | 归档策略、代码、结果到 Markdown |

- 步骤 2-4 支持**迭代优化**：如果回测失败或风控不通过，自动回到步骤 2 重新生成代码（最多 3 轮）
- 每个步骤通过 `notify_fn` 向前端实时推送进度

### 安全沙箱

BacktestAgent 执行用户生成代码时实施多层安全控制：

| 安全措施 | 说明 |
|----------|------|
| **导入白名单** | 仅允许 `backtrader`, `pandas`, `numpy`, `pandas_ta`, `vectorbt`, `math`, `datetime`, `json` |
| **危险模块拦截** | 禁止 `os`, `sys`, `subprocess`, `shutil`, `socket`, `http`, `urllib`, `pathlib`, `importlib` |
| **进程隔离** | 通过 `subprocess.run()` 在独立进程中执行 |
| **超时限制** | 单次回测最长 10 分钟 |
| **无限循环检测** | 静态扫描 `while True` 等模式 |

### 数据管理

| 目录 | 用途 |
|------|------|
| `data/history_cache/` | 历史行情数据本地缓存（Parquet/CSV） |
| `data/decision_ledger/` | 决策账本归档（每个策略一个 Markdown 文件） |
| `workspace/strategies/` | 量化策略代码存储 |

### 量化依赖

| 包名 | 用途 |
|------|------|
| `backtrader` | 经典回测框架 |
| `pandas-ta` | 技术指标计算 |
| `vectorbt` | 向量化回测与组合分析 |
| `pyarrow` | Parquet 数据高效读写 |
| `akshare` | A 股历史行情数据源 |

---

> **维护说明**: 本文档随代码同步更新。如有架构变更，请同步修改此文件。

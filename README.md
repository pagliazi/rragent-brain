# RRAgent — 统一 A 股多智能体量化系统

A-share multi-agent quantitative trading system with unified harness + execution layer.

## 项目路径

```
~/RRAgent-Universe/rragent-brain/   # 项目根目录（唯一真实来源）
├── rragent/                        # Harness 控制层 (Python 包, ~13,000 行)
├── agents/                         # PyAgent 执行层 (12 个 agent, ~22,000 行)
├── rragent_server.py               # 主 FastAPI 服务 (5,292 行)
├── webchat_api.py                  # 旧 FastAPI 服务 (保留兼容, 4,597 行)
├── static/js/                      # 新前端 (11 个 JSX, rragent_server 使用)
├── webchat_static/                 # 旧前端 (webchat_api 使用)
├── tests/                          # 全部测试 (~2,100 行)
├── plans/                          # 架构规划
│   └── rragent-harness-restructure-plan.md  # 13 步重构 Plan (2,555 行)
├── n8n_workflows/                  # 13 个 n8n 自动化工作流
├── registry.yaml                   # Agent/Service 注册表 (Single Source of Truth)
├── rules.yaml                      # 自动化规则
├── pyproject.toml                  # Python 包定义
└── rrclaw                          # bash 管理 CLI
```

## 双层架构

### 控制层: `rragent/` Harness 包

核心 LLM 代理循环，管理工具调用、上下文压缩、容错和自学习。

```
rragent/
├── runtime/
│   ├── conversation.py         # ConversationRuntime — 异步生成器 LLM 循环
│   ├── session.py              # JSONL 会话持久化 (256KB 轮转 + 崩溃恢复)
│   ├── providers/              # 多 Provider 路由
│   │   ├── router.py           # ProviderRouter 降级链 (Anthropic→DashScope→OpenAI→Ollama)
│   │   ├── credential_pool.py  # 凭证轮转 (4 策略: fill_first/round_robin/random/least_used)
│   │   └── anthropic.py / dashscope.py / openai_compat.py / simple.py
│   └── resilience/             # 7 层容错
│       ├── api_retry.py        # 指数退避重试
│       ├── circuit_breaker.py  # 熔断器
│       ├── error_classifier.py # 错误分类 → 恢复建议
│       ├── health_monitor.py   # 组件健康监控
│       └── recovery_recipes.py # 7 种故障恢复方案
├── tools/
│   ├── registry.py             # GlobalToolRegistry (Tier0/1/2)
│   ├── executor.py             # 并发/串行工具调度 (分区执行)
│   ├── search.py               # ToolSearch — Tier0 元工具，惰性加载 Tier1
│   ├── index_builder.py        # 从 skills YAML 自动生成工具注册表
│   ├── builtin/                # 内置工具 (factor_tools, market_query, bash, file_ops, canvas)
│   ├── pyagent/bridge.py       # Redis Pub/Sub → 12 个 PyAgent
│   ├── hermes/runtime.py       # HermesNativeRuntime (线程池)
│   └── mcp/                    # MCP 集成
│       ├── server.py           # RRAgent 作为 MCP Server
│       ├── client.py           # 连接外部 MCP Server
│       └── reachrich_server.py # ReachRich 行情 MCP
├── context/
│   ├── engine.py               # 5 层上下文压缩
│   │                           # tool_result_budget → history_snip → microcompact
│   │                           # → context_collapse → autocompact
│   └── memory/                 # 3 级记忆 (会话内/用户级/系统级)
├── evolution/                  # 4 层自学习
│   ├── background_review.py    # Loop2: 会话内反思 (Hermes fork)
│   ├── engine.py               # Loop3: 跨会话进化 (Redis Stream)
│   ├── gepa_pipeline.py        # Loop4: 日级 GEPA 优化
│   ├── autoresearch_loop.py    # 策略实验循环
│   └── skill_guard.py / skill_creator.py / pattern_detector.py ...
├── permissions/                # 4 级权限 (safe/aware/consent/critical)
├── workers/                    # Worker 状态机
│   ├── boot.py                 # INIT→DISCOVERING→VALIDATING→READY→RUNNING→DEGRADED
│   ├── coordinator.py          # 并发启动 + 健康检查
│   └── task_packet.py          # 优先级任务队列
└── channels/
    ├── gateway.py              # Gateway WebSocket (v3 protocol, :7790)
    ├── acp_runtime.py          # ACP WebSocket Server (OpenClaw 通信协议)
    └── webhook.py              # HTTP 回调
```

### 执行层: `agents/` PyAgent

12 个专用 Python Agent，通过 Redis Pub/Sub 接受任务。

| Agent | 职责 | 运行用户 |
|-------|------|----------|
| orchestrator | 任务分发，协调所有 agent | clawagent |
| market | 行情数据查询 + 推送 | clawagent |
| analysis | 技术分析，板块轮动 | clawagent |
| news | 新闻情绪，舆情监控 | clawagent |
| strategist | 策略生成，信号整合 | clawagent |
| dev | 代码生成，工具开发 | zayl |
| backtest | 策略回测 (backtrader/vectorbt) | clawagent |
| browser | Web 信息采集 | clawagent |
| desktop | GUI 自动化 (AppleScript) | zayl |
| apple | Apple Events 控制 | zayl |
| general | 通用任务处理 | clawagent |
| monitor | 系统监控，告警 | clawagent |

PyAgent 附属模块（`agents/`）：
- `memory/` — 三层记忆 (ChromaDB + NetworkX + Timeline) + reflection_engine + soul_guardian
- `alpha_digger.py` — Alpha 因子挖掘引擎
- `meme_digger.py` — 热点/游资题材挖掘
- `factor_library.py` — 因子库 (Redis)
- `quant_pipeline.py` — 量化管线

### 服务层

| 服务 | 文件 | 状态 | 端口 |
|------|------|------|------|
| **rragent** (主) | `rragent_server.py` | autostart=true | 7789 |
| webchat (旧) | `webchat_api.py` | autostart=false (compat) | 7789 |
| n8n | `n8n_wrapper.py` | autostart=true | 5678 |
| telegram | `telegram_bot.py` | agent管理 | — |
| feishu | `feishu_bot.py` | agent管理 | — |

### 通信拓扑

```
┌─ Telegram bot ─┐
├─ 飞书 bot ─────┤ → Redis openclaw:orchestrator
└─ WebUI (:7789) ┘         ↓
                    rragent_server.py (FastAPI)
                    ├── ConversationRuntime (LLM 循环)
                    │   ├── ToolSearch (Tier0 ~8 + Tier1 ~120 lazy)
                    │   ├── ProviderRouter (Anthropic/DashScope/OpenAI 降级)
                    │   ├── ContextEngine (5 层压缩)
                    │   └── EvolutionEngine (4 层自学习)
                    │             ↓ Redis rragent:{agent_name}
                    ├── PyAgent 执行层 (12 agents)
                    │   ├── backtest (vectorbt/backtrader)
                    │   ├── market / analysis / news / strategist
                    │   └── alpha_digger / factor_library
                    ├── ReachRich API (行情数据, :192.168.1.138)
                    ├── Hermes Agent (本地 PTC, BRAIN_PATH)
                    └── MCP Server (:7791, Claude Desktop/Cursor)
```

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `BRAIN_PATH` | 自动检测 (项目根目录) | rragent-brain 路径，连接两层 |
| `RRAGENT_PORT` | `7789` | 服务端口 |
| `RRAGENT_DEFAULT_MODEL` | `qwen3.5-plus` | 默认 LLM 模型 |
| `OPENAI_API_KEY` | — | DashScope/OpenAI 兼容 API Key |
| `OPENAI_BASE_URL` | `https://coding.dashscope.aliyuncs.com/v1` | LLM API 地址 |
| `ANTHROPIC_API_KEY` | — | Claude API Key (可选) |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | Redis 连接 |
| `REACHRICH_URL` | `http://192.168.1.138/api` | ReachRich API |
| `REACHRICH_TOKEN` | — | ReachRich API Key (`rk_...`) |
| `JWT_SECRET` | `rragent-secret` | JWT 签名密钥 |

## 部署

```bash
# 主机 (zayl@macOS)
./rragent start all          # 启动所有 agents + rragent service
./rragent deploy             # SCP 同步到 clawagent@192.168.1.x:/Users/clawagent/openclaw/
./rragent status             # 查看所有组件状态

# 安装 Python 包
pip install -e ".[full]"

# 运行阶段测试
python run_p0.py            # P0: 核心 LLM 循环
python run_p1.py            # P1: 工具系统
python run_p2.py            # P2: 容错体系
python run_p3.py            # P3: 自学习
```

## 关键路径

- **Git remote**: `http://192.168.1.136/zayl/openclaw-brain.git` (私有 Gitea)
- **凭证**: `clawagent` 用户的 env 文件 (`webchat.env`, `telegram.env`, `feishu.env`)
- **重构 Plan**: `plans/rragent-harness-restructure-plan.md` (P0→P5 全部已实现 ✅)
- **旧项目** (已合并): `/Users/zayl/rragent/` (harness 源) + 本目录的 `agents/` (执行层)

## 实施状态 (P0→P5 全部完成)

| 阶段 | 内容 | 状态 |
|------|------|------|
| P0 | ConversationRuntime + JSONL + Gateway | ✅ |
| P1 | ToolSearch + 5 层上下文压缩 | ✅ |
| P2 | 7 层容错 + ProviderRouter + 凭证池 | ✅ |
| P3 | Background Review + Evolution Engine + Skill | ✅ |
| P4 | GEPA + Autoresearch + Worker Boot | ✅ |
| P5 | ACP + Canvas + MCP Server | ✅ |

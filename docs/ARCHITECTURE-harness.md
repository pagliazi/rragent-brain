# RRAgent Harness 控制层架构文档

> **注意**: 本文档描述 `rragent/` Harness 控制层。完整系统架构（含 agents/ 执行层）请见根目录 `README.md`。

## 项目路径

```
~/RRAgent-Universe/rragent-brain/   # 统一项目根目录（合并后唯一真实来源）
├── rragent/                        # Harness 控制层 Python 包（本文档描述此层）
├── agents/                         # PyAgent 执行层 (12 个 agent)
├── rragent_server.py               # 主 FastAPI 服务入口 (5,292 行)
├── webchat_api.py                  # 旧 FastAPI 服务 (保留兼容, 4,597 行)
├── static/                         # 新前端 (React JSX + Tailwind)
├── pyproject.toml                  # Python 包定义
└── tests/                          # 全部测试
```

## 核心包结构 (`rragent/`)

```
rragent/
├── __init__.py                     # 包定义 v0.1.0
├── __main__.py                     # python -m rragent 入口
│
├── runtime/                        # 核心 LLM 循环
│   ├── conversation.py             # ConversationRuntime (异步生成器)
│   ├── session.py                  # JSONL 会话持久化
│   ├── config.py                   # 配置加载 (YAML → 环境变量 → 默认值)
│   ├── prompt.py                   # System prompt 构建
│   ├── hooks.py                    # 工具前/后生命周期钩子
│   ├── server.py                   # [未用] 独立 server 实现
│   ├── providers/                  # LLM 提供商
│   │   ├── simple.py               # OpenAI 兼容 provider (百炼/DashScope)
│   │   ├── router.py               # ProviderRouter 降级链
│   │   ├── credential_pool.py      # 凭证轮转 (4 策略)
│   │   ├── anthropic.py            # Anthropic Claude
│   │   ├── dashscope.py            # 通义千问
│   │   ├── openai_compat.py        # Ollama / vLLM
│   │   └── base.py                 # Provider 基类
│   └── resilience/                 # 容错体系
│       ├── api_retry.py            # 指数退避重试
│       ├── circuit_breaker.py      # 熔断器
│       ├── error_classifier.py     # 错误分类 → 恢复建议
│       ├── health_monitor.py       # 组件健康检查
│       └── recovery_recipes.py     # 7 种故障恢复方案
│
├── tools/                          # 工具体系
│   ├── base.py                     # Tool 基类 + ToolSpec + ToolResult
│   ├── registry.py                 # GlobalToolRegistry (Tier 0/1/2)
│   ├── executor.py                 # 并发/串行工具调度
│   ├── search.py                   # ToolSearch 惰性加载 (Tier 0 元工具)
│   ├── index_builder.py            # 从 skills YAML 自动生成工具注册表
│   ├── builtin/                    # 内置工具
│   │   ├── factor_tools.py         # 因子挖掘/评估/融合/列表/回测
│   │   ├── market_query.py         # 行情查询
│   │   ├── bash.py                 # Shell 执行
│   │   ├── file_ops.py             # 文件读写
│   │   └── canvas.py               # ECharts 可视化
│   ├── pyagent/                    # PyAgent Redis 集成
│   │   └── bridge.py               # Redis Pub/Sub → 12 个 agent
│   ├── hermes/                     # Hermes Agent 集成
│   │   └── runtime.py              # HermesNativeRuntime (线程池)
│   └── mcp/                        # Model Context Protocol
│       ├── server.py               # RRAgent 作为 MCP Server
│       ├── client.py               # 连接外部 MCP Server
│       └── reachrich_server.py     # ReachRich 行情 MCP
│
├── context/                        # 上下文工程
│   ├── engine.py                   # 5 层压缩 (ContextProvider)
│   └── memory/                     # 3 级记忆
│       ├── tier1_session.py        # 会话内 LRU
│       ├── tier2_user.py           # 用户级 USER.md
│       └── tier3_system.py         # 系统级 JSON (置信度衰减)
│
├── evolution/                      # 自学习系统
│   ├── background_review.py        # Loop 2: 会话内反思
│   ├── engine.py                   # Loop 3: 跨会话进化
│   ├── gepa_pipeline.py            # Loop 4: 日级 GEPA 优化
│   ├── autoresearch_loop.py        # 策略实验循环
│   ├── pattern_detector.py         # 重复工具链检测
│   ├── failure_detector.py         # 失败模式分析
│   ├── correction_tracker.py       # 纠错记录
│   ├── skill_creator.py            # 自动技能生成
│   ├── skill_guard.py              # 技能安全扫描
│   └── perf_detector.py            # 性能退化检测
│
├── skills/                         # 技能管理
│   ├── loader.py                   # YAML+Markdown 技能加载
│   ├── executor.py                 # 技能执行 + 触发匹配
│   └── sync.py                     # 双向技能同步
│
├── workers/                        # 多组件协调
│   ├── boot.py                     # Worker 状态机 (6 状态)
│   ├── coordinator.py              # 并发启动 + 健康检查
│   └── task_packet.py              # 优先级任务队列
│
├── channels/                       # 通道接入
│   ├── gateway.py                  # Gateway WebSocket (v3 protocol)
│   ├── acp_runtime.py              # ACP WebSocket Server (:7790)
│   └── webhook.py                  # HTTP 回调
│
├── commands/                       # 斜杠命令
│   ├── evolve.py                   # /evolve status|run|gepa
│   └── research.py                 # /research start|stop
│
├── permissions/                    # 权限控制
│   ├── policy.py                   # 4 级: safe/aware/consent/critical
│   └── enforcer.py                 # 工作空间边界
│
└── data_sources/                   # 数据接入
    └── reachrich_stream.py         # Redis Pub/Sub 实时行情消费
```

## 前端结构 (`static/`)

```
static/
├── index.html                      # 入口 (React CDN + Babel + Tailwind)
└── js/
    ├── 01-core.jsx                 # 认证、API、Toast、共享组件
    ├── 02-nav.jsx                  # 导航栏 (桌面侧边 + 移动底部 Tab)
    ├── 03-dashboard.jsx            # 仪表盘 (Agent 状态 + 快捷操作)
    ├── 04-chat.jsx                 # 对话面板 (SSE 流式 + 工具卡片 + Token 计数)
    ├── 05-market.jsx               # 行情面板 (涨停/板块/热门/资讯)
    ├── 06-quant.jsx                # 量化面板 (因子库/回测/策略/融合)
    ├── 07-tools.jsx                # 工具面板 (翻译/搜索/代码/计算)
    ├── 08-system.jsx               # 系统面板 (LLM 配置/监控/诊断/用量)
    ├── 09-app.jsx                  # 应用入口 (路由/分栏/SSE 事件)
    ├── 10-autoresearch.jsx         # 自动研究面板
    └── 11-yao.jsx                  # 策略分析面板
```

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `RRAGENT_PORT` | `7789` | 服务端口 |
| `RRAGENT_HOST` | `0.0.0.0` | 绑定地址 |
| `RRAGENT_DEFAULT_MODEL` | `qwen3.5-plus` | 默认 LLM 模型 |
| `RRAGENT_REDIS_MODE` | `false` | Redis 订阅模式 (替代 orchestrator) |
| `RRAGENT_LISTEN_CHANNEL` | `openclaw:orchestrator` | Redis 监听频道 |
| `OPENAI_API_KEY` | — | LLM API 密钥 (百炼/OpenAI 兼容) |
| `OPENAI_BASE_URL` | `https://coding.dashscope.aliyuncs.com/v1` | LLM API 地址 |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | Redis 连接 |
| `BRAIN_PATH` | 自动检测 (项目根目录) | rragent-brain 路径，连接 harness 与 agents/ 执行层 |
| `REACHRICH_URL` | `http://192.168.1.138/api` | ReachRich API 地址 |
| `REACHRICH_TOKEN` | — | ReachRich API Key (`rk_...`) |
| `JWT_SECRET` | `rragent-secret` | JWT 签名密钥 |

## Redis 频道

| 频道 | 用途 |
|------|------|
| `openclaw:orchestrator` | IM bot 消息入口 (Telegram/飞书 → RRAgent) |
| `openclaw:{agent}` | PyAgent 通信 (market/analysis/backtest 等) |
| `openclaw:rragent` | RRAgent 回复频道 |
| `openclaw:heartbeats` | Agent 心跳 (RRAgent 写 orchestrator key) |
| `openclaw:reply:{id}` | 请求-响应配对 |
| `rragent:execution_events` | Evolution Engine 执行事件流 |
| `rragent:plan_log:{id}` | 执行计划追踪 |

## 数据目录

```
~/.rragent/
├── workspace/          # 工作空间
│   └── SOUL.md         # 系统人格定义
├── skills/             # 用户技能
├── sessions/           # JSONL 会话文件
├── memory/             # 系统级记忆 (JSON)
├── experiments/        # 策略实验结果
├── traces/             # 执行追踪
└── tool_results/       # 大工具结果缓存
```

## 运行架构

```
┌─ Telegram bot ─┐
├─ 飞书 bot ─────┤ → Redis openclaw:orchestrator
└─ WebUI (:7789) ┘         ↓
                    rragent_server.py (FastAPI, 主服务)
                    ├── ConversationRuntime (LLM 异步生成器循环)
                    ├── ToolSearch (Tier0 ~8 + Tier1 ~120 lazy)
                    ├── ProviderRouter (Anthropic/DashScope/OpenAI 降级链)
                    ├── ContextEngine (5 层压缩)
                    ├── EvolutionEngine (4 层自学习)
                    │         ↓ Redis rragent:{agent_name}
                    ├── agents/ PyAgent 执行层 (12 agents)
                    │   ├── orchestrator / market / analysis / news / strategist
                    │   ├── dev / backtest / browser / desktop / apple / general / monitor
                    │   └── alpha_digger / meme_digger / factor_library / quant_pipeline
                    ├── ReachRich API (行情数据, 192.168.1.138)
                    ├── Hermes Agent (本地 PTC, BRAIN_PATH)
                    └── MCP Server (:7791, Claude Desktop/Cursor)
```

## 代码量

| 模块 | 文件 | 行数 |
|------|------|------|
| rragent/ (Harness 控制层) | ~75 | ~12,964 |
| agents/ (PyAgent 执行层) | ~60 | ~21,745 |
| rragent_server.py | 1 | 5,292 |
| webchat_api.py (旧, compat) | 1 | 4,597 |
| run_p*.py (阶段入口) | 4 | ~400 |
| static/ (新前端) | 12 | ~6,300 |
| webchat_static/ (旧前端) | — | — |
| tests/ (合并后) | ~8 | ~2,100 |
| **总计** | **~160+** | **~53,000+** |

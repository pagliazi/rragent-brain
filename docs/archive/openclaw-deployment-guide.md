# OpenClaw 多智能体系统 — 部署与运维指南

> **版本**: v6.0 | **最后更新**: 2026-02-25
> **项目**: OpenClaw A 股多智能体协作系统
> **运行环境**: Mac Mini M4 / macOS / `clawagent` 用户

## 架构

```
┌─────────────────────────────────────────────────────────┐
│                    Mac Mini M4                          │
│                                                         │
│  ┌──────────┐  ┌──────────────┐  ┌───────────────────┐ │
│  │   zayl    │  │ ollama_runner│  │    clawagent      │ │
│  │  (admin)  │  │  (standard)  │  │   (standard)      │ │
│  │           │  │              │  │                   │ │
│  │  Surge    │  │  Ollama      │  │  Orchestrator     │ │
│  │  :6152    │  │  :11434      │  │  9 Agents         │ │
│  │  :6153    │  │  bge-m3      │  │  Telegram Bot     │ │
│  │           │  │              │  │  飞书 Bot          │ │
│  │           │  │              │  │  Web Dashboard    │ │
│  │           │  │              │  │  :7789             │ │
│  └──────────┘  └──────────────┘  └───────────────────┘ │
│       │               │                │               │
│       └───────────────┴────────────────┘               │
│              127.0.0.1 本机通信                         │
└─────────────────────────────────────────────────────────┘
         │                                    │
    Surge 代理出网                     Telegram API polling
    (HTTP/SOCKS5)                     (outbound only)
```

## 服务一览

| 服务 | 用户 | 类型 | 端口 | 管理方式 |
|------|------|------|------|---------|
| Surge 代理 | zayl | App | 6152/6153 | Surge GUI |
| Ollama LLM | ollama_runner | LaunchDaemon | 11434 | `sudo launchctl` |
| Orchestrator | clawagent | LaunchAgent | - | `sudo launchctl` |
| Market Agent | clawagent | LaunchAgent | - | `sudo launchctl` |
| Analysis Agent | clawagent | LaunchAgent | - | `sudo launchctl` |
| News Agent | clawagent | LaunchAgent | - | `sudo launchctl` |
| Strategist Agent | clawagent | LaunchAgent | - | `sudo launchctl` |
| Dev Agent | clawagent | LaunchAgent | - | `sudo launchctl` |
| Backtest Agent | clawagent | LaunchAgent | - | `sudo launchctl` |
| Browser Agent | clawagent | LaunchAgent | - | `sudo launchctl` |
| Desktop Agent | clawagent | LaunchAgent | - | `sudo launchctl` |
| Telegram Bot | clawagent | LaunchAgent | - | `sudo launchctl` |
| 飞书 Bot | clawagent | LaunchAgent | - | `sudo launchctl` |
| Web Dashboard | clawagent | LaunchAgent | 7789 | `sudo launchctl` |

---

## Web Dashboard (FastAPI + React)

### 访问

浏览器打开：**http://192.168.1.188:7789**
认证方式：HTTP Basic Auth（配置在 `webchat.env`）

### 功能视图

| 视图 | 功能 |
|------|------|
| 💬 对话 | SSE 流式响应、气泡消息、快捷命令、多对话线程 |
| 📈 行情 | 涨停/连板/板块/热股、自由提问分析 |
| 📋 任务 | 5 个预设任务创建（含量化研究）、列表刷新、详情/取消 |
| ⚙️ 系统 | LLM/Embedding/数据源/SOUL/记忆面板、Provider/Model 动态选择 |
| 📊 量化 | 策略研究启动、回测结果、决策账本、缓存管理 |

### 管理
```bash
sudo launchctl kickstart -k gui/503/com.openclaw.webchat      # 重启
sudo launchctl bootout gui/503/com.openclaw.webchat            # 停止
tail -f /Users/clawagent/logs/webchat.log                      # 日志
```

---

## Telegram Bot 命令一览

| 命令 | 功能 | 示例 |
|------|------|------|
| `/zt` | 涨停板 | `/zt` |
| `/lb` | 连板 | `/lb` |
| `/bk` | 板块 | `/bk` |
| `/rg` | 热股 | `/rg` |
| `/status` | Agent 状态 | `/status` |
| `/jobs` | 任务列表 | `/jobs` |
| `/task_new <预设>` | 启动预设任务 | `/task_new close_review` |
| `/task_status <id>` | 任务详情 | `/task_status abc123` |
| `/quant <主题>` | 启动量化研究 | `/quant 连板股动量策略` |
| `/ssh <host> <cmd>` | SSH 远程执行 | `/ssh root@10.0.0.5 uname -a` |
| `/sh <命令>` | Shell 命令 | `/sh ls -la ~/openclaw` |
| `/screen` | 截取 Mac 桌面 | `/screen` |

自由文本消息经 LLM 意图识别，自动路由到对应 Agent。

---

## 服务管理

### 一键部署
```bash
sudo bash /Users/zayl/openclaw-deploy/install-agents.sh
```

### Agent 管理（通用模式）
```bash
# 重启某个 Agent（替换 AGENT_NAME）
sudo launchctl kickstart -k gui/503/com.openclaw.AGENT_NAME

# 停止某个 Agent
sudo launchctl bootout gui/503/com.openclaw.AGENT_NAME

# 查看日志
tail -f /Users/clawagent/logs/AGENT_NAME.log
tail -f /Users/clawagent/logs/AGENT_NAME.err
```

### Agent 名称列表
`orchestrator` `market-agent` `analysis-agent` `news-agent` `strategist-agent` `dev-agent` `backtest-agent` `browser-agent` `desktop-agent` `telegram-bot` `feishu-bot` `webchat`

### Ollama
```bash
sudo launchctl kickstart system/com.local.ollama                   # 重启
curl http://127.0.0.1:11434                                        # 检查
curl http://127.0.0.1:11434/api/tags                               # 模型列表
```

### Redis
```bash
redis-cli ping                    # 检查
redis-cli hgetall openclaw:heartbeats   # Agent 心跳
```

---

## 预设任务

| 预设 | 名称 | 说明 |
|------|------|------|
| `morning_prep` | 盘前准备 | 新闻 → 行情 → 分析 → 策略 → 推送 |
| `close_review` | 收盘复盘 | 行情 → 新闻 → 分析 → 复盘 → 板块 → 风险 → 推送 |
| `deep_research` | 深度研究 | 全量行情 → 新闻 → 深度分析 → 中期策略 |
| `memory_maintenance` | 记忆维护 | 健康检查 → 提醒 → SOUL → 卫生 → 渠道 |
| `quant_research` | 量化研究 | Alpha 策略 → 代码生成 → 回测 → 风控 → 决策 → 归档 |

---

## 文件说明

| 文件 | 用途 |
|------|------|
| `webchat_api.py` | Web Dashboard 后端 (FastAPI) |
| `webchat_frontend.html` | Web Dashboard 前端 (React 18 SPA) |
| `telegram_bot.py` | Telegram Bot 主程序 |
| `feishu_bot.py` | 飞书 Bot 主程序 |
| `agents/orchestrator.py` | 编排器 |
| `agents/backtest_agent.py` | 量化回测沙箱 |
| `agents/quant_pipeline.py` | 量化 Pipeline 编排 |
| `agents/llm_router.py` | LLM 多云路由 |
| `install-agents.sh` | 一键部署脚本 |
| `telegram.env` | Telegram + LLM API Key 配置 |

## 安全须知

- clawagent 为标准用户，无 sudo 权限
- zayl 和 ollama_runner 的 home 目录 chmod 700
- `.env` 文件权限 600（含 Bot Token 和 API Key）
- Web Dashboard 启用 HTTP Basic Auth
- `TELEGRAM_ALLOWED_USERS` 限制 Telegram 授权用户
- BacktestAgent 代码沙箱禁止危险模块（os/sys/subprocess/shutil）
- `/sh` 命令内置危险操作拦截
- Ollama 仅监听 127.0.0.1

## LLM Provider

| Provider | 说明 |
|----------|------|
| 百炼 (Bailian) | 阿里云，qwen 系列模型 |
| SiliconFlow | 开源模型托管 |
| DeepSeek | deepseek-chat / deepseek-coder |
| DashScope | 阿里云 DashScope |
| OpenAI | GPT 系列（备用） |

回退链：百炼 → SiliconFlow → DeepSeek → DashScope → OpenAI
可通过 Web Dashboard 系统面板动态锁定 Provider/Model。

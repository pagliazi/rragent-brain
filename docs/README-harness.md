<div align="center">

# RRAgent

**A股量化智能体框架 / A-Share Quant Trading Agent**

[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://python.org)
[![Redis](https://img.shields.io/badge/Redis-7+-red.svg)](https://redis.io)
[![LLM](https://img.shields.io/badge/LLM-Claude%20%7C%20Qwen-purple.svg)](https://anthropic.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

*RRAgent 是基于 LLM 的 A 股量化交易框架，支持实时行情分析、策略回测、因子挖掘与多条件选股。<br>对接 [ReachRich](https://rr.zayl.net) 数据平台，覆盖沪深京 5000+ 标的。*

</div>

> **[中文文档](README.zh-CN.md)** | English
>
> **This repo has moved to [pagliazi/rrclaw](https://github.com/pagliazi/rrclaw).** Both repos are kept in sync.

---

## Features

- **Real-time Market Data** — 全市场报价、涨跌停板、板块轮动、异动监控
- **Strategy Backtesting** — backtrader / vectorbt 双引擎，PBO 交叉验证
- **Factor Mining** — Alpha 因子扫描，滚动窗口优化验证
- **DSL Stock Screener** — 200+ 因子组合筛选
- **Multi-Channel** — Telegram、飞书、WebChat、REST API
- **API Key Auth** — `rk_` Bearer token，外部服务接入

## Quick Start

```bash
git clone https://github.com/pagliazi/rrclaw.git
cd rragent

# 一键部署
./deploy.sh

# 或手动安装
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env       # 填入 API Key
cp config.example.yaml rragent.yaml
```

配置 `.env`：

```env
ANTHROPIC_API_KEY=sk-ant-...          # 或 DASHSCOPE_API_KEY
REACHRICH_URL=https://rr.zayl.net/api
REACHRICH_TOKEN=rk_your_key           # 从 ReachRich 设置页获取
REDIS_URL=redis://127.0.0.1:6379/0
```

启动：

```bash
python -m rragent --config rragent.yaml
```

## Architecture

```
┌─ IM Gateway ──────────────────────────────────┐
│  Telegram · WebChat · 飞书 · API                     │
└──────────────┬──────────────────────────────────────┘
               │ WebSocket / ACP
┌──────────────▼──────────────────────────────────────┐
│  RRAgent (Python, ~10,800 lines)                      │
│                                                      │
│  ConversationRuntime ── LLM 推理循环                  │
│  ├── ContextEngine ─── 上下文压缩                     │
│  ├── ToolExecutor ──── 工具并发调度                    │
│  ├── ProviderRouter ── Claude / Qwen / Ollama        │
│  ├── CircuitBreaker ── 熔断 + 故障恢复                │
│  └── EvolutionEngine ─ 跨会话模式学习                  │
│                                                      │
│  Tools: PyAgent (71 cmd) · Hermes (47) · MCP         │
└──────────────────────────────────────────────────────┘
```

RRAgent 负责 LLM 推理主循环：接收指令 → 调度工具获取数据 → 执行回测 → 返回结果。定位是量化系统的决策调度层。

## Usage

**自然语言查询：**

```
"今天涨停板有哪些半导体？"  → 调用 market_query → 板块过滤 → 返回结果
"回测突破20日均线策略"      → 生成代码 → 调用 backtest → 返回收益曲线
```

**Python API 调用：**

```python
import httpx

headers = {"Authorization": "Bearer rk_your_key"}

# 全市场快照
r = httpx.get("https://rr.zayl.net/api/fast/realtime/", headers=headers)

# DSL 选股
r = httpx.post("https://rr.zayl.net/api/bridge/screener/", headers=headers, json={
    "payload": {"rules": [{"field": "pct_chg", "op": ">", "value": 5}]}
})
```

## ReachRich API

| Endpoint | Description |
|----------|-------------|
| `GET /bridge/snapshot/` | 全市场快照 |
| `GET /bridge/limitup/` | 涨跌停板 |
| `GET /bridge/dragon-tiger/` | 龙虎榜 |
| `GET /bridge/concepts/` | 板块概念 |
| `GET /bridge/sentiment/` | 舆情摘要 |
| `GET /bridge/kline/` | K线数据 |
| `GET /bridge/indicators/` | 技术指标 |
| `GET /bridge/presets/` | 策略预设 (200+) |
| `POST /bridge/screener/` | DSL 选股 |
| `POST /bridge/backtest/run/` | 策略回测 |
| `GET /fast/realtime/` | 实时行情 |
| `GET /sse/realtime/` | SSE 推送 |

认证方式：`Authorization: Bearer rk_...`，从 [ReachRich](https://rr.zayl.net) 设置页生成。

## Deployment

```bash
# Docker
cd deploy/ && docker compose up -d

# Linux (systemd)
sudo cp -r . /opt/rragent
cd /opt/rragent && python3 -m venv .venv && .venv/bin/pip install -e .
sudo cp deploy/hermes-bridge.service /etc/systemd/system/rragent.service
sudo systemctl enable --now rragent

# macOS (launchd)
cp deploy/com.hermes-bridge.plist ~/Library/LaunchAgents/com.rragent.plist
launchctl load ~/Library/LaunchAgents/com.rragent.plist
```

## FAQ

**如何获取 API Key？** — [rr.zayl.net](https://rr.zayl.net) 注册登录 → 设置 → API Key → 生成

**切换 LLM？** — 设置 `RRAGENT_PRIMARY_MODEL=dashscope/qwen3.5-plus` 或 `ollama/qwen2.5-coder:14b`

**不接 IM 只用数据？** — 跳过 Gateway，直接 HTTP 调 API 或启动 `rragent-market` MCP 服务

## License

MIT. See [LICENSE](LICENSE).

---

<div align="center">

Based on [Claude Code](https://github.com/anthropics/claude-code) · [Hermes Agent](https://github.com/NousResearch/hermes-agent) · [RRAgent](https://github.com/openclaw/openclaw)

</div>

# OpenClaw ↔ ReachRich 完整 API 手册

> **版本**: v1.1 | **更新**: 2026-02-26  
> **服务端**: `192.168.1.139:8001` (FastAPI, 228 个端点)  
> **自动文档**: `http://192.168.1.139:8001/api/docs` (Swagger UI)  
> **认证**: Bridge 端点 HMAC-SHA256 签名 + IP 白名单 / 其他端点 Session 认证  

---

## 〇、自动文档与动态接入

### 在线文档入口

| URL | 说明 |
|-----|------|
| `http://192.168.1.139:8001/api/docs` | Swagger UI — 交互式 API 测试 |
| `http://192.168.1.139:8001/api/redoc` | ReDoc — 结构化阅读文档 |
| `http://192.168.1.139:8001/api/openapi.json` | OpenAPI 3.x JSON Schema（机器可读） |

### OpenClaw 动态 Tool Calling

对于 Market Agent、News Agent 等需要直接查数据的 Agent，可直接加载 `openapi.json` 实现自动 Function Calling：

```python
import requests
from langchain_community.agent_toolkits.openapi import planner

# 拉取 FastAPI 自动生成的 OpenAPI Schema
openapi_spec = requests.get("http://192.168.1.139:8001/api/openapi.json").json()

# 自动转换为 LLM 可调用的 Tools
fastapi_tools = planner.create_openapi_agent(openapi_spec, llm=deepseek_model)

# Agent 自动调用：「帮我查平安银行今天的K线」→ 自动匹配 /api/bridge/kline/
```

### Quant Coder Prompt 注入

Quant Coder 的任务是**生成回测代码**（代码在沙箱执行），不需要动态 Tool Calling。应将本手册的第五章（沙箱回测 + 数据获取）注入到 System Prompt，确保生成的代码使用正确的内部端点和输出格式。

---

## 一、网络架构

```
                        ┌─────────────────────────────────────┐
                        │      Mac Mini M4 (192.168.1.188)    │
                        │      OpenClaw 多智能体系统 (控制面)    │
                        └───────────┬─────────────────────────┘
                                    │
                          HTTP 直连（HMAC 签名）
                          端口 8001，路径 /api/bridge/*
                                    │
┌───────────────────┐               ▼                ┌──────────────────┐
│  用户浏览器 / App  │    ┌──────────────────────┐    │   PostgreSQL     │
│                   │    │ 192.168.1.139 后端     │    │   Redis          │
│                   │    │                      │◄──►│   DolphinDB      │
└────────┬──────────┘    │  Django  (port 8000)  │    └──────────────────┘
         │               │  FastAPI (port 8001)  │
         │               │  沙箱 (quant_runner)  │
         │               └──────────▲───────────┘
         │                          │
         │    Nginx 反向代理          │
         ▼                          │
┌────────────────────────────┐      │
│ 192.168.1.138 前端服务器     │      │
│                            │──────┘
│  Nginx (:80)               │
│   ├─ /           → Next.js (localhost:3000)
│   ├─ /api/bridge/  → FastAPI (139:8001)
│   ├─ /api/ai-strategies/ → FastAPI (139:8001)
│   ├─ /api/       → Django  (139:8000)
│   ├─ /django-admin/ → Django (139:8000)
│   └─ /static/    → Django  (139:8000)
└────────────────────────────┘
```

### 1.1 三层架构

| 层 | IP | 服务 | 角色 |
|----|-----|------|------|
| 控制面 | 192.168.1.188 (Mac Mini) | OpenClaw agents | AI 策略研究、风控决策、自动化投研 |
| 执行面 | 192.168.1.139 | Django 8000 + FastAPI 8001 | 数据 API、沙箱回测、策略归档、数据采集 |
| 展示面 | 192.168.1.138 | Nginx + Next.js 3000 | 用户 Web UI、反向代理 |

### 1.2 调用关系与路由规则

系统中存在 **四种调用方** ，各自走不同的认证和路由：

#### ① 用户浏览器 → Nginx (138) → 后端 (139)

```
用户浏览器
  │
  ▼ HTTP :80
Nginx (192.168.1.138)
  │
  ├─ 页面请求 (/, /dashboard, /ai-strategies...)
  │     └─→ Next.js (localhost:3000) → SSR 渲染
  │
  ├─ API 请求 /api/bridge/* , /api/ai-strategies/*
  │     └─→ FastAPI (192.168.1.139:8001) 高性能端点
  │
  ├─ API 请求 /api/* (其他)
  │     └─→ Django (192.168.1.139:8000) 业务逻辑
  │
  ├─ /django-admin/
  │     └─→ Django (192.168.1.139:8000) 管理后台
  │
  └─ /static/, /media/
        └─→ Django (192.168.1.139:8000) 静态资源
```

**认证方式**: Session Cookie（登录后浏览器自动携带）

#### ② OpenClaw Agent (Mac Mini) → FastAPI (139:8001)

```
OpenClaw Agent (192.168.1.188)
  │
  │  HTTP 直连（不经过 Nginx）
  │  Headers: X-Bridge-Timestamp + X-Bridge-Key [+ X-Bridge-Nonce]
  ▼
FastAPI (192.168.1.139:8001)
  └─ /api/bridge/*  → BridgeGuardMiddleware 验证 → 端点处理
```

**认证方式**: HMAC-SHA256 签名 + IP 白名单（192.168.1.0/24）
**直连原因**: 跳过 Nginx 减少延迟，Bridge 端点有专属中间件

#### ③ 沙箱回测代码 → FastAPI (localhost:8001)

```
quant_runner 沙箱进程 (192.168.1.139 本机)
  │
  │  HTTP localhost（无需认证）
  ▼
FastAPI (127.0.0.1:8001)
  └─ /api/internal/kline/  → 仅允许 127.0.0.1 访问
```

**认证方式**: 无（localhost IP 白名单）
**用途**: 沙箱内的回测代码获取K线数据，不能访问 /api/bridge/*（需要 HMAC）

#### ④ FastAPI ↔ Django 内部转发

```
FastAPI (8001)
  │
  │  未匹配的 /api/* 请求 → fallback 代理
  ▼
Django (8000)
  └─ 处理 FastAPI 未实现的端点
```

**说明**: FastAPI 的 fallback router 将未实现的端点透明代理到 Django，实现渐进式迁移

### 1.3 端口与服务对照

| 端口 | 服务 | 调用方 | 说明 |
|------|------|--------|------|
| 138:80 | Nginx | 用户浏览器 | 统一入口，反向代理 |
| 138:3000 | Next.js | Nginx 内部 | SSR 前端渲染 |
| 139:8000 | Django | Nginx / FastAPI fallback | 传统业务 API、Admin |
| 139:8001 | FastAPI | Nginx / OpenClaw / 沙箱 | 高性能 API、Bridge、回测 |

### 1.4 数据流示例

**示例 A: 用户在前端查看 AI 策略大厅**

```
浏览器 → 138:80/ai-strategies → Next.js SSR
  → fetch /api/ai-strategies/ → Nginx → 139:8001/api/ai-strategies/
  → FastAPI fe_router (Session 认证) → PostgreSQL → JSON 响应
```

**示例 B: OpenClaw 执行完整回测流程**

```
Alpha Researcher → GET /api/bridge/snapshot/     (HMAC) → 市场快照
                 → GET /api/bridge/limitup/      (HMAC) → 涨停数据
Quant Coder      → GET /api/bridge/kline/        (HMAC) → K线数据 (用于分析)
                 → 生成 Python 回测代码
Backtest Engine  → POST /api/bridge/backtest/run/ (HMAC) → 提交代码到沙箱
  沙箱内代码      → GET /api/internal/kline/      (localhost) → 获取K线
                 → 执行策略逻辑 → print(JSON 指标)
Portfolio Mgr    → POST /api/bridge/strategy/save/ (HMAC) → 归档策略
```

**示例 C: 用户在前端执行策略筛选**

```
浏览器 → 138:80/screener → Next.js
  → POST /api/screener/presets/{slug}/run/ → Nginx → 139:8000 (Django)
  → DSL 引擎执行 → 返回筛选结果
```

---

## 二、认证机制

### 2.1 HMAC-SHA256 签名

所有 `/api/bridge/*` 端点需要携带以下 HTTP Headers：

| Header | 说明 |
|--------|------|
| `X-Bridge-Timestamp` | 当前 Unix 时间戳（秒），5 分钟有效窗口 |
| `X-Bridge-Key` | HMAC-SHA256 签名值 |
| `X-Bridge-Nonce` | **仅 POST 请求**，随机 hex 字符串，防重放 |

**签名算法**（GET / POST 统一）：

```python
import hmac, hashlib, time

ts = str(int(time.time()))
path = "/api/bridge/kline/"        # 请求的完整路径
message = f"{ts}{path}".encode()
sig = hmac.new(SECRET.encode(), message, hashlib.sha256).hexdigest()
```

> 注意：签名中的 `path` 可以是完整路径 `/api/bridge/xxx/` 或相对路径 `/xxx/`，服务端两种都接受。

### 2.2 IP 白名单

支持精确 IP 和 CIDR 子网。当前配置：`127.0.0.1, 192.168.1.0/24`

### 2.3 速率限制

每个 IP 每 60 秒最多 30 次请求。

---

## 三、响应规范

- **格式**: JSON (application/json)
- **序列化**: orjson（支持 Decimal、datetime 自动转 str）
- **完整性校验**: 响应头 `X-Data-Hash` = SHA256(response_body)
- **缓存标识**: `X-Cache: HIT` 或 `MISS`

---

## 四、数据查询端点（GET）

### 4.1 K线数据

```
GET /api/bridge/kline/
```

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| ts_code | str | ✅ | — | 股票代码，如 `000001.SZ` |
| period | str | — | daily | `daily` / `weekly` / `monthly` |
| start_date | str | — | — | 起始日期 `YYYY-MM-DD` 或 `YYYYMMDD` |
| end_date | str | — | — | 结束日期 |
| limit | int | — | 250 | 1–2000 |
| fmt | str | — | json | `json` / `backtrader` |

**响应示例**（fmt=json）：

```json
{
  "code": "000001.SZ",
  "period": "daily",
  "count": 3,
  "data": [
    {
      "ts_code": "000001.SZ",
      "trade_date": "2024-09-03",
      "open": "10.50",
      "high": "10.80",
      "low": "10.40",
      "close": "10.70",
      "vol": "123456",
      "amount": "130000000",
      "pct_chg": "1.90",
      "pre_close": "10.50",
      "change": "0.20"
    }
  ]
}
```

**响应示例**（fmt=backtrader）：

```json
{
  "code": "000001.SZ",
  "period": "daily",
  "count": 3,
  "data": [
    {
      "datetime": "2024-09-03",
      "open": 10.50,
      "high": 10.80,
      "low": 10.40,
      "close": 10.70,
      "volume": 123456.0,
      "openinterest": 0
    }
  ]
}
```

**缓存**: 5 分钟

---

### 4.2 全市场快照

```
GET /api/bridge/snapshot/
```

无参数。返回最新交易日的市场全貌。

```json
{
  "trade_date": "2026-02-25",
  "total_stocks": 5471,
  "limit_up": 104,
  "limit_down": 12,
  "up_count": 3200,
  "down_count": 1800,
  "flat_count": 471,
  "avg_pct_chg": "0.85",
  "total_amount_yi": "12345.67",
  "sentiment": {
    "score": 72,
    "style_bias": "成长",
    "hot_sectors": "AI,半导体"
  }
}
```

**缓存**: 30 秒

---

### 4.3 涨停板聚合

```
GET /api/bridge/limitup/
```

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| trade_date | str | — | 最新 | 交易日 `YYYYMMDD` |

```json
{
  "trade_date": "2026-02-25",
  "limit_up_count": 104,
  "limit_down_count": 12,
  "limit_failed_count": 35,
  "limit_up": [
    {
      "ts_code": "300505.SZ",
      "name": "川金诺",
      "close": "37.30",
      "pct_chg": "20.00",
      "first_time": "09:30:03",
      "last_time": "09:30:03",
      "open_times": 0,
      "up_stat": "1/1",
      "limit_type": "U",
      "industry": "化工"
    }
  ],
  "limit_down": [...],
  "limit_failed": [...],
  "conn_board_ladder": {
    "5": ["天风证券"],
    "4": ["xxx"],
    "3": ["xxx", "yyy"]
  },
  "industry_distribution": {
    "化工": 15,
    "半导体": 12,
    "AI": 10
  }
}
```

**缓存**: 1 分钟

---

### 4.4 概念板块

```
GET /api/bridge/concepts/
```

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| limit | int | — | 50 | 1–200 |

```json
{
  "count": 50,
  "concepts": [
    {
      "id": 1,
      "name": "人工智能",
      "board_code": "BK0800",
      "pct_chg": "3.25",
      "up_count": 120,
      "down_count": 30,
      "leading_stock": "科大讯飞",
      "leading_stock_pct": "9.98",
      "total_market_value": "12345678",
      "turnover_rate": "5.23",
      "updated_at": "2026-02-25 15:30:00"
    }
  ]
}
```

**缓存**: 2 分钟

---

### 4.5 龙虎榜

```
GET /api/bridge/dragon-tiger/
```

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| trade_date | str | — | 最新 | 交易日 |
| limit | int | — | 50 | 1–200 |

```json
{
  "count": 25,
  "items": [
    {
      "ts_code": "300505.SZ",
      "name": "川金诺",
      "trade_date": "2026-02-25",
      "close": "37.30",
      "pct_chg": "20.00",
      "turnover_rate": "25.6",
      "reason": "连续三个交易日内涨幅偏离值累计达20%",
      "net_amount": "50000000",
      "buy_amount": "80000000",
      "sell_amount": "30000000",
      "amount": "130000000"
    }
  ]
}
```

---

### 4.6 舆情摘要

```
GET /api/bridge/sentiment/
```

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| limit | int | — | 20 | 新闻条数 1–100 |

```json
{
  "news": [
    {
      "id": 1,
      "title": "央行下调MLF利率",
      "summary": "...",
      "source": "新华社",
      "sentiment_label": "positive",
      "sentiment_score": "0.85",
      "associated_stocks": "600036.SH,601398.SH",
      "publish_time": "2026-02-25 10:30:00",
      "importance": "3"
    }
  ],
  "stats": {
    "stat_time": "2026-02-25",
    "positive_count": 45,
    "negative_count": 12,
    "neutral_count": 33,
    "total_count": 90,
    "hot_keywords": "AI,降息,半导体",
    "hot_stocks": "000001.SZ,300505.SZ",
    "heat_index": "72"
  }
}
```

**缓存**: 2 分钟

---

### 4.7 技术指标

```
GET /api/bridge/indicators/
```

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| ts_code | str | ✅ | — | 股票代码 |
| limit | int | — | 60 | 1–500 |

```json
{
  "code": "000001.SZ",
  "count": 60,
  "indicators": [
    {
      "ts_code": "000001.SZ",
      "trade_date": "2026-02-25",
      "ma5": "10.50",
      "ma10": "10.30",
      "ma20": "10.10",
      "ma60": "9.80",
      "macd": "0.12",
      "macd_signal": "0.08",
      "macd_hist": "0.04",
      "rsi_6": "65.3",
      "rsi_12": "58.1",
      "kdj_k": "72.5",
      "kdj_d": "68.3",
      "kdj_j": "80.9",
      "boll_upper": "11.20",
      "boll_mid": "10.50",
      "boll_lower": "9.80"
    }
  ]
}
```

---

### 4.8 策略预设库

```
GET /api/bridge/presets/
```

无参数。返回所有激活的策略预设模板。

```json
{
  "count": 12,
  "presets": [
    {
      "id": 4,
      "name": "右侧突破策略",
      "slug": "right-side-breakout",
      "description": "识别中阳线/长阳线突破信号，配合成交量放大确认",
      "category": "breakout",
      "payload": { ... },
      "is_active": true,
      "access_level": 2,
      "backtest_params": {},
      "created_at": "2026-01-12 23:01:24"
    }
  ]
}
```

---

### 4.9 决策账本列表

```
GET /api/bridge/ledger/
```

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| status | str | — | 全部 | `PENDING` / `APPROVE` / `REJECT` |
| page | int | — | 1 | 页码 |
| page_size | int | — | 20 | 每页条数 1–100 |

```json
{
  "total": 15,
  "page": 1,
  "page_size": 20,
  "items": [
    {
      "id": 1,
      "title": "均线金叉放量突破策略",
      "topic": "breakout",
      "status": "APPROVE",
      "attempts": 2,
      "code_hash": "a7701955...",
      "backtest_metrics": {"sharpe": 1.2, "max_drawdown": -0.15},
      "model_used": "qwen2.5-coder:14b",
      "is_immutable": true,
      "created_at": "2026-02-25 10:30:00"
    }
  ]
}
```

---

### 4.10 决策账本详情

```
GET /api/bridge/ledger/{ledger_id}/
```

返回完整策略记录，包含 `strategy_code`、`decision_report`、`risk_review`，以及 `integrity_ok`（代码哈希校验结果）。

---

## 五、执行端点（POST）

### 5.1 沙箱回测

```
POST /api/bridge/backtest/run/
```

**请求体**：

```json
{
  "strategy_code": "import requests\nimport pandas as pd\n...",
  "stock": "000001.SZ",
  "start_date": "20240101",
  "end_date": "20241231",
  "timeout": 600,
  "mode": "backtrader",
  "strategy_params": {"holding_days": 5, "stop_loss": -0.05, "take_profit": 0.10}
}
```

| 字段 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| strategy_code | str | ✅ | — | 完整 Python 代码 |
| stock | str | 条件 | `""` | 股票代码，**backtrader 模式必填**，vectorbt 模式忽略 |
| start_date | str | — | 20240101 | 起始日期 |
| end_date | str | — | 20241231 | 结束日期 |
| timeout | int | — | 600 | 超时秒数 30–600（vectorbt 模式上限 60） |
| mode | str | — | backtrader | 回测模式：`backtrader`（单股）/ `vectorbt`（全市场） |
| strategy_params | dict | — | `{}` | 策略动态参数，如 `{"holding_days": 5, "stop_loss": -0.05, "take_profit": 0.10}`，通过 `--params` CLI 传入脚本 |

**模式差异**：

| | backtrader | vectorbt |
|---|---|---|
| 数据源 | `/api/internal/kline/`（localhost） | ClickHouse `daily_kline` / DolphinDB |
| 股票范围 | 单股（`stock` 必填） | 全市场（`stock` 忽略） |
| 内存限制 | 2 GB | 4 GB |
| 超时上限 | 600 秒 | 60 秒 |
| 允许 `os` | ❌ | ✅ 仅 `os.getenv()` / `os.environ.get()` |
| 网络访问 | 仅 8001 端口 | 额外放行 8123（ClickHouse）/ 8848（DolphinDB） |

**成功响应**：

```json
{
  "success": true,
  "metrics": {
    "cagr": 0.15,
    "max_drawdown": -0.12,
    "sharpe": 1.2,
    "win_rate": 0.55,
    "trades": 42
  },
  "code_hash": "a7701955...",
  "run_id": "048ec0513ba8",
  "mode": "backtrader"
}
```

**失败响应**：

```json
{
  "success": false,
  "error": "Code validation failed: Banned import: os",
  "code_hash": "...",
  "mode": "backtrader"
}
```

#### 沙箱安全约束

**禁止的 import**：`os, sys, subprocess, shutil, socket, http.server, urllib, pathlib, importlib, ctypes, signal, multiprocessing, threading, asyncio, pickle, shelve, webbrowser, code, codeop, compileall`

**禁止的模式**：`__import__()`, `eval()`, `exec()`, `compile()`, `globals()`, `locals()`, `getattr()`, `setattr()`, `delattr()`, `open(..., 'w')`, `open(..., 'a')`, `breakpoint()`, `__builtins__`, `__subclasses__`

**禁止的外部数据库**：`yfinance`, `akshare`, `tushare`, `baostock`, `efinance`

**VectorBT 模式额外禁止**（防止 SQL 注入 / 数据库破坏）：
- SQL 写入：`INSERT INTO`, `DROP TABLE/DATABASE`, `ALTER TABLE`, `CREATE TABLE/DATABASE`, `DELETE FROM`, `TRUNCATE`
- DolphinDB 破坏性调用：`dropDatabase`, `dropTable`
- ClickHouse 写入：`client.command()`, `client.insert()`
- 仅允许 `os.getenv()` / `os.environ.get()`，其他 `os.*` 调用被拒绝

**可用库**：

| 模式 | 可用库 |
|------|--------|
| backtrader | `backtrader, pandas, numpy, requests, json, argparse, math, datetime` |
| vectorbt | `vectorbt, pandas, numpy, clickhouse_connect, dolphindb, json, argparse, math, datetime, os`（仅 getenv） |

#### 沙箱内数据获取

沙箱内代码**不能**访问 Bridge API（需要 HMAC 签名），必须使用内部端点：

```python
url = "http://127.0.0.1:8001/api/internal/kline/"
params = {"ts_code": args.stock, "start_date": args.start, "end_date": args.end, "period": "daily"}
resp = requests.get(url, params=params)
df = pd.DataFrame(resp.json()["data"])
```

#### 输出格式要求

策略代码最后一行必须 `print` 纯 JSON：

```python
print(json.dumps({
    "cagr": 0.15,
    "max_drawdown": -0.12,
    "sharpe": 1.2,
    "win_rate": 0.55,
    "trades": 42
}))
```

---

### 5.2 DSL 选股

```
POST /api/bridge/screener/
```

**请求体**：

```json
{
  "payload": {
    "filters": { ... },
    "outputs": { "limit": 50, "fields": [...], "order_by": [...] },
    "universe": { "mode": "all", "exclude": ["*ST"] }
  },
  "limit": 50
}
```

`payload` 格式参照 `/api/bridge/presets/` 返回的 `payload` 字段。

---

### 5.3 策略归档

```
POST /api/bridge/strategy/save/
```

**请求体**：

```json
{
  "title": "均线金叉放量突破策略",
  "topic": "breakout",
  "status": "APPROVE",
  "attempts": 2,
  "strategy_code": "import ...",
  "backtest_metrics": {"sharpe": 1.2, "max_drawdown": -0.12},
  "risk_review": "风控通过，回撤可控",
  "decision_report": "# 策略决策报告\n...",
  "model_used": "qwen2.5-coder:14b",
  "rounds_data": [
    {
      "round": 1,
      "code": "import os, argparse ...",
      "code_chars": 4200,
      "backtest": {
        "success": false,
        "error": "Exit code 1",
        "stderr": "numba typing error ...",
        "stdout": ""
      }
    },
    {
      "round": 2,
      "code": "import os, argparse ...",
      "code_chars": 5100,
      "backtest": {
        "success": true,
        "metrics": {"sharpe": 1.2, "max_drawdown": -0.12, "trades": 520, "win_rate": 0.52},
        "risk_decision": "APPROVE",
        "risk_reason": "风控通过"
      }
    }
  ]
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| rounds_data | list[object] | 否 | 每轮迭代的完整代码与回测结果 |
| rounds_data[].round | int | — | 轮次序号 (1-based) |
| rounds_data[].code | str | — | 该轮 Coder 生成的完整 Python 代码 |
| rounds_data[].code_chars | int | — | 代码字符数 |
| rounds_data[].backtest.success | bool | — | 回测是否成功执行 |
| rounds_data[].backtest.metrics | object | — | 成功时的回测指标 |
| rounds_data[].backtest.error | str | — | 失败时的错误摘要 |
| rounds_data[].backtest.stderr | str | — | 失败时的完整 stderr (最多8000字符) |
| rounds_data[].backtest.risk_decision | str | — | 风控决策 APPROVE/REJECT (回测成功时) |

**响应**：

```json
{
  "id": 15,
  "code_hash": "a7701955...",
  "status": "saved"
}
```

---

### 5.4 盘中信号扫描

```
POST /api/bridge/intraday/scan/
```

在沙箱中执行 DolphinDB 实时数据扫描代码，用于盘中信号检测。底层复用 `run_sandbox_backtest` 的 vectorbt 模式。

**请求体**：

```json
{
  "strategy_code": "import dolphindb as ddb\nimport os\n...",
  "stock_pool": ["000001.SZ", "600036.SH"],
  "timeout": 30
}
```

| 字段 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| strategy_code | str | ✅ | — | DolphinDB 扫描 Python 代码 |
| stock_pool | list[str] | — | `[]` | 目标股票池（空则全市场） |
| timeout | int | — | 30 | 超时秒数 10–60 |

**成功响应**（结构同沙箱回测）：

```json
{
  "success": true,
  "metrics": { ... },
  "code_hash": "b3e82f91...",
  "run_id": "a1b2c3d4e5f6",
  "mode": "vectorbt"
}
```

**安全约束**：与 vectorbt 模式相同（允许 `clickhouse_connect`, `dolphindb`, `os.getenv()`；禁止 SQL 写入操作）。

---

## 六、Python 客户端使用

### 6.1 BridgeClient 初始化

```python
import os
from bridge_client import BridgeClient

client = BridgeClient(
    base_url=os.getenv("BRIDGE_BASE_URL", "http://192.168.1.139:8001/api/bridge"),
    secret=os.getenv("BRIDGE_SECRET"),
)
```

**环境变量**：

| 变量 | 必填 | 说明 |
|------|------|------|
| `BRIDGE_SECRET` | ✅ | HMAC 密钥，与服务端 `.env` 中一致 |
| `BRIDGE_BASE_URL` | — | 默认 `http://192.168.1.139:8001/api/bridge` |

### 6.2 方法一览

| 方法 | 端点 | Agent 使用者 |
|------|------|-------------|
| `get_snapshot()` | /snapshot/ | Market Agent |
| `get_limitup(trade_date?)` | /limitup/ | Market Agent |
| `get_concepts(limit?)` | /concepts/ | Market Agent |
| `get_sentiment(limit?)` | /sentiment/ | News Agent |
| `get_dragon_tiger(trade_date?)` | /dragon-tiger/ | Market Agent |
| `get_kline(ts_code, period?, start_date?, end_date?, limit?, fmt?)` | /kline/ | Quant Agent |
| `get_indicators(ts_code, limit?)` | /indicators/ | Quant Agent |
| `get_presets()` | /presets/ | Strategist Agent |
| `run_screener(payload, limit?)` | /screener/ | Quant Agent |
| `run_backtest(code, stock?, start_date?, end_date?, timeout?, mode?, strategy_params?)` | /backtest/run/ | Backtest Agent |
| `run_intraday_scan(code, stock_pool?, timeout?)` | /intraday/scan/ | Execution Agent |
| `save_strategy(title, ..., rounds_data?)` | /strategy/save/ | Portfolio Manager |
| `get_ledger(status?, page?)` | /ledger/ | Portfolio Manager |
| `get_ledger_detail(ledger_id)` | /ledger/{id}/ | Portfolio Manager |

### 6.3 使用示例

```python
# Market Agent: 获取市场全貌
snapshot = await client.get_snapshot()
print(f"涨停 {snapshot['limit_up']} 家，跌停 {snapshot['limit_down']} 家")

# Quant Agent: 获取K线
kline = await client.get_kline("000001.SZ", start_date="2024-09-01", end_date="2025-01-01")
print(f"共 {kline['count']} 根K线")

# Backtest Agent: 执行沙箱回测
result = await client.run_backtest(
    code="import requests\nimport pandas as pd\n...",
    stock="000001.SZ",
    start_date="20240901",
    end_date="20250101",
)
if result["success"]:
    print(f"夏普比率: {result['metrics']['sharpe']}")

# Portfolio Manager: 归档策略
saved = await client.save_strategy(
    title="均线金叉放量突破",
    status="APPROVE",
    strategy_code="...",
    backtest_metrics=result["metrics"],
    decision_report="# 决策报告\n...",
)
```

---

## 七、错误码

| HTTP 状态 | 含义 | 常见原因 |
|-----------|------|----------|
| 200 | 成功 | — |
| 401 | 认证失败 | HMAC 签名错误、缺少 headers、时间戳过期 |
| 403 | 禁止访问 | IP 不在白名单 |
| 404 | 资源不存在 | 无数据或 ledger_id 无效 |
| 429 | 限流 | 超过 30 次/分钟 |
| 500 | 服务器错误 | 后端异常，查看日志 |

---

## 八、Agent 角色 → API 映射

```
┌─────────────────┐     GET /snapshot/, /limitup/, /concepts/, /dragon-tiger/
│  Alpha Researcher│────────────────────────────────────────────────────────►
│  (deepseek-r1)  │     分析市场数据，提出策略假说
└────────┬────────┘
         │ 策略假说
         ▼
┌─────────────────┐     GET /kline/, /indicators/, /presets/
│   Quant Coder   │────────────────────────────────────────────────────────►
│  (qwen-coder)   │     生成 Python 回测代码
└────────┬────────┘
         │ 回测代码
         ▼
┌─────────────────┐     POST /backtest/run/
│  Backtest Engine│────────────────────────────────────────────────────────►
│  (139 沙箱)     │     沙箱执行，返回指标 JSON
└────────┬────────┘
         │ 回测指标
         ▼
┌─────────────────┐     （纯 LLM 判断，不调 API）
│  Risk Analyst   │
│  (deepseek-r1)  │     APPROVE / REJECT + 改进建议
└────────┬────────┘
         │ 审核结果
         ▼
┌─────────────────┐     POST /strategy/save/
│Portfolio Manager│────────────────────────────────────────────────────────►
│  (deepseek-r1)  │     撰写决策账本并归档
└─────────────────┘
```

---

## 九、风控阈值参考

```yaml
risk_thresholds:
  min_sharpe: 0.5
  max_drawdown: -0.30
  min_trades: 5
  min_win_rate: 0.35
```

不满足上述阈值的策略应被 Risk Analyst 标记为 `REJECT`，触发 Quant Coder 重新迭代（最多 3 轮）。

---

## 十、ReachRich 全平台 API 目录

FastAPI 8001 端口共 **228 个端点**，按功能域分组如下。OpenClaw 主要使用 Bridge 类端点（HMAC 认证），但在特定场景下也可能用到其他公开端点。

### 10.1 Bridge API（OpenClaw 专用，HMAC 认证）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/bridge/snapshot/` | 全市场最新快照 |
| GET | `/api/bridge/limitup/` | 涨停板聚合数据 |
| GET | `/api/bridge/concepts/` | 概念板块聚合 |
| GET | `/api/bridge/dragon-tiger/` | 龙虎榜聚合 |
| GET | `/api/bridge/sentiment/` | 舆情摘要 |
| GET | `/api/bridge/kline/` | K线数据 |
| GET | `/api/bridge/indicators/` | 技术指标 |
| GET | `/api/bridge/presets/` | 策略预设列表 |
| GET | `/api/bridge/ledger/` | 决策账本列表 |
| GET | `/api/bridge/ledger/{id}/` | 决策账本详情 |
| POST | `/api/bridge/screener/` | 执行 DSL 选股 |
| POST | `/api/bridge/backtest/run/` | 沙箱回测执行（backtrader/vectorbt 双模式） |
| POST | `/api/bridge/intraday/scan/` | 盘中 DolphinDB 信号扫描 |
| POST | `/api/bridge/strategy/save/` | 保存 AI 策略到归档 |

### 10.2 沙箱内部端点（仅 localhost，无需认证）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/internal/kline/` | 沙箱专用K线数据（回测代码使用） |

### 10.3 行情数据

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/market/` | 市场行情列表 |
| GET | `/api/market/{pk}/` | 个股行情详情 |
| GET | `/api/market/fund-flow/` | 资金流向 |
| GET | `/api/market/fund-overview/` | 基金概览 |
| GET | `/api/market/north-fund/` | 北向资金 |
| GET | `/api/stocks/market-data/latest/` | 最新行情 |
| GET | `/api/stocks/market-data/history/` | 历史行情 |
| GET | `/api/weekly/` | 周线数据 |
| GET | `/api/monthly/` | 月线数据 |

### 10.4 实时行情与 SSE

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/realtime/` | 实时报价 |
| GET | `/api/realtime/{ts_code}` | 个股实时报价 |
| GET | `/api/realtime/snapshot/` | 实时快照 |
| GET | `/api/realtime/orderbook/` | 买卖五档 |
| GET | `/api/realtime/trades/` | 实时成交 |
| GET | `/api/realtime/trades/intraday/` | 分时成交 |
| GET | `/api/realtime/trades/auction/` | 竞价数据 |
| GET | `/api/realtime/mainflow/` | 主力资金流 |
| GET | `/api/realtime/mainforce-holding/` | 主力持仓 |
| GET | `/api/realtime/status/` | 市场状态 |
| GET | `/api/market-data/intraday/` | 分时行情 |
| GET | `/api/sse/realtime/` | SSE 实时推送流 |
| GET | `/api/sse/quotes/{ts_codes}/` | SSE 个股报价流 |

### 10.5 涨跌停与龙虎榜

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/limitup/` | 涨跌停列表 |
| GET | `/api/limitup/{pk}/` | 涨跌停详情 |
| GET | `/api/dragon-tiger/` | 龙虎榜列表 |
| GET | `/api/dragon-tiger/dates/` | 龙虎榜日期 |
| GET | `/api/dragon-tiger/{pk}/` | 龙虎榜详情 |
| GET | `/api/dragon-tiger-inst/` | 龙虎榜机构 |

### 10.6 概念板块与板块联动

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/concept-boards/` | 概念板块列表 |
| GET | `/api/concept-boards/by-stock/` | 个股所属板块 |
| GET | `/api/concept-boards/stocks/` | 板块成分股 |

### 10.7 同花顺热股

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/ths-hot/` | 热股列表 |
| GET | `/api/ths-hot/latest/` | 最新热股 |
| GET | `/api/ths-hot/dates/` | 可用日期 |
| GET | `/api/ths-hot/markets/` | 按市场分类 |

### 10.8 舆情与新闻

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/sentiment/` | 市场情绪指标 |
| GET | `/api/sentiment/dashboard/` | 情绪看板 |
| GET | `/api/sentiment/radar/` | 情绪雷达 |
| GET | `/api/sentiment/realtime/` | 实时情绪 |
| GET | `/api/sentiment/news/` | 情绪新闻列表 |
| GET | `/api/sentiment/keywords/` | 情绪关键词 |
| GET | `/api/sentiment/sources/` | 数据源管理 |
| GET | `/api/sentiment/alerts/` | 情绪预警 |
| GET | `/api/cls/news/` | 财联社新闻 |
| GET | `/api/cls/news/latest/` | 财联社最新 |
| GET | `/api/cls/news/realtime/` | 财联社实时 |
| GET | `/api/cls/concepts/` | 财联社概念 |
| GET | `/api/cls/stocks/` | 财联社个股 |
| GET | `/api/news/` | 通用新闻列表 |

### 10.9 异动监控

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/abnormal/` | 异动列表 |
| GET | `/api/abnormal/alert/` | 异动预警 |
| GET | `/api/abnormal/{ts_code}/` | 个股异动 |
| GET | `/api/abnormal/kline/{ts_code}/` | 异动K线 |
| GET | `/api/abnormal/estimate/{ts_code}/` | 异动估算 |
| POST | `/api/abnormal/calculate/` | 异动计算 |

### 10.10 AI 策略与选股

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/ai-strategy/chat/` | AI 对话选股 |
| POST | `/api/ai-strategy/compile/` | AI 策略编译 |
| POST | `/api/ai-strategy/execute/` | AI 策略执行 |
| GET | `/api/ai-strategy/factors/` | 可用因子列表 |
| POST | `/api/ai-strategy/optimize/` | AI 策略优化 |
| POST | `/api/ai-strategy/save/` | AI 策略保存 |
| POST | `/api/v3/ai-analyze/` | V3 AI 分析 |
| GET | `/api/v3/ai-status/` | V3 AI 状态 |
| GET | `/api/v3/insight/{ts_code}/` | V3 个股洞察 |
| POST | `/api/v3/quant-score/` | V3 量化评分 |
| GET | `/api/v3/screen/` | V3 筛选列表 |
| POST | `/api/v3/screen/` | V3 执行筛选 |

### 10.11 策略筛选与回测

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/screener/presets/` | 策略预设列表 |
| GET | `/api/screener/presets/{slug}/` | 预设详情 |
| POST | `/api/screener/presets/{slug}/run/` | 执行预设筛选 |
| POST | `/api/screener/presets/{slug}/run-realtime/` | 实时预设筛选 |
| POST | `/api/screener/presets/{slug}/backtest/` | 预设回测 |
| POST | `/api/screener/presets/run-multiple/` | 批量执行预设 |
| POST | `/api/screener/run/` | 自定义筛选 |
| POST | `/api/screener/realtime/` | 实时自定义筛选 |
| POST | `/api/backtest/` | 通用回测 |

### 10.12 ETF 监控

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/etf/explosive/` | ETF 爆量异动 |
| GET | `/api/etf/stats/` | ETF 统计 |
| GET | `/api/etf/alerts/` | ETF 预警 |
| GET | `/api/etf/flow/` | ETF 资金流 |
| GET | `/api/etf/daily/` | ETF 日线数据 |
| GET | `/api/etf/intraday/` | ETF 分时数据 |
| GET | `/api/etf/{ts_code}/` | ETF 详情 |

### 10.13 每日复盘

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/analysis/reports/` | 复盘报告列表 |
| GET | `/api/analysis/reports/latest/` | 最新复盘 |
| GET | `/api/analysis/reports/{id}/` | 复盘详情 |

### 10.14 前端专用 AI 策略（Session 认证）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/ai-strategies/` | AI 策略列表（管理员） |
| GET | `/api/ai-strategies/{id}/` | AI 策略详情（管理员） |

### 10.15 用户认证

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/auth/login/` | 登录 |
| POST | `/api/auth/register/` | 注册 |
| GET | `/api/auth/me/` | 当前用户 |
| GET | `/api/auth/check/` | 认证检查 |
| POST | `/api/auth/token/` | JWT 获取 |
| POST | `/api/auth/token/refresh/` | JWT 刷新 |
| GET | `/api/auth/points/` | 积分信息 |
| POST | `/api/auth/checkin/` | 签到 |

### 10.16 系统管理（admin）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/admin/users/` | 用户管理 |
| GET | `/api/admin/collection-tasks/` | 采集任务管理 |
| GET | `/api/admin/strategy-presets/` | 策略预设管理 |
| GET | `/api/admin/factor-snapshots/` | 因子快照管理 |
| GET | `/api/admin/tickets/` | 工单管理 |
| GET | `/api/admin/staff/` | 员工列表 |

### 10.17 其他

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/trading-calendar/` | 交易日历 |
| GET | `/api/cache/status/` | 缓存状态 |
| GET | `/api/data/integrity/` | 数据完整性检查 |
| GET | `/api/backend-info/` | 后端信息 |
| POST | `/api/tasks/trigger/` | 触发后台任务 |
| GET | `/api/stock/intraday/` | 个股分时 |
| GET | `/api/lottery/` | 彩票数据 |
| GET | `/api/v2/snapshot` | V2 快照 |
| GET | `/api/v2/delta` | V2 增量 |

---

## 十一、OpenClaw Agent 推荐端点索引

各 Agent 根据职责重点使用的端点速查表：

| Agent | 主要端点 | 用途 |
|-------|---------|------|
| **Market Agent** | `/bridge/snapshot/`, `/bridge/limitup/`, `/bridge/concepts/`, `/bridge/dragon-tiger/`, `/realtime/`, `/ths-hot/latest/` | 盘中/盘后市场全貌 |
| **News Agent** | `/bridge/sentiment/`, `/cls/news/latest/`, `/sentiment/dashboard/`, `/sentiment/radar/` | 舆情监控与情绪分析 |
| **Alpha Researcher** | 综合 Market + News Agent 的输出 | 提出策略假说 |
| **Quant Coder** | `/bridge/kline/`, `/bridge/indicators/`, `/bridge/presets/`, `/api/internal/kline/`(沙箱内) | 获取历史数据、编写回测代码 |
| **Backtest Engine** | `/bridge/backtest/run/`（mode=backtrader/vectorbt） | 提交代码到沙箱执行 |
| **Execution Agent** | `/bridge/intraday/scan/` | 盘中 DolphinDB 信号扫描 |
| **Risk Analyst** | 纯 LLM 判断，不直接调 API | 审核回测指标 |
| **Portfolio Manager** | `/bridge/strategy/save/`, `/bridge/ledger/` | 归档策略、查看账本 |
| **Strategist Agent** | `/bridge/presets/`, `/screener/presets/`, `/v3/screen/` | 策略库管理与筛选 |
| **Analysis Agent** | `/analysis/reports/latest/`, `/sentiment/dashboard/` | 每日复盘 |
| **Dev Agent** | `/api/docs` (Swagger UI), `/api/openapi.json` | 接口探索与调试 |

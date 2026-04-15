# ReachRich 完整系统架构文档

> **更新**: 2026-03-03  
> **覆盖范围**: 前端机 (138) + 后端机 (139) + 沙箱 + 所有服务、代码、配置文件

---

## 一、两台机器的职责划分

```
┌──────────────────────────────────────────┐     ┌────────────────────────────────────────────────────┐
│           192.168.1.138 (前端机)          │     │              192.168.1.139 (后端机)                  │
│                                          │     │                                                    │
│  Nginx :80                               │     │  ★ FastAPI  :8001  (全部 276 个 API, 4 workers)    │
│    ├─ /                → Next.js :3000   │◄────│    Django   :8000  (仅 Admin + 静态 + Celery调度)  │
│    ├─ /api/*           → 139:8001 ★     │     │    Celery Worker   (4 并发, 6 队列)                │
│    ├─ /django-admin/   → 139:8000        │     │    Celery Beat     (定时调度)                      │
│    └─ /static/ /media/ → 139:8000        │     │    PostgreSQL :5432                                │
│                                          │     │    ClickHouse :8123                                │
│  ★ 全部 /api/ 已切换至 FastAPI :8001    │     │    DolphinDB  :8848                                │
│  认证: JWT + Django Session 双模         │     │    Redis      :6379                                │
│                                          │     │    Sandbox    /opt/quant_sandbox                   │
│  代码同步: Mac → 139 → 138 (前端部署)    │     │                                                    │
└──────────────────────────────────────────┘     └────────────────────────────────────────────────────┘
```

---

## 二、前端机 (192.168.1.138)

### 2.1 Nginx 反向代理

**配置文件**: `/etc/nginx/sites-enabled/reachrich.conf`  
**源文件备份**: `/data/jupyter/ReachRich/deploy/nginx/reachrich.conf`（在 139 上）

```nginx
# 流量路由规则（优先级从高到低）
/api/sse/          → 139:8001  (FastAPI SSE, 禁用缓冲，24h 超时)
/api/ai-strategies/→ 139:8001  (FastAPI)
/api/bridge/       → 139:8001  (FastAPI, 600s 超时)
/api/              → 139:8001  (FastAPI, 已切换 ★)
/django-admin/     → 139:8000  (Django admin, 永久)
/static/           → 139:8000  (Django 静态文件)
/media/            → 139:8000  (Django 媒体文件)
/                  → 127.0.0.1:3000  (Next.js)
```

**upstream 定义**:
- `backend_api`   → `192.168.1.139:8001`（已切换至 FastAPI，可通过 `switch-backend.sh` 回切 :8000）
- `fastapi_backend`→ `192.168.1.139:8001`（始终指向 FastAPI）
- `django_backend` → `192.168.1.139:8000`（admin/static 专用）
- `frontend_next`  → `127.0.0.1:3000`

### 2.2 Next.js 前端

**代码路径**: `/data/jupyter/ReachRich/frontend/apps/web/`（在 139 上，部署到 138）  
**运行端口**: `:3000`  
**框架**: Next.js 15 (App Router) + TypeScript + Tailwind CSS

#### 环境变量文件

| 文件 | 用途 |
|------|------|
| `.env.local` | 生产覆盖（`INTERNAL_API_URL=http://192.168.1.138`） |
| `.env.production` | 默认生产值（`INTERNAL_API_URL=http://127.0.0.1`） |
| `.env.development` | 开发环境（`INTERNAL_API_URL=http://127.0.0.1:8000`） |

#### API 代理逻辑 (next.config.ts)

```
生产: Nginx 处理代理（rewrites 返回空）
开发: Next.js rewrites /api/* → NEXT_PUBLIC_API_PROXY_BASE
```

#### 页面路由

```
/                         主页 (大盘概览)
/realtime/                实时行情
/realtime/boards/         概念板块实时
/realtime/rtmarket/       实时市场 v1
/realtime/rtmarket-v2/    实时市场 v2
/limitup/                 涨停板
/limitstep/               涨停梯队
/dragon/                  龙虎榜
/dragon/hot-money/[id]/   游资详情
/analysis/                技术分析
/screener/                选股器
/sentiment/               市场情绪
/market/                  行情
/market/daily/ /weekly/ /monthly/
/news/                    新闻
/news/[id]/               新闻详情
/news/stock/[symbol]/     个股新闻
/cls/                     财联社
/ai-strategies/           AI 策略大厅
/ai-strategies/[id]/      策略详情 (含 rounds_data 研发过程)
/ai-stock/                AI 选股
/ai-strategy/             AI 策略 (旧)
/backtest/                回测
/etf/                     ETF
/finance/                 财务数据
/abnormal/                异动监控
/lottery/                 彩票
/admin/                   前端管理后台
/login/ /register/        认证
```

#### 前端代码目录

```
frontend/
├── apps/web/
│   ├── src/
│   │   ├── app/              页面 (App Router)
│   │   ├── components/       通用组件
│   │   │   ├── Navbar.tsx
│   │   │   ├── StockDetailModal.tsx
│   │   │   ├── UnifiedKLineChart.tsx
│   │   │   ├── VirtualStockTable.tsx   (虚拟滚动大表格)
│   │   │   └── ...
│   │   ├── lib/
│   │   │   ├── api.ts          Bridge/FastAPI 接口调用 + TypeScript 类型
│   │   │   ├── authApi.ts      认证接口
│   │   │   ├── server-api.ts   Server Component 专用请求
│   │   │   ├── sse.ts          SSE 长连接管理
│   │   │   └── utils.ts
│   │   ├── hooks/              自定义 React Hooks
│   │   ├── stores/             Zustand 状态管理
│   │   └── utils/
│   ├── public/
│   ├── next.config.ts
│   ├── package.json
│   └── tsconfig.json
├── packages/
│   ├── api-client/            共享 API 客户端包
│   └── shared/                共享类型和工具
├── pnpm-workspace.yaml        Monorepo 工作区配置
└── turbo.json                 Turborepo 构建配置
```

---

## 三、后端机 (192.168.1.139)

### 3.1 服务总览

| 服务 | systemd | 端口 | Python 入口 | workers |
|------|---------|------|-------------|---------|
| Django 主后端 | `reachrich-backend` | `:8000` | `ReachRich.asgi:application` | 4 |
| FastAPI 后端 | `reachrich-fastapi` | `:8001` | `fastapi_backend.main:app` | 4 |
| Celery Worker | `celery-worker` | — | `ReachRich` app | 4 并发 |
| Celery Beat | `celery-beat` | — | DatabaseScheduler | — |
| PostgreSQL 16 | `postgresql` | `:5432` | — | — |
| ClickHouse | `clickhouse-server` | `:8123` | — | — |
| DolphinDB | `dolphindb` | `:8848` | — | — |
| Redis | `redis-server` | `:6379` | — | — |

### 3.2 双后端职责说明（FastAPI 已完整接管 API）

**FastAPI :8001 已完整实现全部 276 个 API 路由**，覆盖原 Django 的所有 104 个 `/api/` 端点。
`fallback.py` 处于 dry-run 模式（Django 代理已默认关闭），任何未匹配路由直接返回 404。

**Django :8000 仅保留以下非 API 职责**：

| 职责 | Django :8000 | FastAPI :8001 |
|------|-------------|--------------|
| **所有 `/api/*` 业务接口** | 保留了历史路由定义但不再承接流量 | ✅ 唯一生产实现（276 路由） |
| `/admin/` Django 原生管理后台 | ✅ 唯一实现（ORM 管理界面） | — |
| `/static/` `/media/` 静态文件 | ✅ Django 托管 | — |
| Celery 任务调度 | ✅ Django ORM + django_celery_beat | — |
| Django Migrations | ✅ `manage.py migrate` | — |

**Nginx 路由实际走向**（已全部切换至 FastAPI）：

```nginx
/api/bridge/        → 139:8001  FastAPI
/api/ai-strategies/ → 139:8001  FastAPI
/api/sse/           → 139:8001  FastAPI (SSE 长连接)
/api/               → 139:8001  FastAPI (backend_api upstream 已切换)
/django-admin/      → 139:8000  Django Admin（仅此需要 Django）
/static/ /media/    → 139:8000  Django 托管
```

> `backend_api` upstream 已切换到 :8001。如需回切 Django，可执行 `deploy/nginx/switch-backend.sh`。

**Python 运行时**: `/srv/jupyterlab/venv/bin/python3`（主服务唯一运行时）  
**工作目录**: `/data/jupyter/ReachRich`（所有服务）  
**环境变量**: `/data/jupyter/ReachRich/.env`

### 3.2 systemd 服务文件位置

`/etc/systemd/system/` 下：

```
reachrich-backend.service   Django uvicorn :8000, 4 workers, uvloop+httptools
reachrich-fastapi.service   FastAPI uvicorn :8001, 4 workers, uvloop+httptools
celery-worker.service       4 并发, 消费队列: celery/market_data/evening_data/news/basic_data/realtime_data
celery-beat.service         DatabaseScheduler, 启动前自动 migrate + sync_celery_beat_tasks
dolphindb.service           /opt/dolphindb/server/dolphindb
```

---

## 四、代码结构 (/data/jupyter/ReachRich)

```
ReachRich/                          ← 项目根
│
├── .env                            ← 所有服务的密钥/配置（唯一真理源）
│
├── ReachRich/                      ← Django 主配置包
│   ├── settings.py                 Django 配置 (DB/Redis/Celery/DolphinDB/ClickHouse)
│   ├── urls.py                     URL 路由总入口
│   ├── asgi.py                     ASGI 入口 (uvicorn 启动点)
│   ├── celery.py                   Celery app 定义
│   └── middleware.py               全局中间件
│
├── stocks/                         ← Django 核心应用
│   ├── models/
│   │   ├── __init__.py             统一导出
│   │   ├── ai_strategy.py          AIStrategyLedger (含 rounds_data JSONField)
│   │   ├── market.py               StockInfo, MarketData, StockIndicator
│   │   ├── abnormal.py             AbnormalMovement
│   │   ├── concept.py              ConceptBoard, ConceptStock
│   │   ├── dragon_tiger.py         DragonTigerRecord
│   │   ├── report.py               FinancialReport
│   │   ├── sentiment.py            MarketSentiment, BridgeAuditLog
│   │   ├── strategy.py             Strategy, StrategyPreset
│   │   ├── support.py              SupportTicket
│   │   └── user.py                 UserProfile
│   ├── tasks/                      ← Celery 异步任务
│   │   ├── market_data.py          日行情同步
│   │   ├── realtime.py             实时行情采集
│   │   ├── intraday_collector.py   盘中采集
│   │   ├── indicators.py           技术指标计算
│   │   ├── concepts.py             概念板块同步
│   │   ├── dragon_tiger.py         龙虎榜同步
│   │   ├── sentiment.py            情绪数据
│   │   ├── report.py               财报数据
│   │   ├── cls_data.py             财联社新闻
│   │   ├── strategy.py             策略相关任务
│   │   ├── premarket.py            盘前任务
│   │   ├── maintenance.py          维护/清理任务
│   │   └── lottery_collector.py    彩票数据
│   ├── pipelines/                  ← 数据管道 (PostgreSQL↔ClickHouse↔DolphinDB)
│   │   ├── clickhouse_export.py    PostgreSQL → ClickHouse 同步
│   │   ├── dolphindb_storage.py    行情 → DolphinDB 写入
│   │   ├── realtime_snapshot.py    实时快照管道
│   │   ├── sink_dolphindb_to_clickhouse.py  DolphinDB → ClickHouse
│   │   ├── abnormal_movement.py    异动数据管道
│   │   └── timescale_storage.py    时序存储
│   ├── management/commands/        ← Django 管理命令
│   │   └── backfill_indicators.py  历史技术指标回填
│   ├── migrations/                 数据库迁移文件 (0001~0064+)
│   ├── screener/                   选股 DSL 引擎
│   ├── ai_strategy/                AI 策略生成逻辑
│   ├── factors/                    量化因子计算
│   ├── dolphindb_scripts/          DolphinDB 脚本
│   └── views/                      Django 视图 (部分 API)
│
├── fastapi_backend/                ← FastAPI 独立后端 (:8001)
│   ├── main.py                     应用入口, 挂载所有 router
│   ├── auth.py                     ★ 双模认证 (JWT Bearer + Django Session cookie)
│   ├── config.py                   统一配置 (读取 .env, pydantic Settings)
│   ├── database.py                 asyncpg SQLAlchemy 异步 DB
│   ├── redis_client.py             aioredis 异步 Redis
│   ├── api/
│   │   ├── openclaw_bridge.py      ★ Bridge API 全部端点 (43KB)
│   │   │                             GET  /api/bridge/kline/
│   │   │                             GET  /api/bridge/snapshot/
│   │   │                             GET  /api/bridge/limitup/
│   │   │                             GET  /api/bridge/concepts/
│   │   │                             GET  /api/bridge/dragon-tiger/
│   │   │                             GET  /api/bridge/sentiment/
│   │   │                             GET  /api/bridge/indicators/
│   │   │                             GET  /api/bridge/presets/
│   │   │                             POST /api/bridge/screener/
│   │   │                             POST /api/bridge/backtest/run/
│   │   │                             POST /api/bridge/strategy/save/
│   │   │                             GET  /api/bridge/ledger/
│   │   │                             GET  /api/bridge/ledger/{id}/
│   │   │                             POST /api/bridge/intraday/scan/
│   │   │                             GET  /api/bridge/system/schema   ← 元数据端点
│   │   ├── backtest_executor.py    沙箱执行器 (六层安全)
│   │   ├── viewsets.py             行情/选股主 API (72KB)
│   │   ├── realtime.py             实时行情 API (30KB)
│   │   ├── auth_views.py           认证 API (38KB)
│   │   ├── ai_strategy.py          AI 策略前端 API
│   │   ├── admin_api.py            管理后台 API
│   │   ├── websocket.py            WebSocket 实时推送
│   │   ├── sse.py                  SSE 事件流
│   │   ├── market_data.py          市场数据 API
│   │   ├── index_views.py          指数 API
│   │   ├── etf_views.py            ETF API
│   │   ├── mobile.py               移动端 API
│   │   ├── misc.py                 杂项 API
│   │   ├── screener.py             选股 API
│   │   ├── lottery.py              彩票 API
│   │   ├── trading_calendar.py     交易日历
│   │   ├── health.py               健康检查
│   │   ├── support.py              工单 API
│   │   ├── tasks.py                任务触发 API
│   │   └── fallback.py             降级响应
│   ├── middleware/
│   │   ├── bridge_guard.py         ★ HMAC-SHA256 + IP 白名单 + 限流
│   │   ├── firewall.py             防火墙中间件
│   │   └── client_detect.py        客户端类型检测
│   ├── schemas/                    Pydantic 模型
│   └── clients/
│       └── eastmoney.py            东方财富 HTTP 客户端
│
├── frontend/                       ← Next.js 前端 (部署到 138)
│   └── apps/web/                   (详见第二章)
│
├── deploy/
│   ├── systemd/                    systemd 服务文件备份
│   ├── nginx/                      Nginx 配置备份 (部署到 138)
│   │   ├── reachrich.conf          ★ 主配置 (当前生产)
│   │   ├── api.reachrich.conf      API 专用配置
│   │   └── switch-backend.sh       Django↔FastAPI 热切换脚本
│   ├── frp/
│   │   └── frpc.ini                内网穿透配置
│   └── docker/
│
├── docs/
│   └── openclaw/                   OpenClaw 接入文档
│       ├── api-manual.md           ★ Bridge API 完整手册
│       ├── data-schema-reference.md 数据表结构参考
│       ├── quant_coder_prompt.yaml  ★ LLM Prompt 模板
│       ├── bridge_client.py        Python 客户端 (供 Mac 端使用)
│       └── openclaw-integration-guide.md
│
├── scripts/                        运维脚本
├── data_factory/                   数据工厂 (Alembic 独立迁移)
└── logs/                           日志目录
```

---

## 五、沙箱 (/opt/quant_sandbox)

### 5.1 目录结构

```
/opt/quant_sandbox/
├── venv/                           ← 独立 Python 3.12 虚拟环境 (与主服务隔离)
│   ├── bin/
│   │   └── python                  沙箱专用解释器
│   └── lib/python3.12/site-packages/
│       ├── openclaw_quant_lib.py   ★ 内置工具库 (本项目自定义)
│       ├── vectorbt/               全市场向量化回测
│       ├── clickhouse_connect/     ClickHouse HTTP 驱动
│       ├── numba/                  JIT 加速 (vectorbt 依赖)
│       ├── pandas/ numpy/          数据处理
│       ├── dolphindb/              DolphinDB Python 驱动
│       ├── backtrader/             单股回测框架
│       ├── scipy/ sklearn/         科学计算
│       └── ...
├── tmp/                            ← LLM 生成代码临时存放 (执行后立即删除)
└── .numba_cache/                   Numba JIT 编译缓存 (加速二次执行)

执行用户: quant_runner (nologin shell, 无密码登录, 无 sudo)
```

### 5.2 openclaw_quant_lib.py

**路径**: `/opt/quant_sandbox/venv/lib/python3.12/site-packages/openclaw_quant_lib.py`

| 函数 | 说明 |
|------|------|
| `get_clean_kline_data(start, end, lookback_days=80)` | ClickHouse 连接 + 去重 + ST 过滤 + pivot + float64，返回 `(close_df, volume_df, raw_df)` |
| `get_clean_ch_data(...)` | `get_clean_kline_data` 的别名（兼容 Mac 端文档） |
| `get_kline_with_indicators(start, end, indicators=[...], lookback_days=120)` | SQL 窗口函数预计算指标（ma5~ma120, vol_ma5~vol_ma60 等），返回 `(close_df, ind_dfs, raw_df)` |
| `safe_float(x)` | NaN/Inf/None → `None` |
| `build_result(pf, close_df)` | 从 vectorbt Portfolio 生成标准输出 dict |

### 5.3 六层安全防护 (backtest_executor.py)

```
L1: OS 用户隔离   sudo -u quant_runner --set-home bash -c "..."
L2: 代码静态扫描  AST 解析禁止模块 + 正则禁止危险模式
L3: 进程隔离      subprocess + ulimit -v 4GB -n 64 -f 10MB + timeout
L4: 网络隔离      vectorbt 仅放行 127.0.0.1:8123(ClickHouse) 和 :8848(DolphinDB)
L5: 输出校验      最后一行必须是合法 JSON, 数值范围检查
L6: 文件清理      执行后立即 os.unlink(script_path)
```

**禁止的模块** (vectorbt 模式): `sys, subprocess, shutil, socket, http.server, urllib, pathlib, importlib, ctypes, signal, multiprocessing, threading, asyncio, pickle, shelve, webbrowser...`

**禁止的模式**: `__import__`, `eval`, `exec`, `compile`, `globals`, `locals`, `getattr`, `setattr`, `open(...,'w')`, SQL 写操作 (`INSERT/DROP/ALTER/CREATE/DELETE/TRUNCATE`)

---

## 六、数据库

### 6.1 PostgreSQL 16

- **监听**: `127.0.0.1:5432`
- **数据目录**: `/var/lib/postgresql/16/main`
- **主要表**: `stocks_stockinfo`, `stocks_marketdata`, `stocks_stockindicator`, `stocks_aistrategyledger` (含 `rounds_data` JSONField), `stocks_bridgeauditlog`, `stocks_marketsentiment`, ...

### 6.2 ClickHouse

- **监听**: `127.0.0.1:8123` (HTTP) / `9000` (TCP, 内部)
- **数据目录**: `/var/lib/clickhouse/`
- **数据库**: `reachrich`

| 表 | 说明 | 规模 |
|----|------|------|
| `daily_kline` | ★ 量化管线唯一数据源，日K线全量历史 | ~1559 万行 (2015~今) |
| `stocks_marketsentiment` | 市场情绪聚合 | ~133 行/天 |
| `factor_snapshot` | 因子快照 | ~78 万行 |
| `stock_history_daily` | 历史日线含指标 | ~37 万行 |
| `mainflow_minute` | 分钟主力资金 | — |
| `stock_snapshot_rt` | 实时快照 | 日内 |
| `feature_daily` | 预计算技术指标（量化管线不使用） | — |

### 6.3 DolphinDB

- **监听**: `0.0.0.0:8848`
- **安装目录**: `/opt/dolphindb/server/`
- **数据目录**: `/opt/dolphindb/server/local8848/`

| 表路径 | 说明 | 列数 |
|--------|------|------|
| `dfs://reachrich/stock_realtime` | 实时行情（含主力净流入） | 20 |
| `dfs://reachrich/stock_snapshot_eod` | 日终快照 | 21 |
| `dfs://reachrich/stock_history` | 历史日线含指标 (ma/rsi/macd/boll) | 27 |
| `dfs://reachrich/realtime/stock_snapshot_rt` | 实时快照+五档盘口 | 45 |
| `dfs://reachrich/realtime/realtime_mainflow` | 实时主力资金分钟 | 8 |
| `dfs://reachrich/realtime/realtime_snapshots` | 逐笔快照 | 45 |
| `dfs://reachrich/realtime/realtime_trades` | 逐笔成交 | 7 |
| `dfs://concept_realtime/concept_realtime` | 概念板块实时 | 14 |

### 6.4 Redis

- **监听**: `127.0.0.1:6379`
- **用途**: Celery 消息队列 Broker + FastAPI 响应缓存 (`bridge:` 前缀)

---

## 七、认证架构

### 7.0 Web 前端认证 — JWT + Django Session 双模

FastAPI 的 `auth.py` 和 `auth_views.py` 实现了双模认证，前端无需切换即可正常工作：

```
认证优先级:
  1. Authorization: Bearer <jwt>   → 解码 HS256 JWT (与 Django simplejwt 兼容)
  2. Cookie: sessionid=<key>       → 查 django_session 表，解析 _auth_user_id
  3. 均无                          → 返回 None (匿名)
```

**登录流程** (`POST /api/auth/login/`):
- 验证 Django pbkdf2_sha256 密码
- 创建 `django_session` 记录（与 Django 4.1+ 格式完全兼容）
- 设置 `sessionid` cookie (httponly, samesite=lax, 14天有效)
- 返回 `{success: true, user: {...}}`

**数据一致性**: FastAPI 和 Django 共享同一个 PostgreSQL `django_session` 表，
无论从哪个后端登录，会话都能被双方识别。

### 7.1 OpenClaw Bridge API 认证

所有 `/api/bridge/` 端点均受 `BridgeGuardMiddleware` 保护：

```python
# HMAC-SHA256 签名规则
message   = timestamp + path          # 注意: 不含请求体
signature = hmac(BRIDGE_SECRET, message, sha256)

# 必填 HTTP Headers
X-Bridge-Key:       {signature}       # hex string
X-Bridge-Timestamp: {unix_timestamp}  # 10 位秒级时间戳，±300s 内有效
X-Bridge-Nonce:     {uuid}            # POST 请求额外要求，防重放
```

**配置**: `.env` 中 `BRIDGE_SECRET`、`BRIDGE_ALLOWED_IPS`、`BRIDGE_RATE_LIMIT`

---

## 八、Celery 任务队列

**Broker**: `redis://127.0.0.1:6379/0`  
**调度器**: `django_celery_beat.schedulers:DatabaseScheduler`（数据库驱动，动态调整）

| 队列 | 主要任务 |
|------|---------|
| `celery` | 通用任务 |
| `market_data` | 日行情同步、指数、涨跌停 |
| `evening_data` | 盘后数据 (概念、龙虎榜、财报、指标计算) |
| `news` | 财联社新闻、情绪分析 |
| `basic_data` | 基础数据同步 |
| `realtime_data` | 盘中实时采集 |

---

## 九、GitLab 仓库与多机协作

### 9.1 仓库拓扑

```
┌──────────────────────────────────────────────────────────────────────┐
│                    192.168.1.136  (GitLab CE)                        │
│                                                                      │
│  http://192.168.1.136/reachrich/ReachRich.git   ← 唯一远程仓库      │
│  分支: master (保护 ★ 禁止直接 push, 只能通过 MR 合入)              │
│                                                                      │
│  项目成员:                                                           │
│    reachrich   Owner       ← GitLab 管理员                           │
│    rr-backend  Maintainer  ← 139 生产机 (可合并 MR 到 master)       │
│    zayl        Developer   ← Mac 主开发 (推分支、提 MR)             │
│    claw        Developer   ← Mac OpenClaw (推分支、提 MR)           │
└─────────────┬──────────────────────┬────────────────┬────────────────┘
              │                      │                │
              ▼                      ▼                ▼
┌──────────────────────┐  ┌────────────────────┐  ┌────────────────────┐
│  139 (生产机)         │  │  Mac (zayl)        │  │  Mac (claw)        │
│  user: rr-backend    │  │  user: zayl        │  │  user: claw        │
│  branch: deploy/     │  │  branch: dev/zayl  │  │  branch: dev/claw  │
│         backend      │  │  ★ 主力开发        │  │  ★ OpenClaw AI     │
│  ★ 生产部署+合并 MR  │  │                    │  │                    │
└──────────────────────┘  └────────────────────┘  └────────────────────┘
```

### 9.2 分支策略与保护规则

**核心原则：任何人不能直接 push 到 master，所有变更必须通过 Merge Request 合入。**

| 规则 | 设置 |
|------|------|
| master 直接 push | **禁止** (No one) |
| master 合并 MR | 仅 Maintainer (`rr-backend`, `reachrich`) |
| master force push | **禁止** |
| 分支命名约定 | `deploy/backend`, `dev/zayl`, `dev/claw`, `feature/*`, `fix/*` |

**分支用途**：

```
master            ← 稳定生产代码，只能通过 MR 合入
├── deploy/backend← 139 生产机工作分支 (rr-backend)
├── dev/zayl      ← zayl Mac 开发分支
├── dev/claw      ← claw Mac 开发分支 (OpenClaw)
├── feature/*     ← 功能开发分支 (任何 Developer 可创建)
└── fix/*         ← 紧急修复分支
```

**GitLab 用户与角色**：

| 用户 | 角色 | 机器 | 权限说明 |
|------|------|------|---------|
| `rr-backend` | Maintainer | 139 生产机 | 可合并 MR 到 master、部署、热修复 |
| `zayl` | Developer | Mac 主机 | 只能推自己的分支、提 MR，不能直接改 master |
| `claw` | Developer | Mac OpenClaw | 只能推自己的分支、提 MR，不能直接改 master |
| `reachrich` | Owner | — | GitLab 管理（创建用户、调整权限） |

> 初始密码：`rr-backend` → `Xk9#mP2vL7qR`，`zayl` → `Wn5#kT8xJ3bF`，`claw` → `MacDev@2026`  
> 建议首次登录 GitLab Web (`http://192.168.1.136`) 后立即修改密码。

### 9.3 新 Mac 接入步骤

**1. 克隆仓库**（使用分配给自己的 GitLab 账号）

```bash
git clone http://192.168.1.136/reachrich/ReachRich.git
cd ReachRich
```

首次 clone 会提示输入 GitLab 账号密码。配置 credential store 免密：

```bash
git config credential.helper store
# 下次输入密码后会自动保存到 ~/.git-credentials
```

**2. 配置 Git 用户信息**（必须与 GitLab 用户对应）

```bash
# zayl Mac
git config user.name "zayl"
git config user.email "zayl@reachrich.local"

# claw Mac (OpenClaw 机器)
# git config user.name "claw"
# git config user.email "claw@reachrich.local"
```

**3. 创建个人开发分支**（禁止在 master 上直接工作）

```bash
# zayl Mac
git checkout -b dev/zayl
git push -u origin dev/zayl

# claw Mac
# git checkout -b dev/claw
# git push -u origin dev/claw
```

**4. 创建本地 `.env`**

`.env` 包含数据库密码和 API 密钥，**不应提交到仓库**（已加入 .gitignore）。从模板复制：

```bash
cp .env.example .env
# 编辑 .env，填入 139 后端的实际连接参数
```

Mac 端 `.env` 关键配置（指向 139 后端）：

```env
# 数据库连接（通过内网访问 139）
DB_HOST=192.168.1.139
DB_PORT=5432
DB_NAME=reachrich

# Bridge API
BRIDGE_HOST=192.168.1.138
BRIDGE_SECRET=<从 139 的 .env 获取>

# ClickHouse（如果 Mac 端需要直连）
CH_HOST=192.168.1.139
CH_PORT=8123
```

**5. 前端开发（如需要）**

```bash
cd frontend
pnpm install
cp apps/web/.env.development apps/web/.env.local
# 编辑 .env.local，设置 NEXT_PUBLIC_API_BASE_URL 指向 138 或 139
pnpm dev
```

### 9.4 日常协作流程

**完整工作流：开发 → 提 MR → 审核合并 → 部署**

```
Mac (dev/zayl)                GitLab 136               139 (deploy/backend)
    │                              │                        │
    ├─ git add + commit            │                        │
    ├─ git push origin dev/zayl ──►│                        │
    │                              │                        │
    │  在 GitLab 上创建 MR ────────┤                        │
    │  dev/zayl → master           │                        │
    │                              │                        │
    │                    审核通过 → │ ◄── 合并 MR (Maintainer)
    │                              │ ── master 更新 ──────►│
    │                              │                        ├─ git pull origin master
    │                              │                        ├─ 重启服务
    │                              │                        │
    ├─ git pull origin master ◄────│                        │
    │  (同步最新 master)           │                        │
```

**Mac 端日常命令**：

```bash
# 1. 在自己的分支上开发
git checkout dev/zayl
# ... 编码 ...
git add -A && git commit -m "feat: xxx"
git push origin dev/zayl

# 2. 同步 master 最新代码到自己的分支
git fetch origin
git rebase origin/master
# 如有冲突，解决后 git rebase --continue
git push origin dev/zayl --force-with-lease

# 3. 在 GitLab 网页上创建 Merge Request:
#    http://192.168.1.136/reachrich/ReachRich/-/merge_requests/new
#    Source: dev/zayl → Target: master

# 4. MR 合并后，同步 master
git checkout dev/zayl
git fetch origin
git rebase origin/master
```

**139 生产机部署命令**：

```bash
cd /data/jupyter/ReachRich

# 1. 同步 master（MR 合并后）
git checkout master
git pull origin master

# 2. 重启服务
sudo systemctl restart reachrich-fastapi    # FastAPI 后端
sudo systemctl restart reachrich-backend    # Django (如改了 migration)
sudo systemctl restart celery-worker        # Celery (如改了任务)

# 3. 前端变更（在 139 构建，部署到 138）
cd frontend && pnpm build
rsync -avz apps/web/.next/ 138:/data/ReachRich/frontend/apps/web/.next/

# 4. 139 自身热修复 → 推到 deploy/backend → 提 MR
git checkout deploy/backend
git rebase origin/master
# ... 修复 ...
git add -A && git commit -m "fix: xxx"
git push origin deploy/backend
# 在 GitLab 创建 MR: deploy/backend → master
```

### 9.5 多机协作注意事项

| 场景 | 处理方式 |
|------|---------|
| 合并冲突 | 在自己分支 `git rebase origin/master` 解决，不要用 `git merge` |
| `.env` 密钥变更 | 线下同步，**不要提交 `.env` 到仓库**（已加入 .gitignore） |
| 数据库 migration | 只在 139 执行 `manage.py migrate`，Mac 不需要本地数据库 |
| 前端依赖更新 | `pnpm install` 后提交 `pnpm-lock.yaml` |
| 沙箱工具库更新 | 直接在 139 编辑 `/opt/quant_sandbox/venv/.../openclaw_quant_lib.py`，不走 Git |
| 紧急生产修复 | 139 在 `deploy/backend` 修复 → MR 合入 master → Mac 端 rebase 同步 |
| 另一台 Mac 加入 | GitLab 创建新用户 → 加入项目 Developer → clone → 创建 `dev/xxx` 分支 |

---

## 十、关键配置文件速查

| 文件 | 位置 | 说明 |
|------|------|------|
| 主配置 | `/data/jupyter/ReachRich/.env` | 所有密钥和参数 |
| Django 设置 | `ReachRich/settings.py` | DB/Celery/中间件配置 |
| FastAPI 配置 | `fastapi_backend/config.py` | pydantic Settings，读取 .env |
| Nginx (生产) | `/etc/nginx/sites-enabled/reachrich.conf` | 138 上的反向代理 |
| Nginx (备份) | `deploy/nginx/reachrich.conf` | 在 139 代码库中 |
| Django 后端服务 | `/etc/systemd/system/reachrich-backend.service` | :8000, 4 workers |
| FastAPI 服务 | `/etc/systemd/system/reachrich-fastapi.service` | :8001, 4 workers |
| Celery Worker | `/etc/systemd/system/celery-worker.service` | 4 并发, 6 队列 |
| Celery Beat | `/etc/systemd/system/celery-beat.service` | DatabaseScheduler |
| DolphinDB 服务 | `/etc/systemd/system/dolphindb.service` | :8848 |
| LLM Prompt | `docs/openclaw/quant_coder_prompt.yaml` | Backtrader/VectorBT/DolphinDB 三套 |
| Bridge API 文档 | `docs/openclaw/api-manual.md` | 完整接口手册 |
| Bridge Python 客户端 | `docs/openclaw/bridge_client.py` | Mac 端使用 |
| 沙箱工具库 | `/opt/quant_sandbox/venv/lib/python3.12/site-packages/openclaw_quant_lib.py` | 内置工具 |
| 沙箱执行器 | `fastapi_backend/api/backtest_executor.py` | 六层安全 |
| Bridge API 守卫 | `fastapi_backend/middleware/bridge_guard.py` | HMAC + IP + 限流 |

# OpenClaw 量化系统完整架构文档

> **最后更新**：2026-02-27
> **状态**：已与 `api-manual.md` v1.1 + `bridge_client.py` + `quant_coder_prompt.yaml` + `data-schema-reference.md` 完成对齐校验
> 覆盖：网络架构 · 五步流水线 · 盘中监测 · 回测引擎 · 数据 Schema · Bridge API · 沙箱安全 · Prompt 矩阵（Hybrid 加载） · strategy_params 链路 · HTTP 接口层 · Redis 存储

---

## 一、网络架构与三层体系

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
└────────┬──────────┘    │  Django  (port 8000)  │    │   ClickHouse     │
         │               │  FastAPI (port 8001)  │    └──────────────────┘
         │               │  沙箱 (quant_runner)  │
         │               └──────────▲───────────┘
         │    Nginx 反向代理          │
         ▼                          │
┌────────────────────────────┐      │
│ 192.168.1.138 前端服务器     │──────┘
│  Nginx (:80)               │
│   ├─ /           → Next.js (localhost:3000)
│   ├─ /api/bridge/  → FastAPI (139:8001)
│   ├─ /api/       → Django  (139:8000)
│   └─ /static/    → Django  (139:8000)
└────────────────────────────┘
```

| 层 | IP | 服务 | 角色 |
|----|-----|------|------|
| 控制面 | 192.168.1.188 (Mac Mini M4) | OpenClaw agents | AI 策略研究、风控决策、自动化投研 |
| 执行面 | 192.168.1.139 | Django 8000 + FastAPI 8001 | 数据 API、沙箱回测、策略归档、数据采集 |
| 展示面 | 192.168.1.138 | Nginx + Next.js 3000 | 用户 Web UI、反向代理 |

### 1.1 四种调用方与认证

| 调用方 | 路径 | 认证方式 |
|--------|------|---------|
| 用户浏览器 → Nginx → 后端 | `/api/*` | Session Cookie |
| OpenClaw Agent → FastAPI | `/api/bridge/*` | HMAC-SHA256 签名 + IP 白名单 |
| 沙箱回测代码 → FastAPI | `/api/internal/kline/` | 无（仅 127.0.0.1 可访问） |
| FastAPI → Django | 未匹配的 `/api/*` | 内部 fallback 代理 |

### 1.2 HMAC 签名算法

```python
import hmac, hashlib, time, uuid

ts = str(int(time.time()))
path = "/api/bridge/kline/"
message = f"{ts}{path}".encode()
sig = hmac.new(SECRET.encode(), message, hashlib.sha256).hexdigest()

headers = {
    "X-Bridge-Timestamp": ts,
    "X-Bridge-Key": sig,
    "X-Bridge-Nonce": uuid.uuid4().hex,  # 仅 POST 请求
}
```

- 时间戳 5 分钟有效窗口
- IP 白名单：`127.0.0.1, 192.168.1.0/24`
- 速率限制：每 IP 30 次/分钟

### 1.3 核心文件清单

| 文件 | 行数 | 职责 |
|------|------|------|
| `agents/quant_pipeline.py` | ~1200 | 五步流水线主逻辑 + Researcher/Risk/PM Prompt（内联） |
| `agents/quant_coder_prompt.yaml` | ~316 | Coder Prompt 模板（运行时由 Pipeline 加载） |
| `agents/intraday_pipeline.py` | ~390 | 盘中 DolphinDB 扫描逻辑 |
| `agents/backtest_agent.py` | ~780 | 回测执行、指标解析、策略账本、strategy_params 转发 |
| `agents/intraday_agent.py` | ~265 | 盘中命令处理 + 自动监控循环 |
| `agents/bridge_client.py` | ~210 | Bridge API 客户端 (HMAC 签名 + GET/POST 重试) |
| `webchat_api.py` | ~680 | HTTP/SSE 接口层 |

---

## 二、五步量化研发流水线 (QuantPipeline)

### 2.1 总流程

```
用户: "/quant 全市场缩量洗盘放量突破策略"
  │
  ▼
Orchestrator._handle_system_cmd("quant", topic)
  │
  ▼
quant_pipeline.run_quant_pipeline(orchestrator, topic)
  │
  ├─[Step 1] Alpha Researcher  (LLM: deepseek-r1)
  │   → Bridge API GET /snapshot/, /limitup/ 获取市场数据
  │   → 输出: 策略假说 JSON
  │
  │   最多 3 轮迭代 ──────────────────────┐
  │                                       │
  ├─[Step 2] Quant Coder (LLM: qwen-coder)│
  │   → 生成 Python 回测代码               │
  │                                       │
  ├─[Step 3] Backtest Engine (139 沙箱)    │
  │   → Bridge API POST /backtest/run/     │
  │   → 139 沙箱执行 → stdout 指标 JSON    │
  │                                       │
  ├─[Step 4] Risk Analyst (LLM: deepseek-r1)
  │   → APPROVE / REJECT                  │
  │   REJECT → fix_hint → 回到 Step 2 ───┘
  │   APPROVE ↓
  │
  ├─[Step 5] Portfolio Manager (LLM: deepseek-r1)
  │   → 撰写决策报告
  │
  └─ 保存:
     ├─ Redis lpush openclaw:quant_records
     └─ Bridge API POST /strategy/save/
```

### 2.2 流水线模式

| 模式 | 触发条件 | 数据源 | 回测引擎 |
|------|---------|--------|---------|
| **vectorbt** (默认) | `/quant` 或 `/quant_vectorbt` | ClickHouse `daily_kline` / `feature_daily` | vectorbt 全市场向量化 |
| **vectorbt_optimize** | `/quant_optimize` + base_strategy | 同上 | vectorbt |
| **vectorbt_preset** | 选择策略模板后优化 | 同上 | vectorbt |
| **backtrader** | 代码回退路径 | `/api/internal/kline/` (localhost) | Backtrader 循环遍历 |
| **backtrader_optimize** | 同上 | 同上 | Backtrader |
| **backtrader_preset** | 同上 | 同上 | Backtrader |

> 当前 `backtest_mode` 在 `quant_pipeline.py:766` 行硬编码为 `"vectorbt"`。

### 2.3 迭代重试机制

```
MAX_RETRIES = 3

错误类型 → fix_hint 策略：
├── bt_resp.error (代码报错)
│   → "上次代码报错: {stderr}，请修复"
├── bt_status == "error" + OOM/killed
│   → "执行超时/内存不足，将更多计算下推到 ClickHouse"
├── bt_status == "timeout"
│   → "代码执行超时，优化性能"
├── bt_status == "no_metrics"
│   → "未输出标准指标，检查 print(json.dumps(result))"
└── Risk REJECT
    → risk_suggestions 传入下一轮 fix_hint
```

---

## 三、Prompt 矩阵

### 3.0 Prompt 管理方式（Hybrid 模式）

| 角色 | 存储位置 | 加载方式 | 说明 |
|------|----------|---------|------|
| Researcher | `quant_pipeline.py` 内联 | 直接引用常量 | 含数据能力边界、两段式架构约束 |
| Coder | `quant_coder_prompt.yaml` | `_load_coder_prompts()` 运行时加载 + 模块级缓存 | 方便独立调优，YAML 加载失败时 fallback 到内联 |
| Risk Analyst | `quant_pipeline.py` 内联 | 直接引用常量 | 含硬性风控阈值 |
| PM | `quant_pipeline.py` 内联 | 直接引用常量 | 决策报告模板 |

> **权威参考文件**：`api-manual.md`（接口规范）、`data-schema-reference.md`（数据 Schema）、`quant_coder_prompt.yaml`（Coder 模板）、`bridge_client.py`（客户端实现）

### 3.1 Researcher Prompt（Step 1）

| Prompt 常量 | 用途 | 输出 JSON 关键字段 |
|-------------|------|-------------------|
| `VECTORBT_RESEARCHER_PROMPT` | 新策略研发 | `name, clickhouse_logic, dolphindb_logic, start_date, end_date, strategy_params` |
| `VECTORBT_OPTIMIZE_RESEARCHER_PROMPT` | 策略优化 | 同上 + `improvements` |
| `VECTORBT_PRESET_RESEARCHER_PROMPT` | 模板转化 | 同上 + `improvements` |
| `RESEARCHER_PROMPT` | backtrader 新策略 | `name, logic, stock, start_date, end_date, strategy_params` |
| `OPTIMIZE_RESEARCHER_PROMPT` | backtrader 优化 | 同上 |
| `PRESET_RESEARCHER_PROMPT` | backtrader 模板 | 同上 |

**Researcher 的数据能力边界（写入 Prompt）：**

| 数据源 | 可用数据 |
|--------|---------|
| ClickHouse `daily_kline` | 全A股日线OHLCV（1560万行, 6816只股, 2015至今） |
| ClickHouse `feature_daily` | 预计算指标宽表（~185万行, 仅近3个月）— 量化管线不使用，Coder 统一从 daily_kline 计算 |
| ClickHouse `stocks_marketsentiment` | 情绪数据（sentiment_score, limit_up_count 等） |
| DolphinDB `stock_realtime` | 盘中逐秒行情（1.17亿行） |
| DolphinDB `stock_snapshot_rt` | 五档盘口（5974万行） |
| DolphinDB `realtime_mainflow` | 主力资金明细 |

**两段式架构强制（vectorbt 模式）：**
- `clickhouse_logic`: 盘后全市场选股（截面/多因子）
- `dolphindb_logic`: 盘中高频介入信号

### 3.2 Coder Prompt（Step 2 — YAML 加载）

| YAML key / 内联常量 | 用途 | 数据连接 | 加载方式 |
|---------------------|------|---------|---------|
| `vectorbt_coder.system_prompt` | 生成 vectorbt 回测代码 | `clickhouse_connect → daily_kline / feature_daily` | YAML 优先 |
| `VECTORBT_OPTIMIZE_CODER_PROMPT` | 优化已有 vectorbt 代码 | 同上 | 内联（含 base_code 注入） |
| `quant_coder.system_prompt` | backtrader 代码 | `/api/internal/kline/` + `--stocks` 参数 | YAML 优先 |
| `OPTIMIZE_CODER_PROMPT` | 优化 backtrader 代码 | 同上 | 内联（含 base_code 注入） |

**Coder 关键防错规范（全部写入 Prompt）：**

| 规范 | 说明 |
|------|------|
| `client.query_df("SQL")` | 查询必须用此方法；❌ `client.query(...).df_result` 不存在 |
| `pd.set_option('future.no_silent_downcasting', True)` | 避免 pandas FutureWarning |
| `vbt.settings.array_wrapper["freq"] = "1d"` | vectorbt 频率设置，否则年化指标报错 |
| `df.drop_duplicates()` + `df.pivot()` | pivot 前必须去重 |
| `df[~df["name"].str.contains("ST", na=False)]` | 过滤 ST 股 |
| `pf.trades.win_rate()` | 必须加括号，是方法不是属性 |
| `safe_float()` | 处理 NaN/Inf → 输出 null |
| `volume` 非 `vol` | ClickHouse 列名严格匹配 |
| `daily_kline` 非 `daily_factors` | ClickHouse 表名严格匹配 |
| `.ffill()` 非 `fillna(method='ffill')` | pandas 2.x 兼容 |

**Coder 可用表选择逻辑：**
```
策略需要 MA/MACD/KDJ/RSI/BOLL → 方式A: 直接查 feature_daily
策略需要原始 OHLCV + 自定义指标 → 方式B: daily_kline + 窗口函数
⚠️ feature_daily 不包含: EXPMA/EMA/DPO/CCI/OBV/ATR — 需从 daily_kline 计算
```

**vectorbt 标准输出格式：**
```python
def safe_float(x):
    v = float(x)
    return round(v, 4) if np.isfinite(v) else None

result = {
    "total_return": safe_float(pf.total_return().mean()),
    "sharpe":       safe_float(pf.sharpe_ratio().mean()),
    "max_drawdown": safe_float(pf.max_drawdown().mean()),
    "win_rate":     safe_float(pf.trades.win_rate().mean()),
    "trades":       int(pf.trades.count().sum()),
    "cagr":         safe_float(pf.annualized_return().mean()),
    "num_stocks":   close_df.shape[1],
    "num_days":     close_df.shape[0],
}
print(json.dumps(result))
```

### 3.3 Risk Prompt（Step 4）

| Prompt 常量 | 引擎 | 硬性阈值 |
|-------------|------|---------|
| `VECTORBT_RISK_PROMPT` | vectorbt | Sharpe ≥ 0.8, MaxDD < 30%, Trades ≥ 30, WinRate ≥ 45% |
| `RISK_PROMPT` | backtrader | Sharpe ≥ 0.5, Trades ≥ 5, WinRate ≥ 35% |

**REJECT 时建议约束：** 放宽 ClickHouse 选股条件，而非改动 DolphinDB 盘中逻辑。

### 3.4 PM Prompt（Step 5）

`PM_PROMPT` — 综合决策报告：策略名、逻辑评估、回测指标、风险、最终决定。报告通过 `POST /api/bridge/strategy/save/` 归档。

---

## 四、盘中监测系统 (IntradayPipeline)

### 4.1 两阶段流程

```
阶段一：盘后选股（15:00 后执行）
    run_post_market_selection()
    → 调用 run_quant_pipeline() 执行完整五步流水线
    → 通过策略筛出股票池
    → 保存到 Redis (TTL 48h)

阶段二：盘中扫描（09:30-15:00 循环执行）
    run_intraday_monitor()
    → 每 120 秒执行一次 run_intraday_scan()
    → LLM 生成 DolphinDB 扫描代码
    → Bridge API POST /intraday/scan/ 提交到 139 沙箱执行
    → 从响应 metrics 中提取 signals
    → 保存到 Redis + 推送告警
```

### 4.2 盘中 Coder Prompt

`INTRADAY_CODER_PROMPT` 关键要素：

| 要素 | 说明 |
|------|------|
| 连接 | `dolphindb.session()` → `127.0.0.1:8848` / `quant_sandbox` / `os.getenv("DOLPHIN_PWD")` |
| 核心原则 | "计算下推" — 在 DolphinDB 端完成聚合，只返回信号 |
| 参数 | `argparse --pool`（逗号分隔股票代码） |
| 输出 | `{"signals": [...], "scanned": N, "timestamp": "..."}` |

### 4.3 盘中扫描响应处理

Bridge API `/intraday/scan/` 的响应结构与沙箱回测相同：
```json
{
  "success": true,
  "metrics": {"signals": [...], "scanned": N, "timestamp": "..."},
  "code_hash": "...",
  "run_id": "...",
  "mode": "vectorbt"
}
```
客户端需从 `resp["metrics"]` 中提取 `signals` 和 `scanned`，而非从顶层读取。

### 4.4 Orchestrator 盘中命令路由

| 命令 | 处理函数 | 说明 |
|------|---------|------|
| `intraday_select` | `run_post_market_selection()` | 盘后选股 |
| `intraday_monitor` | `send("intraday", "start_monitor")` | 启动盘中监控循环 |
| `intraday_scan` | `send("intraday", "scan")` | 单次扫描 |
| `intraday_status` | `send("intraday", "get_status")` | 查询状态 |
| `intraday_pool` | `send("intraday", "get_pool")` | 查看股票池 |
| `intraday_stop` | `send("intraday", "stop_monitor")` | 停止监控 |

---

## 五、回测执行引擎 (BacktestAgent)

### 5.1 执行优先级

```
vectorbt 模式:
  1. Bridge API POST /api/bridge/backtest/run/ (mode=vectorbt, timeout=60s)

backtrader 模式:
  1. Bridge API (mode=backtrader)
  2. SSH → quant_runner@139 直接执行
  3. 本地 subprocess + akshare 缓存
```

### 5.2 Bridge API 回测响应格式

```json
// 成功
{"success": true, "metrics": {"cagr": 0.15, "sharpe": 1.2, ...}, "code_hash": "...", "run_id": "...", "mode": "vectorbt"}

// 失败
{"success": false, "error": "Code validation failed: ...", "code_hash": "...", "mode": "vectorbt"}
```

BacktestAgent 内部通过 `_normalize_bridge_result()` 将上述格式统一为：
```json
{"status": "success|error|timeout|no_metrics", "metrics": {...}, "message": "..."}
```

### 5.3 指标解析（SSH/本地模式）

从沙箱 stdout 中提取：
1. 搜索 `BACKTEST_RESULT:` 前缀的 JSON
2. 回退：搜索最后一行 JSON（包含 `cagr` 或 `total_return`）
3. 标准化字段：`cagr, total_return, max_drawdown, sharpe, win_rate, trades`

### 5.4 Agent Actions

| Action | 说明 |
|--------|------|
| `run_backtest` | 执行回测代码 |
| `validate_code` | 代码安全检查 |
| `save_ledger` | 保存策略到账本 |
| `list_ledger` | 列出账本（响应 key: `items`） |
| `list_strategies` | 列出策略模板（Bridge 预设 + 本地账本） |
| `get_strategy` | 获取策略详情 |

---

## 六、数据层 — ClickHouse（历史日线回测）

### 6.1 连接方式

```python
import os, clickhouse_connect
client = clickhouse_connect.get_client(
    host='127.0.0.1', port=8123, database='reachrich',
    username='default', password=os.getenv('CH_PWD'))
df = client.query_df("SELECT ... FROM daily_kline WHERE ...")
```

### 6.2 daily_kline — 主力表

全市场日线行情，vectorbt 回测的核心数据源。**~1559 万行 / 6816 只股票 / 2015-01-05 至今**

| 列名 | 类型 | 说明 |
|------|------|------|
| `trade_date` | Date | 交易日期 (格式 'YYYY-MM-DD') |
| `ts_code` | String | 股票代码 (如 000001.SZ) |
| `name` | String | 股票名称（用于过滤 ST） |
| `open`, `high`, `low`, `close` | Float64 | OHLC |
| `pre_close` | Float64 | 前收盘价 |
| `volume` | Float64 | 成交量 (**注意: 不是 vol**) |
| `amount` | Float64 | 成交额 |
| `pct_chg` | Float64 | 涨跌幅 (%) |
| `turnover_rate` | Float64 | 换手率 (%) |
| `amplitude` | Float64 | 振幅 (%) |
| `main_net_sum` | Float64 | 主力净流入 |

**窗口函数查询模板：**
```sql
SELECT trade_date, ts_code, close, volume, pct_chg,
    avg(close)  OVER w5  AS ma5,
    avg(close)  OVER w20 AS ma20,
    avg(volume) OVER w5  AS vol_ma5
FROM daily_kline
WHERE trade_date >= '2024-09-01' AND trade_date <= '2025-12-31'
WINDOW
    w5  AS (PARTITION BY ts_code ORDER BY trade_date ROWS 4 PRECEDING),
    w20 AS (PARTITION BY ts_code ORDER BY trade_date ROWS 19 PRECEDING)
ORDER BY trade_date, ts_code
```
> 如果策略需要 MA60，查询 WHERE 需向前扩展至少 80 个自然日，否则前 N 行窗口不满。

### 6.3 feature_daily — 预计算指标宽表

**~96 万行**。策略涉及 MA + MACD + KDJ + RSI + BOLL 的多因子选股时，直接查此表更快。

| 列名 | 说明 |
|------|------|
| `ts_code`, `trade_date`, `name`, `close` | 基础字段 |
| `ma5`, `ma10`, `ma20`, `ma30`, `ma60` | 均线 |
| `vol_ma5`, `vol_ma10` | 量能均线 |
| `macd`, `macd_signal`, `macd_hist` | MACD 三线 |
| `k_value`, `d_value`, `j_value` | KDJ |
| `rsi_6`, `rsi_12` | RSI |
| `boll_upper`, `boll_mid`, `boll_lower` | 布林带 |

> ⚠️ **不包含**：EXPMA/EMA/DPO/CCI/OBV/ATR — 需从 daily_kline 窗口函数或 pandas 计算

### 6.4 其他 ClickHouse 表

| 表名 | 行数 | 说明 |
|------|------|------|
| `factor_snapshot` | ~72 万 | 因子快照（version + JSON factors） |
| `stocks_marketsentiment` | ~133 | 市场情绪（涨停数、情绪分、攻击力分等） |

---

## 七、数据层 — DolphinDB（盘中实时）

### 7.1 连接方式

```python
import os, dolphindb as ddb
s = ddb.session()
s.connect("127.0.0.1", 8848, "quant_sandbox", os.getenv("DOLPHIN_PWD"))
```
> 沙箱账户 `quant_sandbox` 仅有只读权限，禁止写入/删除。

### 7.2 表清单

| 表名 | 库路径 | 数据规模 | 用途 |
|------|--------|---------|------|
| `stock_realtime` ⭐ | `dfs://reachrich` | 1.17亿行 | 盘中逐秒行情 |
| `stock_snapshot_rt` ⭐ | `dfs://reachrich/realtime` | 5974万行 | 五档盘口 |
| `realtime_mainflow` | `dfs://reachrich/realtime` | 1万行 | 主力资金分钟级 |
| `stock_history` | `dfs://reachrich` | 215万行 | 历史日线+指标 |
| `stock_snapshot_eod` | `dfs://reachrich` | 22万行 | 日终快照 |
| `realtime_snapshots` | `dfs://reachrich/realtime` | 820万行 | 逐笔快照 |
| `realtime_trades` | `dfs://reachrich` | 29万行 | 逐笔成交 |
| `concept_realtime` | `dfs://concept_realtime` | - | 概念板块实时 |

### 7.3 stock_realtime 核心列

盘中每 3-6 秒推送，全市场 5000+ 只股票：

| 列名 | 类型 | 说明 |
|------|------|------|
| `update_time` | TIMESTAMP | 推送时间 |
| `ts_code` | SYMBOL | 股票代码 |
| `price`, `pct_chg`, `change` | DOUBLE | 最新价/涨跌幅/涨跌额 |
| `volume`, `amount` | DOUBLE | 累计成交量/额 |
| `open`, `high`, `low`, `pre_close` | DOUBLE | OHLC |
| `turnover_rate`, `speed` | DOUBLE | 换手率/涨速 |
| `main_net`, `vol_ratio` | DOUBLE | 主力净流入/量比 |
| `total_mv`, `circ_mv`, `pb` | DOUBLE | 总市值/流通市值/市净率 |

**计算下推查询模板：**
```python
script = """
    t = loadTable("dfs://reachrich", "stock_realtime")
    latest = select last(price) as price, last(pct_chg) as pct_chg,
                    last(volume) as volume, last(main_net) as main_net,
                    last(vol_ratio) as vol_ratio
             from t
             where date(update_time) = today()
             group by ts_code
    select * from latest where pct_chg > 5.0 and vol_ratio > 2.0
           order by pct_chg desc limit 20
"""
result = s.run(script)
```

### 7.4 stock_snapshot_rt 五档盘口

| 列名 | 说明 |
|------|------|
| `bid1`~`bid5`, `bid_vol1`~`bid_vol5` | 买五档价/量 |
| `ask1`~`ask5`, `ask_vol1`~`ask_vol5` | 卖五档价/量 |
| `main_net`, `main_buy`, `main_sell` | 主力净/买/卖 |

### 7.5 realtime_mainflow 主力资金

| 列名 | 说明 |
|------|------|
| `main_net` | 主力净流入 |
| `super_net`, `large_net` | 超大单/大单净流入 |
| `medium_net`, `small_net` | 中单/小单净流入 |

---

## 八、Bridge API 接口清单（192.168.1.139:8001）

### 8.1 GET 查询端点

| 端点 | 参数 | 缓存 | 响应 key |
|------|------|------|---------|
| `/api/bridge/snapshot/` | 无 | 30s | `trade_date, total_stocks, limit_up, sentiment{...}` |
| `/api/bridge/limitup/` | `trade_date?` | 1min | `limit_up[], limit_down[], conn_board_ladder{}` |
| `/api/bridge/concepts/` | `limit?=50` | 2min | `concepts[]` |
| `/api/bridge/dragon-tiger/` | `trade_date?, limit?=50` | — | `items[]` |
| `/api/bridge/sentiment/` | `limit?=20` | 2min | `news[], stats{}` |
| `/api/bridge/kline/` | `ts_code, period?, start_date?, end_date?, limit?=250, fmt?=json` | 5min | `data[]` |
| `/api/bridge/indicators/` | `ts_code, limit?=60` | — | `indicators[]` |
| `/api/bridge/presets/` | 无 | — | `presets[]` |
| `/api/bridge/ledger/` | `status?, page?=1, page_size?=20` | — | `items[]` |
| `/api/bridge/ledger/{id}/` | — | — | 完整策略记录 |

### 8.2 POST 执行端点

| 端点 | 请求体关键字段 | 超时 | 响应格式 |
|------|--------------|------|---------|
| `/api/bridge/backtest/run/` | `strategy_code, stock?, start_date, end_date, timeout, mode` | 620s | `{success, metrics, code_hash, run_id, mode}` |
| `/api/bridge/intraday/scan/` | `strategy_code, stock_pool?, timeout?=30` | 620s | `{success, metrics, code_hash, run_id, mode}` |
| `/api/bridge/screener/` | `payload{filters, outputs, universe}, limit` | 620s | 筛选结果 |
| `/api/bridge/strategy/save/` | `title, topic, status, strategy_code, backtest_metrics, ...` | 620s | `{id, code_hash, status}` |

### 8.3 沙箱内部端点（仅 localhost）

| 端点 | 说明 |
|------|------|
| `GET /api/internal/kline/` | backtrader 模式回测代码专用，参数同 bridge/kline |

### 8.4 K线数据字段差异

| 来源 | 成交量字段 | 数值类型 |
|------|----------|---------|
| Bridge API `fmt=json` | `vol` (字符串) | 全部字符串，需 `pd.to_numeric()` |
| Bridge API `fmt=backtrader` | `volume` (数值) | 全部数值 |
| ClickHouse `daily_kline` | `volume` (Float64) | 全部数值 |

---

## 九、沙箱安全约束

### 9.1 回测模式差异

Pipeline 默认使用 `vectorbt` 模式，支持通过前端参数 `mode` 或环境变量切换为 `backtrader`：

```
前端 body.mode → webchat_api → orchestrator → run_quant_pipeline(backtest_mode=...)
```

| | backtrader | vectorbt |
|---|---|---|
| 数据源 | `/api/internal/kline/`（localhost） | ClickHouse + DolphinDB |
| 股票范围 | 单股（`stock` 必填） | 全市场（`stock` 忽略） |
| 内存限制 | 2 GB | 4 GB |
| 超时上限 | 600 秒 | 60 秒 |
| 允许 `os` | ❌ | ✅ 仅 `os.getenv()` / `os.environ.get()` |
| 网络访问 | 仅 8001 端口 | 额外放行 8123（ClickHouse）/ 8848（DolphinDB） |

### 9.1.1 strategy_params 传递链路

Researcher 输出的 `strategy_params`（如 `holding_days`, `stop_loss`, `take_profit`）自动透传到沙箱：

```
Pipeline → orchestrator.send("backtest", "run_backtest", {strategy_params: {...}})
  → backtest_agent._handle_run_backtest() → params.get("strategy_params")
    → Bridge API: POST /backtest/run/ {strategy_params: {...}}
    → SSH: python script.py --start ... --end ... --params '{JSON}'
```

沙箱代码通过 `argparse --params` 接收并解析为 `json.loads(args.params)`。

### 9.2 可用库

| 模式 | 可用库 |
|------|--------|
| backtrader | `backtrader, pandas, numpy, requests, json, argparse, math, datetime` |
| vectorbt | `vectorbt, pandas, numpy, clickhouse_connect, dolphindb, json, argparse, math, datetime, os`（仅 getenv） |

### 9.3 禁止项

**禁止 import**：`os, sys, subprocess, shutil, socket, http.server, urllib, pathlib, importlib, ctypes, signal, multiprocessing, threading, asyncio, pickle, shelve, webbrowser, code, codeop, compileall`

**禁止模式**：`__import__(), eval(), exec(), compile(), globals(), locals(), getattr(), setattr(), delattr(), open(...,'w'), open(...,'a'), breakpoint(), __builtins__, __subclasses__`

**禁止外部数据源**：`yfinance, akshare, tushare, baostock, efinance`

**vectorbt 额外禁止**：SQL 写入（INSERT/DROP/ALTER/CREATE/DELETE/TRUNCATE）、DolphinDB 破坏性调用（dropDatabase/dropTable）、ClickHouse 写入（client.command()/client.insert()）

---

## 十、BridgeClient 客户端 (bridge_client.py)

### 10.1 初始化

```python
from agents.bridge_client import get_bridge_client
client = get_bridge_client()
```

环境变量：`BRIDGE_SECRET`（必填）、`BRIDGE_BASE_URL`（默认 `http://192.168.1.139:8001/api/bridge`）

### 10.2 方法一览

| 方法 | 端点 | Agent 使用者 |
|------|------|-------------|
| `get_snapshot()` | /snapshot/ | Market Agent / Alpha Researcher |
| `get_limitup(trade_date?)` | /limitup/ | Market Agent / Alpha Researcher |
| `get_concepts(limit?)` | /concepts/ | Market Agent |
| `get_sentiment(limit?)` | /sentiment/ | News Agent |
| `get_dragon_tiger(trade_date?)` | /dragon-tiger/ | Market Agent |
| `get_kline(ts_code, period?, start_date?, end_date?, limit?, fmt?)` | /kline/ | Quant Agent |
| `get_indicators(ts_code, limit?)` | /indicators/ | Quant Agent |
| `get_presets()` | /presets/ | Strategist Agent |
| `run_screener(payload, limit?)` | /screener/ | Quant Agent |
| `run_backtest(code, stock?, start_date?, end_date?, timeout?, mode?)` | /backtest/run/ | Backtest Agent |
| `run_intraday_scan(strategy_code, stock_pool?, timeout?)` | /intraday/scan/ | Execution Agent |
| `save_strategy(title, ...)` | /strategy/save/ | Portfolio Manager |
| `get_ledger(status?, page?)` | /ledger/ | Portfolio Manager |
| `get_ledger_detail(ledger_id)` | /ledger/{id}/ | Portfolio Manager |

---

## 十一、HTTP API 接口层（webchat_api.py, 端口 7789）

### 11.1 量化研发

| 方法 | 端点 | 说明 |
|------|------|------|
| POST | `/api/quant/stream` | SSE 实时研发流（主入口） |
| POST | `/api/quant/optimize` | 策略优化 |
| GET | `/api/quant/records` | 研发记录列表 |
| GET | `/api/quant/records/{id}` | 记录详情 |
| GET | `/api/strategies` | 策略模板库 |
| GET | `/api/strategies/{id}` | 模板详情 |

### 11.2 盘中监测

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/api/intraday/status` | 监测状态 |
| POST | `/api/intraday/select` | 盘后选股 |
| POST | `/api/intraday/scan` | 单次扫描 |
| POST | `/api/intraday/monitor` | 启动监控 |
| POST | `/api/intraday/stop` | 停止监控 |

### 11.3 SSE 事件类型

| type | 基本字段 | 扩展字段（可选） | 说明 |
|------|----------|-----------------|------|
| `started` | `run_id`, `topic` | — | 流水线启动 |
| `step` | `step`, `title`, `status`, `content` | `detail`, `stdout`, `stderr`, `exec_mode`, `code_hash`, `attempt` | 步骤进度。`content` 为摘要（≤300 字），`detail` 为完整日志（≤3000 字），前端可折叠/弹窗展示 |
| `code` | `step`, `title`, `content` | `code_len`, `attempt` | Coder 生成的代码（截断 2000 字） |
| `metrics` | `step`, `title`, `content`, `metrics` | `exec_mode`, `code_hash`, `stdout`, `attempt` | 回测成功的指标 JSON |
| `done` | `result`, `content` | — | 流水线完成 |
| `final` | `text` | — | 最终结果 |
| `error` | `content` | — | 全局错误 |
| `heartbeat` | — | — | keep-alive |
| `close` | — | — | 流结束 |

> **前端展示建议**：`step` 事件中 `status=error` 时，默认显示 `content` 摘要，点击可展开 `detail` 查看完整报错。
> `stdout`/`stderr` 字段可放入"执行日志"折叠面板，方便调试。

---

## 十二、Redis 存储

### 12.1 量化研发

| Redis Key | 类型 | 说明 |
|-----------|------|------|
| `openclaw:quant_records` | List | 研发记录（最近100条） |
| `openclaw:quant_progress:{run_id}` | PubSub | SSE 实时进度流 |
| `openclaw:reply:{msg_id}` | PubSub | 最终回复 |

### 12.2 盘中监测

| Key | 类型 | TTL | 说明 |
|-----|------|-----|------|
| `openclaw:daily_pool` | String(JSON) | 48h | 盘后选股结果 |
| `openclaw:daily_pool:meta` | String(JSON) | 48h | 选股元信息 |
| `openclaw:intraday:signals` | List | - | 买入信号（最近200条） |
| `openclaw:intraday:scan_log` | List | - | 扫描日志（最近500条） |

### 12.3 账本保存路径

1. 优先：Bridge API `POST /api/bridge/strategy/save/`
2. 回退：本地 Markdown `DECISION_LEDGER_DIR`

---

## 十三、Agent 角色 → API 映射

```
┌─────────────────┐     GET /snapshot/, /limitup/, /concepts/, /dragon-tiger/
│  Alpha Researcher│────────────────────────────────────────────────────────►
│  (deepseek-r1)  │     分析市场数据，提出策略假说
└────────┬────────┘
         │ 策略假说
         ▼
┌─────────────────┐     vectorbt: ClickHouse 直连 / backtrader: /internal/kline/
│   Quant Coder   │────────────────────────────────────────────────────────►
│  (qwen-coder)   │     生成 Python 回测代码
└────────┬────────┘
         │ 回测代码
         ▼
┌─────────────────┐     POST /backtest/run/ (mode=vectorbt|backtrader)
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

### 全 Agent 端点速查

| Agent | 主要端点 | 用途 |
|-------|---------|------|
| **Market Agent** | `/bridge/snapshot/`, `/bridge/limitup/`, `/bridge/concepts/`, `/bridge/dragon-tiger/` | 盘中/盘后市场全貌 |
| **News Agent** | `/bridge/sentiment/`, `/cls/news/latest/`, `/sentiment/dashboard/` | 舆情监控与情绪分析 |
| **Alpha Researcher** | 综合 Market + News Agent 的输出 | 提出策略假说 |
| **Quant Coder** | vectorbt 模式: ClickHouse 直连 / backtrader 模式: `/api/internal/kline/` | 编写回测代码 |
| **Backtest Engine** | `/bridge/backtest/run/` (mode=backtrader/vectorbt) | 提交代码到沙箱执行 |
| **Execution Agent** | `/bridge/intraday/scan/` | 盘中 DolphinDB 信号扫描 |
| **Risk Analyst** | 纯 LLM 判断，不直接调 API | 审核回测指标 |
| **Portfolio Manager** | `/bridge/strategy/save/`, `/bridge/ledger/` | 归档策略、查看账本 |
| **Strategist Agent** | `/bridge/presets/`, `/screener/presets/` | 策略库管理与筛选 |
| **Analysis Agent** | `/analysis/reports/latest/`, `/sentiment/dashboard/` | 每日复盘 |

---

## 十四、风控阈值

```yaml
vectorbt_risk_thresholds:
  min_sharpe: 0.8
  max_drawdown: -0.30
  min_trades: 30
  min_win_rate: 0.45

backtrader_risk_thresholds:
  min_sharpe: 0.5
  max_drawdown: -0.30
  min_trades: 5
  min_win_rate: 0.35
```

不满足上述阈值的策略被 Risk Analyst 标记为 `REJECT`，触发 Quant Coder 重新迭代（最多 3 轮）。

---

## 十五、错误码参考

| HTTP 状态 | 含义 | 常见原因 |
|-----------|------|----------|
| 200 | 成功 | — |
| 401 | 认证失败 | HMAC 签名错误、缺少 headers、时间戳过期 |
| 403 | 禁止访问 | IP 不在白名单 |
| 404 | 资源不存在 | 无数据或 ledger_id 无效 |
| 429 | 限流 | 超过 30 次/分钟 |
| 500 | 服务器错误 | 后端异常，查看日志 |

---

## 十六、已知问题与修复记录

| 问题 | 位置 | 状态 |
|------|------|------|
| `run_post_market_selection` status 检查缺少 `"APPROVE"` | intraday_pipeline.py:174 | ✅ 已修复 |
| webchat_api 盘中接口使用未定义 `_send_command` | webchat_api.py | ✅ 已修复 → `send_to_orchestrator()` |
| Bridge `/intraday/scan/` 响应解析错误 — 直接读顶层 `signals` | intraday_pipeline.py:271 | ✅ 已修复 → 从 `resp["metrics"]` 提取 |
| ledger 列表响应 key 不匹配 (`results`/`data` vs `items`) | backtest_agent.py:495 | ✅ 已修复 → 优先读取 `items` |
| quant_coder_prompt.yaml 与 pipeline/API 手册不一致 | quant_coder_prompt.yaml | ✅ 已修复 → 对齐 win_rate()、移除 pandas_ta、补充 cagr |
| clawagent 非登录用户 launchd 域不活跃 | 系统级 | ⚠️ 待修复 → 重启时只杀特定 PID |

---

## 十七、服务进程清单

| 服务 | 启动命令 | 端口 |
|------|---------|------|
| orchestrator | `python -m agents.orchestrator` | - |
| webchat | `python webchat_api.py` | 7789 |
| webui | `python webui.py --ip 0.0.0.0 --port 7788` | 7788 |
| telegram-bot | `python telegram_bot.py` | - |
| telegram-agent | `python telegram_agent.py` | - |
| analysis-agent | `python -m agents.analysis_agent` | - |
| backtest-agent | `python -m agents.backtest_agent` | - |
| browser-agent | `python -m agents.browser_agent` | - |
| desktop-agent | `python -m agents.desktop_agent` | - |
| dev-agent | `python -m agents.dev_agent` | - |
| feishu-bot | `python -u feishu_bot.py` | - |
| market-agent | `python -m agents.market_agent` | - |
| news-agent | `python -m agents.news_agent` | - |
| strategist-agent | `python -m agents.strategist_agent` | - |
| intraday-agent | `python -m agents.intraday_agent` | - |

所有服务以 `clawagent` 用户运行，工作目录 `/Users/clawagent/openclaw`（webui 为 `/Users/clawagent/openclaw-webui`）。

---

## 十八、环境变量速查

| 变量 | 必填 | 位置 | 说明 |
|------|------|------|------|
| `BRIDGE_SECRET` | ✅ | Mac Mini (telegram.env) | HMAC 签名密钥 |
| `BRIDGE_BASE_URL` | — | Mac Mini (telegram.env) | 默认 `http://192.168.1.139:8001/api/bridge` |
| `CH_PWD` | ✅ | 139 沙箱 SANDBOX_ENV | ClickHouse 密码（vectorbt 模式） |
| `DOLPHIN_PWD` | ✅ | 139 沙箱 SANDBOX_ENV | DolphinDB 密码（盘中扫描） |
| `REDIS_URL` | — | Mac Mini | 默认 `redis://127.0.0.1:6379/0` |
| `BACKTEST_TIMEOUT` | — | Mac Mini | SSH 回测超时，默认 1200s |
| `INTRADAY_SCAN_INTERVAL` | — | Mac Mini | 盘中扫描间隔，默认 120s |
| `INTRADAY_AUTO_SCAN` | — | Mac Mini | 自动扫描开关，默认 `"1"` |

"""
OpenClaw Intraday Pipeline — 盘后选股 + 盘中 DolphinDB 实时监控闭环。

流程:
1. 盘后 Phase (20:00): vectorbt 全市场回测 → 筛选出目标股票池 → Redis 存储
2. 盘中 Phase (09:30~15:00): 定时向 139 发送 DolphinDB 扫描代码 → 检测入场信号 → 推送告警

DolphinDB 数据源 (139 服务器):
- dfs://reachrich/stock_realtime: 1.17 亿行 Tick 级数据
- dfs://reachrich/realtime/stock_snapshot_rt: 5974 万行快照
- dfs://reachrich/stock_snapshot_eod: EOD 快照

沙箱安全:
- DolphinDB 连接使用 quant_sandbox 只读用户 (D0 前置)
- 密码通过 os.getenv("DOLPHIN_PWD") 注入，不出现在代码/日志中
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, date
from typing import Optional

logger = logging.getLogger(__name__)

POOL_REDIS_KEY = "rragent:daily_pool"
POOL_META_KEY = "rragent:daily_pool:meta"
SIGNALS_KEY = "rragent:intraday:signals"
SCAN_LOG_KEY = "rragent:intraday:scan_log"

SCAN_INTERVAL_SEC = 120
MAX_SCAN_PER_SESSION = 150

# ── Execution Coder Prompt: 教 LLM 生成 DolphinDB 扫描代码 ──

INTRADAY_CODER_PROMPT = """你是 OpenClaw 的盘中高频交易开发专家 (Execution Coder)。
编写 Python 代码利用 DolphinDB 实时监控特定股票池，寻找日内最佳介入点。

策略逻辑: {strategy_logic}
目标股票池: {stock_pool_desc}

【执行环境】
代码在 192.168.1.139 沙箱运行。核心原则: "计算下推" — 将计算逻辑编写为 DolphinDB 脚本字符串，
通过 session.run() 在数据库端完成聚合，只将命中信号的少量结果返回 Python。

【连接模板】
```python
import os, json, argparse
from datetime import datetime
import dolphindb as ddb

parser = argparse.ArgumentParser()
parser.add_argument("--pool", required=True, help="逗号分隔的股票代码")
args = parser.parse_args()

stock_list = [s.strip() for s in args.pool.split(",") if s.strip()]

s = ddb.session()
s.connect("127.0.0.1", 8848, "quant_sandbox", os.getenv("DOLPHIN_PWD"))
```

【可用 DolphinDB 表（quant_sandbox 只读）】

1. stock_realtime ⭐ 盘中实时行情（1.17亿行, 每3-6秒推送）
   库: dfs://reachrich  表名: stock_realtime
   列: update_time(TIMESTAMP), ts_code(SYMBOL), name(STRING),
       price, pct_chg, change, volume, amount, open, high, low, pre_close(DOUBLE),
       turnover_rate, speed(涨速), main_net(主力净流入), vol_ratio(量比),
       amplitude, total_mv, circ_mv, pb(DOUBLE)

2. stock_snapshot_rt ⭐ 高频快照含五档盘口（5974万行）
   库: dfs://reachrich/realtime  表名: stock_snapshot_rt
   列: ts(TIMESTAMP), ts_code(SYMBOL), price/open/high/low(DOUBLE),
       bid1~bid5, bid_vol1~bid_vol5, ask1~ask5, ask_vol1~ask_vol5(DOUBLE),
       main_net, main_buy, main_sell, pe, pb(DOUBLE)

3. realtime_mainflow 主力资金分钟级（1万行）
   库: dfs://reachrich/realtime  表名: realtime_mainflow
   列: ts(TIMESTAMP), ts_code(SYMBOL),
       main_net, super_net, large_net, medium_net, small_net(DOUBLE)

4. stock_history 历史日线+指标（215万行）
   库: dfs://reachrich  表名: stock_history
   列: trade_date(DATE), ts_code(SYMBOL), name, OHLC, volume, amount,
       ma5~ma60, vol_ma5/10, atr_14, rsi_6/14, macd/signal/hist, boll三线(DOUBLE)

5. stock_snapshot_eod 日终快照（22万行）
   库: dfs://reachrich  表名: stock_snapshot_eod

6. concept_realtime 概念板块实时
   库: dfs://concept_realtime  表名: concept_realtime
   列: trade_date(DATE), board_code(SYMBOL), board_name(STRING),
       latest_price, pct_chg, up_count, down_count,
       leading_stock_name, leading_stock_pct

【查询模板 — DolphinDB SQL via session.run()】
```python
codes_str = "`" + "`".join(stock_list)
script = f\"\"\"
    t = loadTable("dfs://reachrich", "stock_realtime")
    latest = select last(price) as price, last(pct_chg) as pct_chg,
                    last(volume) as volume, last(main_net) as main_net,
                    last(vol_ratio) as vol_ratio, last(open) as open_price,
                    last(high) as high, last(low) as low
             from t
             where date(update_time) = today(), ts_code in [{codes_str}]
             group by ts_code
    select * from latest where pct_chg > 2.0 order by pct_chg desc
\"\"\"
result = s.run(script)
```

【输出格式 — 最后一行 print JSON】
```python
signals = []
for _, row in result.iterrows():
    if meets_entry_condition(row):
        signals.append({{
            "ts_code": str(row["ts_code"]),
            "price": float(row["price"]),
            "signal": "BUY",
            "reason": "...",
            "strength": 0.85,
        }})

print(json.dumps({{"signals": signals, "scanned": len(stock_list), "timestamp": str(datetime.now())}}))
```
或无信号时: print(json.dumps({{"status": "NO_SIGNAL"}}))

【禁止】
- 任何写操作 (dropDatabase, dropTable, INSERT, ALTER 等)
- yfinance, akshare, subprocess, socket
- 仅允许的 os 用法: os.getenv("DOLPHIN_PWD")
- 代码必须在 30 秒内完成

只输出完整可运行的 Python 代码，不要解释。用 ```python ... ``` 包裹。"""


async def get_daily_pool(redis_client) -> dict:
    """从 Redis 获取盘后选股结果"""
    pool_raw = await redis_client.get(POOL_REDIS_KEY)
    if not pool_raw:
        return {}
    try:
        return json.loads(pool_raw)
    except Exception:
        return {}


async def save_daily_pool(redis_client, pool: dict, meta: dict = None):
    """保存盘后选股结果到 Redis，TTL 48 小时"""
    await redis_client.set(POOL_REDIS_KEY, json.dumps(pool, ensure_ascii=False, default=str), ex=172800)
    if meta:
        await redis_client.set(POOL_META_KEY, json.dumps(meta, ensure_ascii=False, default=str), ex=172800)


async def run_post_market_selection(orchestrator, strategy_name: str = "",
                                    notify_fn=None, progress_channel: str = "") -> dict:
    """
    盘后选股: 调用 vectorbt 全市场回测 pipeline，将筛选出的股票存入 Redis。

    返回: {status, pool, metrics, strategy_name}
    """
    import redis.asyncio as aioredis
    from agents.quant_pipeline import run_quant_pipeline

    topic = strategy_name or "盘后全市场筛选 — 明日目标池"

    result = await run_quant_pipeline(orchestrator, topic, notify_fn=notify_fn, progress_channel=progress_channel)

    if result.get("status") not in ("ACCEPT", "APPROVE", "success"):
        return {"status": "no_pool", "message": f"盘后选股未通过风控: {result.get('status')}", "result": result}

    metrics = result.get("metrics", {})
    code = result.get("code", "")
    strategy_name = result.get("name", topic)

    stocks_traded = []
    if isinstance(metrics, dict):
        stocks_traded = metrics.get("stocks_traded_list", [])

    if not stocks_traded and code:
        stocks_traded = _extract_traded_stocks_from_code(code)

    pool = {
        "stocks": stocks_traded,
        "strategy_name": strategy_name,
        "strategy_code": code[:5000],
        "metrics": metrics,
        "created_at": datetime.now().isoformat(),
        "date": date.today().isoformat(),
    }

    r = aioredis.from_url("redis://localhost:6379/0", decode_responses=True)
    try:
        await save_daily_pool(r, pool, meta={"strategy": strategy_name, "count": len(stocks_traded)})
    finally:
        await r.aclose()

    if notify_fn:
        await notify_fn(f"📋 盘后选股完成: {strategy_name}\n目标池 {len(stocks_traded)} 只: {', '.join(stocks_traded[:10])}...")

    return {"status": "ok", "pool": pool, "strategy_name": strategy_name}


def _extract_traded_stocks_from_code(code: str) -> list[str]:
    """尝试从回测代码输出中提取有交易的股票列表（fallback）"""
    return []


async def run_intraday_scan(orchestrator, stock_pool: list[str],
                            strategy_logic: str,
                            notify_fn=None, progress_channel: str = "") -> dict:
    """
    单次盘中扫描: LLM 生成 DolphinDB 代码 → Bridge API 执行 → 返回信号。

    Args:
        orchestrator: Orchestrator 实例
        stock_pool: 目标股票代码列表
        strategy_logic: 策略逻辑描述
        notify_fn: 通知回调
        progress_channel: Redis progress channel

    Returns:
        {status, signals, scanned, scan_code}
    """
    import re
    import redis.asyncio as aioredis
    from agents.llm_router import get_llm_router
    from agents.bridge_client import get_bridge_client

    if not stock_pool:
        return {"status": "error", "message": "股票池为空，请先执行盘后选股"}

    router = get_llm_router()
    bridge = get_bridge_client()

    pool_desc = f"{len(stock_pool)} 只: {', '.join(stock_pool[:20])}" + ("..." if len(stock_pool) > 20 else "")

    coder_reply = await router.chat([
        {"role": "system", "content": "你是盘中实时监控代码工程师，使用 DolphinDB 查询盘中数据。只输出 Python 代码。"},
        {"role": "user", "content": INTRADAY_CODER_PROMPT.format(
            strategy_logic=strategy_logic,
            stock_pool_desc=pool_desc,
        )},
    ], task_type="code")

    if not coder_reply:
        return {"status": "error", "message": "Execution Coder LLM 不可用"}

    code_match = re.search(r"```python\s*(.*?)\s*```", coder_reply, re.DOTALL)
    scan_code = code_match.group(1) if code_match else coder_reply

    pool_csv = ",".join(stock_pool)

    try:
        resp = await bridge.run_intraday_scan(
            strategy_code=scan_code,
            stock_pool=stock_pool,
            timeout=30,
        )
    except Exception as e:
        return {"status": "error", "message": f"Bridge intraday/scan 调用失败: {e}", "scan_code": scan_code}

    if not resp:
        return {"status": "error", "message": "Bridge 返回空", "scan_code": scan_code}

    if not resp.get("success", False):
        err = resp.get("error", "") or resp.get("detail", "") or resp.get("message", "执行失败")
        return {"status": "error", "message": f"沙箱执行失败: {err}", "scan_code": scan_code}

    scan_output = resp.get("metrics", {})
    if isinstance(scan_output, str):
        try:
            scan_output = json.loads(scan_output)
        except (json.JSONDecodeError, TypeError):
            scan_output = {}

    signals = scan_output.get("signals", [])
    scanned = scan_output.get("scanned", len(stock_pool))

    r = aioredis.from_url("redis://localhost:6379/0", decode_responses=True)
    try:
        log_entry = json.dumps({
            "timestamp": datetime.now().isoformat(),
            "signals": signals,
            "scanned": scanned,
            "strategy": strategy_logic[:200],
        }, ensure_ascii=False, default=str)
        await r.lpush(SCAN_LOG_KEY, log_entry)
        await r.ltrim(SCAN_LOG_KEY, 0, 499)

        if signals:
            await r.lpush(SIGNALS_KEY, json.dumps(signals, ensure_ascii=False, default=str))
            await r.ltrim(SIGNALS_KEY, 0, 199)
    finally:
        await r.aclose()

    if signals and notify_fn:
        signal_lines = []
        for sig in signals[:5]:
            signal_lines.append(f"  🎯 {sig.get('ts_code', '?')} @ {sig.get('price', '?')} [{sig.get('reason', '')}]")
        await notify_fn(f"⚡ 盘中信号 ({len(signals)} 只命中 / {scanned} 只扫描):\n" + "\n".join(signal_lines))

    return {"status": "ok", "signals": signals, "scanned": scanned, "scan_code": scan_code}


async def run_intraday_monitor(orchestrator, strategy_logic: str = "",
                               notify_fn=None, progress_channel: str = "",
                               interval: int = SCAN_INTERVAL_SEC,
                               max_scans: int = MAX_SCAN_PER_SESSION) -> dict:
    """
    盘中持续监控循环: 每隔 interval 秒扫描一次，直到收盘或达到上限。

    在非交易时段调用会立即返回。
    """
    import redis.asyncio as aioredis
    from agents.market_time import is_trading_hours, get_market_phase, MarketPhase

    if not is_trading_hours():
        phase = get_market_phase()
        return {"status": "not_trading", "message": f"当前非交易时段 ({phase.value})，盘中监控仅在 09:30~15:00 运行"}

    r = aioredis.from_url("redis://localhost:6379/0", decode_responses=True)
    try:
        pool_data = await get_daily_pool(r)
    finally:
        await r.aclose()

    stock_pool = pool_data.get("stocks", [])
    saved_strategy = pool_data.get("strategy_name", "")

    if not stock_pool:
        return {"status": "no_pool", "message": "Redis 无目标股票池，请先执行盘后选股 (/intraday_select)"}

    logic = strategy_logic or f"根据 {saved_strategy} 策略，监控目标股票池的盘中入场信号"

    if notify_fn:
        await notify_fn(f"🔄 盘中监控启动: {len(stock_pool)} 只目标股 | 间隔 {interval}s | 策略: {saved_strategy}")

    all_signals = []
    scan_count = 0

    for i in range(max_scans):
        if not is_trading_hours():
            logger.info("交易时段结束，停止盘中监控 (共 %d 次扫描)", scan_count)
            break

        scan_count += 1
        logger.info("盘中扫描 #%d / max %d", scan_count, max_scans)

        result = await run_intraday_scan(
            orchestrator, stock_pool, logic,
            notify_fn=notify_fn, progress_channel=progress_channel,
        )

        if result.get("signals"):
            all_signals.extend(result["signals"])

        if i < max_scans - 1 and is_trading_hours():
            await asyncio.sleep(interval)

    return {
        "status": "ok",
        "total_scans": scan_count,
        "total_signals": len(all_signals),
        "signals": all_signals[-50:],
        "strategy": saved_strategy,
    }


async def get_intraday_status(redis_client) -> dict:
    """获取盘中监控状态（供 API 调用）"""
    pool_data = await get_daily_pool(redis_client)
    pool_meta_raw = await redis_client.get(POOL_META_KEY)
    recent_signals_raw = await redis_client.lrange(SIGNALS_KEY, 0, 4)
    recent_logs_raw = await redis_client.lrange(SCAN_LOG_KEY, 0, 4)

    pool_meta = json.loads(pool_meta_raw) if pool_meta_raw else {}
    recent_signals = [json.loads(s) for s in recent_signals_raw] if recent_signals_raw else []
    recent_logs = [json.loads(s) for s in recent_logs_raw] if recent_logs_raw else []

    return {
        "pool": {
            "count": len(pool_data.get("stocks", [])),
            "stocks": pool_data.get("stocks", [])[:20],
            "strategy": pool_data.get("strategy_name", ""),
            "date": pool_data.get("date", ""),
            "meta": pool_meta,
        },
        "recent_signals": recent_signals,
        "recent_scans": recent_logs,
    }

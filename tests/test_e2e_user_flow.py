"""
端到端用户流程测试 — 模拟真实用户通过 RRAgent 查询行情和调用策略选股。

测试两条核心路径：
  Path 1: MCP Server → BridgeClient → ReachRich API（行情查询）
  Path 2: MCP Server → BridgeClient → ReachRich API（策略选股）

使用 Mock BridgeClient 模拟 ReachRich 服务端响应，
验证从用户请求到最终数据返回的完整链路。
"""
from __future__ import annotations

import asyncio
import json
import sys
import time

sys.path.insert(0, ".")


# ── Mock BridgeClient — 模拟 ReachRich FastAPI 服务端 ──

class MockBridgeClient:
    """模拟 ReachRich Bridge API 的所有响应。

    数据格式完全对齐 ReachRich FastAPI 实际返回格式。
    """

    def __init__(self, **kwargs):
        self.call_log: list[tuple[str, dict]] = []

    async def get_snapshot(self) -> dict:
        self.call_log.append(("get_snapshot", {}))
        return {
            "status": "ok",
            "market_status": "trading",
            "update_time": "2026-04-10T14:30:00",
            "total_stocks": 5300,
            "up_count": 2800,
            "down_count": 2100,
            "flat_count": 400,
            "limit_up": 45,
            "limit_down": 8,
            "avg_change_pct": 0.85,
            "total_volume": 8500_0000_0000,
            "total_amount": 9200_0000_0000,
        }

    async def get_limitup(self, trade_date: str = "") -> dict:
        self.call_log.append(("get_limitup", {"trade_date": trade_date}))
        return {
            "status": "ok",
            "trade_date": trade_date or "20260410",
            "count": 3,
            "data": [
                {
                    "ts_code": "300456.SZ", "name": "赛微电子",
                    "close": 42.50, "pct_chg": 20.0,
                    "amount": 15_0000_0000,
                    "limit_type": "首板", "industry": "半导体",
                    "concept": "芯片,MEMS",
                    "first_limit_time": "09:32:15",
                    "open_count": 0,
                },
                {
                    "ts_code": "002049.SZ", "name": "紫光国微",
                    "close": 98.70, "pct_chg": 10.02,
                    "amount": 32_0000_0000,
                    "limit_type": "二连板", "industry": "半导体",
                    "concept": "芯片,军工",
                    "first_limit_time": "10:15:30",
                    "open_count": 2,
                },
                {
                    "ts_code": "600584.SH", "name": "长电科技",
                    "close": 55.30, "pct_chg": 10.01,
                    "amount": 28_0000_0000,
                    "limit_type": "首板", "industry": "半导体封测",
                    "concept": "芯片封测,先进封装",
                    "first_limit_time": "13:45:00",
                    "open_count": 1,
                },
            ],
        }

    async def get_concepts(self, limit: int = 50) -> dict:
        self.call_log.append(("get_concepts", {"limit": limit}))
        return {
            "status": "ok",
            "count": 3,
            "data": [
                {"name": "半导体", "change_pct": 4.52, "turnover_rate": 8.3,
                 "stock_count": 85, "up_count": 72, "limit_up_count": 5},
                {"name": "人工智能", "change_pct": 3.21, "turnover_rate": 6.1,
                 "stock_count": 120, "up_count": 95, "limit_up_count": 3},
                {"name": "新能源汽车", "change_pct": -0.45, "turnover_rate": 4.2,
                 "stock_count": 95, "up_count": 30, "limit_up_count": 0},
            ],
        }

    async def get_kline(self, ts_code: str, period: str = "daily",
                        start_date: str = "", end_date: str = "",
                        limit: int = 250, fmt: str = "json") -> dict:
        self.call_log.append(("get_kline", {
            "ts_code": ts_code, "period": period, "limit": limit,
        }))
        return {
            "status": "ok",
            "ts_code": ts_code,
            "period": period,
            "count": 5,
            "data": [
                {"trade_date": "20260410", "open": 41.20, "close": 42.50,
                 "high": 42.50, "low": 40.80, "vol": 3500_0000, "amount": 15_0000_0000},
                {"trade_date": "20260409", "open": 39.50, "close": 40.10,
                 "high": 40.50, "low": 39.20, "vol": 2800_0000, "amount": 11_0000_0000},
                {"trade_date": "20260408", "open": 38.80, "close": 39.60,
                 "high": 39.80, "low": 38.50, "vol": 2200_0000, "amount": 8_5000_0000},
                {"trade_date": "20260407", "open": 39.00, "close": 38.90,
                 "high": 39.50, "low": 38.20, "vol": 1900_0000, "amount": 7_2000_0000},
                {"trade_date": "20260404", "open": 38.00, "close": 38.80,
                 "high": 39.10, "low": 37.80, "vol": 2100_0000, "amount": 8_0000_0000},
            ],
        }

    async def get_indicators(self, ts_code: str, limit: int = 60) -> dict:
        self.call_log.append(("get_indicators", {"ts_code": ts_code, "limit": limit}))
        return {
            "status": "ok",
            "ts_code": ts_code,
            "data": {
                "ma5": 39.98, "ma10": 39.20, "ma20": 38.50, "ma60": 36.80,
                "rsi_6": 72.5, "rsi_14": 65.3,
                "macd": 0.85, "macd_signal": 0.62, "macd_hist": 0.23,
                "kdj_k": 78.2, "kdj_d": 65.8, "kdj_j": 103.0,
                "boll_upper": 43.50, "boll_mid": 39.20, "boll_lower": 34.90,
                "vol_ratio": 1.85, "turnover_rate": 8.3,
            },
        }

    async def get_sentiment(self, limit: int = 20) -> dict:
        self.call_log.append(("get_sentiment", {"limit": limit}))
        return {
            "status": "ok",
            "update_time": "2026-04-10T14:30:00",
            "summary": {
                "up_down_ratio": 2.8,
                "limit_up": 45, "limit_down": 8,
                "consecutive_limit_up": 12,
                "avg_change_pct": 0.85,
                "market_emotion": "偏多",
                "north_flow": 52.3,
            },
        }

    async def get_dragon_tiger(self, trade_date: str = "") -> dict:
        self.call_log.append(("get_dragon_tiger", {"trade_date": trade_date}))
        return {
            "status": "ok",
            "trade_date": trade_date or "20260410",
            "count": 2,
            "data": [
                {"ts_code": "300456.SZ", "name": "赛微电子",
                 "reason": "涨幅偏离值达7%",
                 "buy_total": 8_0000_0000, "sell_total": 3_0000_0000,
                 "net_buy": 5_0000_0000},
                {"ts_code": "002049.SZ", "name": "紫光国微",
                 "reason": "连续三个交易日涨幅偏离值累计达20%",
                 "buy_total": 12_0000_0000, "sell_total": 9_0000_0000,
                 "net_buy": 3_0000_0000},
            ],
        }

    async def get_presets(self) -> dict:
        self.call_log.append(("get_presets", {}))
        return {
            "status": "ok",
            "presets": [
                {
                    "id": "preset_semiconductor_momentum",
                    "name": "半导体动量选股",
                    "description": "筛选半导体板块中短期动量突破的个股",
                    "conditions": [
                        {"field": "concept", "operator": "contains", "value": "半导体"},
                        {"field": "pct_chg_5d", "operator": ">", "value": 10},
                        {"field": "volume_ratio", "operator": ">", "value": 1.5},
                        {"field": "ma5", "operator": ">", "value": "ma20"},
                    ],
                },
                {
                    "id": "preset_value_low_pe",
                    "name": "低估值价值股",
                    "description": "PE<15, ROE>15%, 连续3年分红",
                    "conditions": [
                        {"field": "pe_ttm", "operator": "<", "value": 15},
                        {"field": "roe", "operator": ">", "value": 15},
                        {"field": "dividend_years", "operator": ">=", "value": 3},
                    ],
                },
                {
                    "id": "preset_limit_up_replay",
                    "name": "涨停复盘",
                    "description": "今日首板，封单金额>1亿，开板次数<=1",
                    "conditions": [
                        {"field": "limit_type", "operator": "==", "value": "首板"},
                        {"field": "seal_amount", "operator": ">", "value": 1_0000_0000},
                        {"field": "open_count", "operator": "<=", "value": 1},
                    ],
                },
            ],
        }

    async def run_screener(self, payload: dict, limit: int = 50) -> dict:
        self.call_log.append(("run_screener", {"payload": payload, "limit": limit}))
        return {
            "status": "ok",
            "screener_id": "screen_20260410_001",
            "execution_time_ms": 1850,
            "result_count": 5,
            "data": [
                {
                    "ts_code": "300456.SZ", "name": "赛微电子",
                    "close": 42.50, "pct_chg": 20.0,
                    "pct_chg_5d": 35.2, "volume_ratio": 3.2,
                    "pe_ttm": 45.6, "market_cap": 180_0000_0000,
                    "industry": "半导体", "concept": "芯片,MEMS",
                    "ma5": 40.10, "ma20": 36.50, "rsi_14": 78.5,
                    "score": 95,
                },
                {
                    "ts_code": "002049.SZ", "name": "紫光国微",
                    "close": 98.70, "pct_chg": 10.02,
                    "pct_chg_5d": 22.8, "volume_ratio": 2.8,
                    "pe_ttm": 38.2, "market_cap": 650_0000_0000,
                    "industry": "半导体", "concept": "芯片,军工",
                    "ma5": 92.30, "ma20": 85.60, "rsi_14": 72.1,
                    "score": 88,
                },
                {
                    "ts_code": "603986.SH", "name": "兆易创新",
                    "close": 125.80, "pct_chg": 6.5,
                    "pct_chg_5d": 18.3, "volume_ratio": 2.1,
                    "pe_ttm": 52.1, "market_cap": 840_0000_0000,
                    "industry": "半导体", "concept": "存储芯片,MCU",
                    "ma5": 120.50, "ma20": 112.30, "rsi_14": 68.3,
                    "score": 82,
                },
                {
                    "ts_code": "688981.SH", "name": "中芯国际",
                    "close": 78.90, "pct_chg": 5.8,
                    "pct_chg_5d": 15.6, "volume_ratio": 1.9,
                    "pe_ttm": 65.3, "market_cap": 6200_0000_0000,
                    "industry": "半导体制造", "concept": "芯片代工",
                    "ma5": 75.20, "ma20": 71.80, "rsi_14": 64.7,
                    "score": 78,
                },
                {
                    "ts_code": "600584.SH", "name": "长电科技",
                    "close": 55.30, "pct_chg": 10.01,
                    "pct_chg_5d": 12.5, "volume_ratio": 1.7,
                    "pe_ttm": 28.5, "market_cap": 490_0000_0000,
                    "industry": "半导体封测", "concept": "芯片封测,先进封装",
                    "ma5": 52.10, "ma20": 49.80, "rsi_14": 70.2,
                    "score": 75,
                },
            ],
        }

    async def get_ledger(self, status: str = "", page: int = 1) -> dict:
        self.call_log.append(("get_ledger", {"status": status, "page": page}))
        return {
            "status": "ok",
            "count": 1,
            "data": [
                {
                    "id": 101, "title": "半导体动量策略 v2",
                    "status": "MONITORING",
                    "created_at": "2026-04-10T10:30:00",
                    "strategy_code": "semiconductor_momentum_v2.py",
                    "backtest_sharpe": 1.85,
                    "backtest_max_drawdown": -0.12,
                    "selected_stocks": ["300456.SZ", "002049.SZ", "603986.SH"],
                },
            ],
        }

    async def get_system_schema(self) -> dict:
        self.call_log.append(("get_system_schema", {}))
        return {
            "clickhouse": {
                "daily_kline": ["trade_date", "ts_code", "open", "close", "high", "low", "vol"],
                "stock_basic": ["ts_code", "name", "area", "industry", "market"],
            },
            "dolphindb": {
                "stock_realtime": ["update_time", "ts_code", "price", "bid", "ask"],
            },
            "api_list": [
                "get_snapshot", "get_limitup", "get_concepts", "get_kline",
                "get_indicators", "get_sentiment", "get_dragon_tiger",
                "get_presets", "run_screener", "get_ledger", "get_system_schema",
            ],
        }

    async def close(self):
        pass


# ── Mock Redis Pub/Sub — 模拟实时数据推送 ──

class MockPubSub:
    def __init__(self):
        self._messages = []
        self._subscribed = []

    async def subscribe(self, *channels):
        self._subscribed.extend(channels)

    async def unsubscribe(self, *channels):
        pass

    async def close(self):
        pass

    def inject(self, channel, data):
        if isinstance(data, str):
            data = data.encode()
        self._messages.append({
            "type": "message",
            "channel": channel.encode() if isinstance(channel, str) else channel,
            "data": data,
        })

    async def get_message(self, ignore_subscribe_messages=True, timeout=0.1):
        if self._messages:
            return self._messages.pop(0)
        return None


class MockRedis:
    def __init__(self):
        self._pubsub = MockPubSub()

    def pubsub(self):
        return self._pubsub


# ── 测试场景 ──

async def test_scenario_1_realtime_market():
    """场景1: 用户问「今天行情怎么样？涨停板有哪些？」

    模拟 LLM 调用链:
      1. market_snapshot → 大盘总览
      2. market_limitup → 今日涨停
      3. market_sentiment → 市场情绪
      4. market_concepts → 板块排名
    """
    print("=" * 60)
    print("场景1: 查询实时行情")
    print("  用户: 今天行情怎么样？涨停板有哪些半导体？")
    print("=" * 60)

    from rragent.tools.mcp.reachrich_server import ReachRichMCPServer

    server = ReachRichMCPServer()
    server._bridge_client = MockBridgeClient()
    bc = server._bridge_client

    # Step 1: LLM 先获取大盘总览
    print("\n[Step 1] LLM 调用 market_snapshot...")
    result = await server._call_tool("market_snapshot", {})
    assert not result.get("isError"), f"market_snapshot failed: {result}"
    data = json.loads(result["content"][0]["text"])
    assert data["status"] == "ok"
    assert data["total_stocks"] == 5300
    assert data["limit_up"] == 45
    print(f"  ✓ 大盘: {data['total_stocks']}只股票, "
          f"涨{data['up_count']} 跌{data['down_count']}, "
          f"涨停{data['limit_up']} 跌停{data['limit_down']}")

    # Step 2: LLM 查看涨停板详情
    print("\n[Step 2] LLM 调用 market_limitup...")
    result = await server._call_tool("market_limitup", {"trade_date": ""})
    assert not result.get("isError")
    data = json.loads(result["content"][0]["text"])
    assert data["count"] == 3
    semi_stocks = [s for s in data["data"] if "半导体" in s.get("industry", "")]
    print(f"  ✓ 涨停板 {data['count']} 只, 其中半导体 {len(semi_stocks)} 只:")
    for s in data["data"]:
        print(f"    {s['ts_code']} {s['name']} +{s['pct_chg']}% "
              f"({s['limit_type']}) {s['industry']}")

    # Step 3: LLM 查看市场情绪
    print("\n[Step 3] LLM 调用 market_sentiment...")
    result = await server._call_tool("market_sentiment", {"limit": 20})
    assert not result.get("isError")
    data = json.loads(result["content"][0]["text"])
    summary = data["summary"]
    print(f"  ✓ 情绪: {summary['market_emotion']}, "
          f"涨跌比 {summary['up_down_ratio']}, "
          f"连板 {summary['consecutive_limit_up']} 只, "
          f"北向资金 +{summary['north_flow']}亿")

    # Step 4: LLM 查看板块排名
    print("\n[Step 4] LLM 调用 market_concepts...")
    result = await server._call_tool("market_concepts", {"limit": 10})
    assert not result.get("isError")
    data = json.loads(result["content"][0]["text"])
    print(f"  ✓ 板块排名 ({data['count']} 个):")
    for c in data["data"]:
        print(f"    {c['name']}: {c['change_pct']:+.2f}% "
              f"(涨停{c['limit_up_count']}只, 换手{c['turnover_rate']}%)")

    # 验证调用日志
    assert len(bc.call_log) == 4
    assert bc.call_log[0][0] == "get_snapshot"
    assert bc.call_log[1][0] == "get_limitup"
    assert bc.call_log[2][0] == "get_sentiment"
    assert bc.call_log[3][0] == "get_concepts"
    print("\n  ✓ 全部 4 次 BridgeClient 调用验证通过")
    print("  PASS: 场景1 完成\n")


async def test_scenario_2_strategy_screening():
    """场景2: 用户问「帮我用半导体动量策略选股」

    模拟 LLM 调用链:
      1. market_presets → 查看可用策略列表
      2. market_screener → 运行选股器 (使用策略条件)
      3. market_kline → 获取选出股票的 K 线
      4. market_indicators → 获取技术指标
      5. market_ledger → 记录到决策台账
    """
    print("=" * 60)
    print("场景2: 策略选股")
    print("  用户: 帮我用半导体动量策略选股，给我分析一下结果")
    print("=" * 60)

    from rragent.tools.mcp.reachrich_server import ReachRichMCPServer

    server = ReachRichMCPServer()
    server._bridge_client = MockBridgeClient()
    bc = server._bridge_client

    # Step 1: LLM 查看可用策略
    print("\n[Step 1] LLM 调用 market_presets 查看策略列表...")
    result = await server._call_tool("market_presets", {})
    assert not result.get("isError")
    data = json.loads(result["content"][0]["text"])
    presets = data["presets"]
    assert len(presets) == 3
    print(f"  ✓ 发现 {len(presets)} 个预设策略:")
    for p in presets:
        print(f"    [{p['id']}] {p['name']} — {p['description']}")

    # Step 2: LLM 选择半导体动量策略并运行选股
    print("\n[Step 2] LLM 调用 market_screener 运行「半导体动量选股」...")
    screener_payload = {
        "preset_id": "preset_semiconductor_momentum",
        "conditions": [
            {"field": "concept", "operator": "contains", "value": "半导体"},
            {"field": "pct_chg_5d", "operator": ">", "value": 10},
            {"field": "volume_ratio", "operator": ">", "value": 1.5},
            {"field": "ma5", "operator": ">", "value": "ma20"},
        ],
        "sort_by": "score",
        "order": "desc",
    }
    result = await server._call_tool("market_screener", {
        "payload": screener_payload,
        "limit": 10,
    })
    assert not result.get("isError")
    data = json.loads(result["content"][0]["text"])
    assert data["status"] == "ok"
    assert data["result_count"] == 5
    print(f"  ✓ 选股完成: {data['result_count']} 只命中 "
          f"(耗时 {data['execution_time_ms']}ms)")
    print(f"  ✓ 选出股票:")
    for i, s in enumerate(data["data"], 1):
        print(f"    {i}. {s['ts_code']} {s['name']} "
              f"现价{s['close']} 今涨{s['pct_chg']:+.1f}% "
              f"5日涨{s['pct_chg_5d']:+.1f}% "
              f"量比{s['volume_ratio']} 评分{s['score']}")

    # Step 3: LLM 获取排名第一的股票 K 线做分析
    top_stock = data["data"][0]
    print(f"\n[Step 3] LLM 调用 market_kline 分析 {top_stock['name']}...")
    result = await server._call_tool("market_kline", {
        "ts_code": top_stock["ts_code"],
        "period": "daily",
        "limit": 5,
    })
    assert not result.get("isError")
    kline = json.loads(result["content"][0]["text"])
    assert kline["ts_code"] == top_stock["ts_code"]
    print(f"  ✓ K线数据 ({kline['count']} 根):")
    for bar in kline["data"][:3]:
        chg = (bar["close"] - bar["open"]) / bar["open"] * 100
        print(f"    {bar['trade_date']}: "
              f"开{bar['open']} 高{bar['high']} 低{bar['low']} 收{bar['close']} "
              f"({chg:+.1f}%)")

    # Step 4: LLM 获取技术指标
    print(f"\n[Step 4] LLM 调用 market_indicators 查看 {top_stock['name']} 指标...")
    result = await server._call_tool("market_indicators", {
        "ts_code": top_stock["ts_code"],
        "limit": 60,
    })
    assert not result.get("isError")
    ind = json.loads(result["content"][0]["text"])
    d = ind["data"]
    print(f"  ✓ 技术指标:")
    print(f"    均线: MA5={d['ma5']} MA10={d['ma10']} MA20={d['ma20']} MA60={d['ma60']}")
    print(f"    RSI: RSI6={d['rsi_6']} RSI14={d['rsi_14']}")
    print(f"    MACD: DIF={d['macd']} DEA={d['macd_signal']} 柱={d['macd_hist']}")
    print(f"    KDJ: K={d['kdj_k']} D={d['kdj_d']} J={d['kdj_j']}")
    print(f"    布林: 上轨{d['boll_upper']} 中轨{d['boll_mid']} 下轨{d['boll_lower']}")
    print(f"    量比={d['vol_ratio']} 换手率={d['turnover_rate']}%")

    # Step 5: LLM 查看决策台账
    print(f"\n[Step 5] LLM 调用 market_ledger 查看历史决策记录...")
    result = await server._call_tool("market_ledger", {"status": "", "page": 1})
    assert not result.get("isError")
    ledger = json.loads(result["content"][0]["text"])
    assert ledger["count"] == 1
    entry = ledger["data"][0]
    print(f"  ✓ 台账记录 ({ledger['count']} 条):")
    print(f"    [{entry['id']}] {entry['title']} "
          f"状态={entry['status']} "
          f"夏普={entry['backtest_sharpe']} "
          f"最大回撤={entry['backtest_max_drawdown']:.1%}")
    print(f"    选股: {', '.join(entry['selected_stocks'])}")

    # 验证完整调用链
    assert len(bc.call_log) == 5
    methods = [c[0] for c in bc.call_log]
    assert methods == [
        "get_presets", "run_screener", "get_kline", "get_indicators", "get_ledger"
    ]
    print(f"\n  ✓ 全部 {len(bc.call_log)} 次 BridgeClient 调用验证通过")
    print("  PASS: 场景2 完成\n")


async def test_scenario_3_realtime_stream():
    """场景3: 实时数据流推送

    模拟 data_factory Celery Worker 发布数据 → RRAgent StreamConsumer 接收。
    """
    print("=" * 60)
    print("场景3: 实时数据流推送")
    print("  模拟: data_factory → Redis Pub/Sub → RRAgent StreamConsumer")
    print("=" * 60)

    from rragent.data_sources.reachrich_stream import (
        ReachRichStreamConsumer, ReachRichStreamConfig, StreamMessage,
        CHANNEL_QUOTES, CHANNEL_HOT, CHANNEL_CONCEPTS,
    )

    redis = MockRedis()
    config = ReachRichStreamConfig(token="", verify_hmac=False)
    consumer = ReachRichStreamConsumer(redis=redis, config=config)
    consumer._pubsub = redis.pubsub()

    received = []
    consumer.on_message(lambda msg: received.append(msg))

    # Monkey-patch for test
    async def _consume_once(self):
        msg = await self._pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
        if msg and msg["type"] == "message":
            channel = msg["channel"].decode() if isinstance(msg["channel"], bytes) else msg["channel"]
            await self._process_message(channel=channel, raw=msg["data"])
    consumer._consume_loop_once = lambda: _consume_once(consumer)

    # Step 1: 模拟 data_factory 发布行情快照
    print("\n[Step 1] data_factory 发布全市场行情到 reachrich:realtime:quotes...")
    quotes_msg = {
        "type": "quote_update",
        "data": [
            {"ts_code": "300456.SZ", "name": "赛微电子", "price": 42.50, "pct_chg": 20.0},
            {"ts_code": "002049.SZ", "name": "紫光国微", "price": 98.70, "pct_chg": 10.02},
            {"ts_code": "000001.SZ", "name": "平安银行", "price": 12.35, "pct_chg": 1.2},
        ],
        "count": 3,
        "update_time": "2026-04-10T14:30:45",
        "market_status": {"is_trading": True, "status": "continuous"},
    }
    redis._pubsub.inject(CHANNEL_QUOTES, json.dumps(quotes_msg))
    await consumer._consume_loop_once()

    assert len(received) == 1
    msg = received[0]
    assert msg.channel == CHANNEL_QUOTES
    assert msg.data["type"] == "quote_update"
    assert len(msg.data["data"]) == 3
    assert msg.verified is False
    print(f"  ✓ 收到行情: {msg.data['count']} 只股票")
    for s in msg.data["data"]:
        print(f"    {s['ts_code']} {s['name']} ¥{s['price']} ({s['pct_chg']:+.1f}%)")

    # Step 2: 模拟发布热门股
    print("\n[Step 2] data_factory 发布热门股到 reachrich:realtime:hot...")
    hot_msg = {
        "type": "hot_update",
        "data": [
            {"ts_code": "300456.SZ", "name": "赛微电子", "pct_chg": 20.0, "amount": 15_0000_0000},
            {"ts_code": "002049.SZ", "name": "紫光国微", "pct_chg": 10.02, "amount": 32_0000_0000},
        ],
        "update_time": "2026-04-10T14:30:45",
    }
    redis._pubsub.inject(CHANNEL_HOT, json.dumps(hot_msg))
    await consumer._consume_loop_once()

    assert len(received) == 2
    msg = received[1]
    assert msg.channel == CHANNEL_HOT
    assert msg.data["type"] == "hot_update"
    print(f"  ✓ 收到热门股: {len(msg.data['data'])} 只")
    for s in msg.data["data"]:
        print(f"    {s['ts_code']} {s['name']} {s['pct_chg']:+.1f}% 成交{s['amount']/1e8:.1f}亿")

    # Step 3: 模拟发布概念板块
    print("\n[Step 3] data_factory 发布概念板块到 reachrich:realtime:concepts...")
    concepts_msg = {
        "type": "concept_update",
        "data": [
            {"name": "半导体", "change_pct": 4.52, "stock_count": 85},
            {"name": "人工智能", "change_pct": 3.21, "stock_count": 120},
        ],
        "update_time": "2026-04-10T14:30:45",
    }
    redis._pubsub.inject(CHANNEL_CONCEPTS, json.dumps(concepts_msg))
    await consumer._consume_loop_once()

    assert len(received) == 3
    msg = received[2]
    assert msg.channel == CHANNEL_CONCEPTS
    print(f"  ✓ 收到板块: {len(msg.data['data'])} 个概念板块")

    stats = consumer.get_stats()
    print(f"\n  ✓ 消费统计: 总接收={stats['received']}, "
          f"未签名={stats['unsigned']}, 错误={stats['errors']}")

    assert stats["received"] == 3
    assert stats["unsigned"] == 3
    assert stats["errors"] == 0
    print("  PASS: 场景3 完成\n")


async def test_scenario_4_mcp_protocol():
    """场景4: 完整 MCP JSON-RPC 协议测试

    模拟 MCP 客户端（如 Claude Desktop）通过标准协议调用 RRAgent 工具。
    """
    print("=" * 60)
    print("场景4: MCP JSON-RPC 协议")
    print("  模拟: Claude Desktop → MCP JSON-RPC → RRAgent MCP Server")
    print("=" * 60)

    from rragent.tools.mcp.reachrich_server import ReachRichMCPServer

    server = ReachRichMCPServer()
    server._bridge_client = MockBridgeClient()

    # Step 1: initialize
    print("\n[Step 1] MCP initialize...")
    resp = await server.handle_request({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05"},
    })
    assert resp["result"]["serverInfo"]["name"] == "rragent-market"
    print(f"  ✓ Server: {resp['result']['serverInfo']['name']} "
          f"v{resp['result']['serverInfo']['version']}")

    # Step 2: tools/list
    print("\n[Step 2] MCP tools/list...")
    resp = await server.handle_request({
        "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
    })
    tools = resp["result"]["tools"]
    assert len(tools) == 11
    print(f"  ✓ 可用工具 ({len(tools)} 个):")
    for t in tools:
        params = list(t["inputSchema"].get("properties", {}).keys())
        param_str = f"({', '.join(params)})" if params else "()"
        print(f"    {t['name']}{param_str} — {t['description'][:40]}")

    # Step 3: tools/call market_screener
    print("\n[Step 3] MCP tools/call market_screener...")
    resp = await server.handle_request({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {
            "name": "market_screener",
            "arguments": {
                "payload": {
                    "conditions": [
                        {"field": "concept", "operator": "contains", "value": "半导体"},
                        {"field": "pct_chg", "operator": ">", "value": 5},
                    ],
                },
                "limit": 10,
            },
        },
    })
    content = resp["result"]["content"][0]["text"]
    data = json.loads(content)
    assert data["status"] == "ok"
    assert data["result_count"] == 5
    print(f"  ✓ 选股结果: {data['result_count']} 只, "
          f"ID={data['screener_id']}")
    print(f"    第一名: {data['data'][0]['name']} 评分 {data['data'][0]['score']}")

    # Step 4: tools/call unknown tool
    print("\n[Step 4] MCP tools/call unknown tool (错误处理)...")
    resp = await server.handle_request({
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "nonexistent_tool", "arguments": {}},
    })
    assert resp["result"]["isError"] is True
    print(f"  ✓ 正确返回错误: {resp['result']['content'][0]['text']}")

    print("\n  PASS: 场景4 完成\n")


async def test_scenario_5_full_user_session():
    """场景5: 完整用户会话模拟

    模拟用户从进入到选股的完整对话:
      1. 先看大盘 → 发现半导体强势
      2. 查看策略列表 → 选择半导体动量
      3. 运行选股 → 得到 5 只票
      4. 查看排名第一的 K 线和指标
      5. 同时实时流推送更新数据
    """
    print("=" * 60)
    print("场景5: 完整用户会话（行情 + 策略选股 + 实时流）")
    print("  用户: 看看大盘，然后用半导体策略选几只票")
    print("=" * 60)

    from rragent.tools.mcp.reachrich_server import ReachRichMCPServer
    from rragent.data_sources.reachrich_stream import (
        ReachRichStreamConsumer, ReachRichStreamConfig,
        CHANNEL_QUOTES,
    )

    # 初始化 MCP Server
    server = ReachRichMCPServer()
    server._bridge_client = MockBridgeClient()

    # 初始化实时流
    redis = MockRedis()
    stream_config = ReachRichStreamConfig(token="", verify_hmac=False)
    stream = ReachRichStreamConsumer(redis=redis, config=stream_config)
    stream._pubsub = redis.pubsub()
    stream_msgs = []
    stream.on_message(lambda msg: stream_msgs.append(msg))

    async def consume_once():
        msg = await stream._pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
        if msg and msg["type"] == "message":
            ch = msg["channel"].decode() if isinstance(msg["channel"], bytes) else msg["channel"]
            await stream._process_message(channel=ch, raw=msg["data"])

    # === 对话开始 ===

    # Turn 1: 看大盘
    print("\n[Turn 1] 用户: 今天大盘怎么样？")
    r = await server._call_tool("market_snapshot", {})
    snap = json.loads(r["content"][0]["text"])
    print(f"  RRAgent: 大盘偏多，{snap['up_count']}涨/{snap['down_count']}跌，"
          f"涨停{snap['limit_up']}只")

    r = await server._call_tool("market_concepts", {"limit": 5})
    concepts = json.loads(r["content"][0]["text"])
    top = concepts["data"][0]
    print(f"  RRAgent: 板块龙头是{top['name']}(+{top['change_pct']}%)，"
          f"涨停{top['limit_up_count']}只")

    # 同时收到实时推送
    redis._pubsub.inject(CHANNEL_QUOTES, json.dumps({
        "type": "quote_update",
        "data": [{"ts_code": "300456.SZ", "price": 42.80, "pct_chg": 20.7}],
        "update_time": "2026-04-10T14:31:00",
    }))
    await consume_once()
    assert len(stream_msgs) == 1
    print(f"  [实时流] 收到行情推送: {stream_msgs[-1].data['data'][0]['ts_code']} "
          f"¥{stream_msgs[-1].data['data'][0]['price']}")

    # Turn 2: 查看策略并选股
    print(f"\n[Turn 2] 用户: 用半导体策略帮我选股")
    r = await server._call_tool("market_presets", {})
    presets = json.loads(r["content"][0]["text"])["presets"]
    semi_preset = next(p for p in presets if "半导体" in p["name"])
    print(f"  RRAgent: 找到策略「{semi_preset['name']}」，正在运行...")

    r = await server._call_tool("market_screener", {
        "payload": {"preset_id": semi_preset["id"], "conditions": semi_preset["conditions"]},
        "limit": 10,
    })
    picks = json.loads(r["content"][0]["text"])
    print(f"  RRAgent: 选出 {picks['result_count']} 只:")
    for i, s in enumerate(picks["data"][:3], 1):
        print(f"    {i}. {s['name']}({s['ts_code']}) "
              f"¥{s['close']} {s['pct_chg']:+.1f}% 评分{s['score']}")

    # 实时流继续推送
    redis._pubsub.inject(CHANNEL_QUOTES, json.dumps({
        "type": "quote_update",
        "data": [{"ts_code": "300456.SZ", "price": 42.90, "pct_chg": 20.9}],
        "update_time": "2026-04-10T14:32:00",
    }))
    await consume_once()
    print(f"  [实时流] 推送: 赛微电子 ¥{stream_msgs[-1].data['data'][0]['price']} "
          f"(+{stream_msgs[-1].data['data'][0]['pct_chg']}%)")

    # Turn 3: 分析排名第一
    top_pick = picks["data"][0]
    print(f"\n[Turn 3] 用户: 详细分析一下{top_pick['name']}")
    r = await server._call_tool("market_kline", {
        "ts_code": top_pick["ts_code"], "period": "daily", "limit": 5,
    })
    kline = json.loads(r["content"][0]["text"])

    r = await server._call_tool("market_indicators", {
        "ts_code": top_pick["ts_code"],
    })
    ind = json.loads(r["content"][0]["text"])["data"]

    print(f"  RRAgent: {top_pick['name']}分析:")
    print(f"    K线: 近5日连涨，今日涨停")
    print(f"    均线: MA5({ind['ma5']}) > MA20({ind['ma20']}) 多头排列")
    print(f"    RSI14={ind['rsi_14']} 偏高但未超买")
    print(f"    MACD 金叉确认, 柱状线={ind['macd_hist']}")
    print(f"    量比={ind['vol_ratio']} 放量明显")

    # 最终统计
    total_calls = len(server._bridge_client.call_log)
    total_stream = len(stream_msgs)
    stream_stats = stream.get_stats()

    print(f"\n{'=' * 60}")
    print(f"会话统计:")
    print(f"  Bridge API 调用: {total_calls} 次")
    print(f"  实时流消息: {total_stream} 条")
    print(f"  流消费统计: {stream_stats}")
    print(f"  调用方法: {[c[0] for c in server._bridge_client.call_log]}")
    print(f"{'=' * 60}")

    assert total_calls == 6  # snapshot, concepts, presets, screener, kline, indicators
    assert total_stream == 2
    assert stream_stats["received"] == 2
    assert stream_stats["errors"] == 0

    print("  PASS: 场景5 完成\n")


# ── 主入口 ──

async def run_all():
    passed = 0
    failed = 0
    scenarios = [
        ("场景1: 实时行情查询", test_scenario_1_realtime_market),
        ("场景2: 策略选股", test_scenario_2_strategy_screening),
        ("场景3: 实时数据流", test_scenario_3_realtime_stream),
        ("场景4: MCP 协议", test_scenario_4_mcp_protocol),
        ("场景5: 完整用户会话", test_scenario_5_full_user_session),
    ]

    for name, test_fn in scenarios:
        try:
            await test_fn()
            passed += 1
        except Exception as e:
            print(f"  FAIL: {name} — {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print("\n" + "=" * 60)
    print(f"总结: {passed}/{len(scenarios)} 通过, {failed}/{len(scenarios)} 失败")
    print("=" * 60)
    return failed


if __name__ == "__main__":
    failed = asyncio.run(run_all())
    sys.exit(failed)

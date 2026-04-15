"""
Unified market query tool — Tier 0 entry point for A-share market data.

Combines zt/lb/bk/hot/summary into one tool to save prompt tokens.
Routes to PyAgent market commands via Redis.
"""

from __future__ import annotations

import json
from typing import Any

from rragent.tools.base import Tool, ToolSpec, ToolResult


class MarketQueryTool(Tool):
    """Unified market data query tool (Tier 0)."""

    spec = ToolSpec(
        name="market_query",
        description=(
            "查询A股市场数据。支持类型: "
            "limitup(涨停板), limitstep(连板), concepts(板块), "
            "hot(热门股), summary(市场总结), kline(K线), indicators(技术指标)"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["limitup", "limitstep", "concepts", "hot", "summary",
                             "kline", "indicators", "sentiment"],
                    "description": "查询类型",
                },
                "params": {
                    "type": "object",
                    "description": "查询参数 (如 code, period, limit 等)",
                    "default": {},
                },
            },
            "required": ["type"],
        },
        is_tier0=True,
        should_defer=False,
        is_concurrent_safe=True,
        timeout=30,
        category="market",
        keywords=["涨停", "连板", "板块", "热门", "市场", "K线", "行情"],
    )

    # Map query types to PyAgent commands
    TYPE_MAP = {
        "limitup": ("market", "get_limitup"),
        "limitstep": ("market", "get_limitstep"),
        "concepts": ("market", "get_concepts"),
        "hot": ("market", "get_hot"),
        "summary": ("market", "get_summary"),
        "kline": ("market", "get_kline"),
        "indicators": ("market", "get_indicators"),
        "sentiment": ("market", "get_sentiment"),
    }

    def __init__(self, bridge):
        self.bridge = bridge

    async def call(self, input: dict[str, Any]) -> ToolResult:
        query_type = input.get("type", "")
        params = input.get("params", {})

        if query_type not in self.TYPE_MAP:
            return ToolResult.error(
                f"Unknown query type: {query_type}. "
                f"Supported: {', '.join(self.TYPE_MAP.keys())}"
            )

        agent, action = self.TYPE_MAP[query_type]

        try:
            result = await self.bridge.call_agent(
                agent=agent,
                action=action,
                params=params,
                timeout=self.spec.timeout,
            )

            if isinstance(result, dict) and result.get("error"):
                return ToolResult.error(str(result["error"]))

            content = json.dumps(result, ensure_ascii=False, indent=2)
            return ToolResult.success(content)

        except Exception as e:
            return ToolResult.error(f"Market query failed ({query_type}): {e}")

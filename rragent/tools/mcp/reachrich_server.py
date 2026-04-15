"""
ReachRich MCP Server — expose A-share market data via MCP protocol.

Only wraps fast query operations suitable for MCP (request-response).
Long-running operations (backtest, factor mining) stay as native RRAgent tools.
Real-time streaming uses Redis Pub/Sub subscription (see data_sources/).

Tool schemas match BridgeClient's actual method signatures:
  - get_limitup(trade_date="")
  - get_concepts(limit=50)
  - get_kline(ts_code, period, start_date, end_date, limit, fmt)
  - get_indicators(ts_code, limit)
  - get_sentiment(limit=20)
  - get_dragon_tiger(trade_date="")
  - get_snapshot()

Usage:
    python -m rragent.tools.mcp.reachrich_server
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Any

logger = logging.getLogger("rragent.tools.mcp.reachrich")


class ReachRichMCPServer:
    """
    MCP Server for ReachRich A-share market data.

    Wraps BridgeClient HTTP+HMAC calls for fast market queries.
    All method signatures and parameter names match the actual
    BridgeClient API (rragent-brain/agents/bridge_client.py).

    NOT exposed (too slow for MCP):
    - backtest (up to 300s)
    - factor_mining (up to 620s)
    - alpha_signal (async polling)
    - intraday_scan (DolphinDB, uses Redis Pub/Sub)
    """

    TOOLS = [
        {
            "name": "market_snapshot",
            "description": "Full market snapshot — all stocks current price/volume/change",
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
        },
        {
            "name": "market_limitup",
            "description": "Get today's limit-up (涨停) stocks with details",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "trade_date": {
                        "type": "string",
                        "default": "",
                        "description": "Trade date YYYYMMDD (empty = today)",
                    },
                },
            },
        },
        {
            "name": "market_concepts",
            "description": "Get sector/concept (板块) performance and rankings",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "default": 50,
                        "description": "Max number of concepts to return",
                    },
                },
            },
        },
        {
            "name": "market_kline",
            "description": "Get K-line (candlestick) data for a stock",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "ts_code": {
                        "type": "string",
                        "description": "Stock code (e.g., 000001.SZ)",
                    },
                    "period": {
                        "type": "string",
                        "default": "daily",
                        "enum": ["1min", "5min", "15min", "30min", "60min", "daily", "weekly"],
                        "description": "K-line period",
                    },
                    "start_date": {
                        "type": "string",
                        "default": "",
                        "description": "Start date YYYY-MM-DD (empty = auto)",
                    },
                    "end_date": {
                        "type": "string",
                        "default": "",
                        "description": "End date YYYY-MM-DD (empty = today)",
                    },
                    "limit": {
                        "type": "integer",
                        "default": 250,
                        "description": "Max number of bars to return",
                    },
                },
                "required": ["ts_code"],
            },
        },
        {
            "name": "market_indicators",
            "description": "Get technical indicators (MA, RSI, MACD, KDJ, BOLL) for a stock",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "ts_code": {
                        "type": "string",
                        "description": "Stock code (e.g., 000001.SZ)",
                    },
                    "limit": {
                        "type": "integer",
                        "default": 60,
                        "description": "Number of recent bars for indicator calculation",
                    },
                },
                "required": ["ts_code"],
            },
        },
        {
            "name": "market_sentiment",
            "description": "Get market sentiment summary (涨跌比, 涨停/跌停数, 连板等)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "default": 20,
                        "description": "Number of sentiment records",
                    },
                },
            },
        },
        {
            "name": "market_dragon_tiger",
            "description": "Get dragon-tiger board (龙虎榜) data — institutional trading",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "trade_date": {
                        "type": "string",
                        "default": "",
                        "description": "Trade date YYYYMMDD (empty = today)",
                    },
                },
            },
        },
        {
            "name": "market_presets",
            "description": "List registered strategy presets for the screener",
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
        },
        {
            "name": "market_screener",
            "description": "Execute DSL stock screening with custom conditions",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "payload": {
                        "type": "object",
                        "description": "Screener DSL payload (conditions, universe, etc.)",
                    },
                    "limit": {
                        "type": "integer",
                        "default": 50,
                        "description": "Max number of results",
                    },
                },
                "required": ["payload"],
            },
        },
        {
            "name": "market_ledger",
            "description": "List decision ledger entries (策略决策台账)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "default": "",
                        "description": "Filter by status (empty = all)",
                    },
                    "page": {
                        "type": "integer",
                        "default": 1,
                        "description": "Page number",
                    },
                },
            },
        },
        {
            "name": "market_system_schema",
            "description": "Get database schema metadata (ClickHouse/DolphinDB tables + API list)",
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
        },
    ]

    def __init__(self, config: dict | None = None):
        self._bridge_client: Any = None
        self._request_id = 0
        self._config = config or {}

    async def initialize(self):
        """Initialize BridgeClient with configuration.

        BridgeClient supports dual auth: token (user) or secret (internal).
        RRAgent users only need REACHRICH_TOKEN — no server secret required.

        Config sources (highest priority last):
          1. BridgeClient defaults (env vars BRIDGE_BASE_URL / REACHRICH_TOKEN)
          2. RRAgent config (reachrich.base_url / reachrich.token)
          3. Environment overrides (REACHRICH_URL / REACHRICH_TOKEN)
        """
        try:
            bridge_client_path = (
                self._config.get("bridge_client_path")
                or os.getenv("BRIDGE_CLIENT_PATH", "")
            )
            if bridge_client_path:
                sys.path.insert(0, bridge_client_path)

            from bridge_client import BridgeClient

            # Build constructor kwargs — only pass non-empty values
            # so BridgeClient's own env-var defaults still work as fallback.
            kwargs: dict[str, str] = {}
            base_url = self._config.get("base_url") or os.getenv("REACHRICH_URL", "")
            token = self._config.get("token") or os.getenv("REACHRICH_TOKEN", "")
            if base_url:
                kwargs["base_url"] = base_url
            if token:
                kwargs["token"] = token

            self._bridge_client = BridgeClient(**kwargs)
            logger.info(
                "ReachRich BridgeClient initialized (url=%s, auth=%s)",
                base_url or "<BridgeClient default>",
                "token" if token else "secret",
            )
        except ImportError:
            logger.warning(
                "BridgeClient not available. Set BRIDGE_CLIENT_PATH env var "
                "or config reachrich.bridge_client_path to the directory "
                "containing bridge_client.py"
            )

    async def handle_request(self, request: dict) -> dict | None:
        """Handle a single MCP JSON-RPC request."""
        method = request.get("method", "")
        req_id = request.get("id")
        params = request.get("params", {})

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "rragent-market", "version": "0.2.0"},
                },
            }

        elif method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": self.TOOLS},
            }

        elif method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            result = await self._call_tool(tool_name, arguments)
            return {"jsonrpc": "2.0", "id": req_id, "result": result}

        elif method == "notifications/initialized":
            return None

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }

    async def _call_tool(self, name: str, args: dict) -> dict:
        """Route tool call to BridgeClient.

        Method signatures match BridgeClient exactly.
        BridgeClient methods are async (httpx.AsyncClient), called directly.
        """
        if not self._bridge_client:
            return {
                "content": [{"type": "text", "text": "BridgeClient not available"}],
                "isError": True,
            }

        try:
            bc = self._bridge_client

            # Dispatch — each entry matches BridgeClient's actual method signature
            if name == "market_snapshot":
                result = await bc.get_snapshot()

            elif name == "market_limitup":
                result = await bc.get_limitup(
                    trade_date=args.get("trade_date", ""),
                )

            elif name == "market_concepts":
                result = await bc.get_concepts(
                    limit=args.get("limit", 50),
                )

            elif name == "market_kline":
                result = await bc.get_kline(
                    ts_code=args.get("ts_code", ""),
                    period=args.get("period", "daily"),
                    start_date=args.get("start_date", ""),
                    end_date=args.get("end_date", ""),
                    limit=args.get("limit", 250),
                )

            elif name == "market_indicators":
                result = await bc.get_indicators(
                    ts_code=args.get("ts_code", ""),
                    limit=args.get("limit", 60),
                )

            elif name == "market_sentiment":
                result = await bc.get_sentiment(
                    limit=args.get("limit", 20),
                )

            elif name == "market_dragon_tiger":
                result = await bc.get_dragon_tiger(
                    trade_date=args.get("trade_date", ""),
                )

            elif name == "market_presets":
                result = await bc.get_presets()

            elif name == "market_screener":
                result = await bc.run_screener(
                    payload=args.get("payload", {}),
                    limit=args.get("limit", 50),
                )

            elif name == "market_ledger":
                result = await bc.get_ledger(
                    status=args.get("status", ""),
                    page=args.get("page", 1),
                )

            elif name == "market_system_schema":
                result = await bc.get_system_schema()

            else:
                return {
                    "content": [{"type": "text", "text": f"Unknown tool: {name}"}],
                    "isError": True,
                }

            text = (
                json.dumps(result, ensure_ascii=False, indent=2)
                if isinstance(result, (dict, list))
                else str(result)
            )
            return {"content": [{"type": "text", "text": text}]}

        except Exception as e:
            return {
                "content": [{"type": "text", "text": f"Error: {e}"}],
                "isError": True,
            }

    async def run_stdio(self):
        """Run as stdio MCP server."""
        await self.initialize()

        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, sys.stdin)

        writer_transport, writer_protocol = await asyncio.get_event_loop().connect_write_pipe(
            asyncio.streams.FlowControlMixin, sys.stdout
        )
        writer = asyncio.StreamWriter(writer_transport, writer_protocol, reader, asyncio.get_event_loop())

        while True:
            try:
                line = await reader.readline()
                if not line:
                    break

                request = json.loads(line.decode())
                response = await self.handle_request(request)

                if response:
                    writer.write((json.dumps(response) + "\n").encode())
                    await writer.drain()
            except Exception as e:
                logger.error(f"ReachRich MCP error: {e}")
                break


async def main():
    from rragent.runtime.config import load_config
    config = load_config()
    server = ReachRichMCPServer(config=config.get("reachrich", {}))
    await server.run_stdio()


if __name__ == "__main__":
    asyncio.run(main())

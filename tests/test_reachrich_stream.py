"""
ReachRich real-time data stream end-to-end test.

Simulates the full flow:
  ReachRich data_factory/FastAPI → Redis PUBLISH → RRAgent StreamConsumer → handler

Tests:
  1. Unsigned messages (data_factory default format) — should be accepted
  2. Signed messages (BridgePublisher HMAC envelope) — should be verified and accepted
  3. Bad signature — should be rejected
  4. Stale messages — should be rejected
  5. MCP server tool schema alignment with BridgeClient
  6. Config and env var plumbing
"""

import asyncio
import hashlib
import hmac
import json
import time
try:
    import pytest
    _asyncio_mark = pytest.mark.asyncio
except ImportError:
    pytest = None
    _asyncio_mark = lambda f: f  # no-op decorator when pytest unavailable

from rragent.data_sources.reachrich_stream import (
    ReachRichStreamConsumer,
    ReachRichStreamConfig,
    ReachRichPublisher,
    StreamMessage,
    CHANNEL_QUOTES,
    CHANNEL_HOT,
)


# ── Mock Redis Pub/Sub ──

class MockPubSub:
    """Simulates redis.asyncio pubsub interface."""

    def __init__(self):
        self._messages: list[dict] = []
        self._subscribed: list[str] = []

    async def subscribe(self, *channels):
        self._subscribed.extend(channels)

    async def unsubscribe(self, *channels):
        for c in channels:
            if c in self._subscribed:
                self._subscribed.remove(c)

    async def close(self):
        pass

    def inject_message(self, channel: str, data):
        """Inject a message as if Redis published it."""
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
    """Simulates redis.asyncio Redis interface for Pub/Sub."""

    def __init__(self):
        self._pubsub = MockPubSub()
        self._published: list[tuple[str, str]] = []

    def pubsub(self):
        return self._pubsub

    async def publish(self, channel, data):
        self._published.append((channel, data))
        return 1  # 1 subscriber


# ── Tests ──

@_asyncio_mark
async def test_unsigned_message():
    """DataCollector publishes unsigned JSON — consumer should accept it."""
    redis = MockRedis()
    config = ReachRichStreamConfig(token="", verify_hmac=False)
    consumer = make_test_consumer(redis, config)

    received: list[StreamMessage] = []
    consumer.on_message(lambda msg: received.append(msg))

    # Simulate DataCollector's exact output format
    dc_message = {
        "type": "quote_update",
        "data": [
            {"ts_code": "000001.SZ", "price": 10.5, "pct_chg": 2.3},
            {"ts_code": "600519.SH", "price": 1800.0, "pct_chg": -0.5},
        ],
        "count": 2,
        "update_time": "2025-01-01T09:30:00",
        "duration": 0.15,
        "market_status": {"is_trading": True, "status": "trading"},
    }

    redis._pubsub.inject_message(CHANNEL_QUOTES, json.dumps(dc_message))

    # Process one message
    await consumer._consume_loop_once()

    assert len(received) == 1
    msg = received[0]
    assert msg.channel == CHANNEL_QUOTES
    assert msg.data["type"] == "quote_update"
    assert len(msg.data["data"]) == 2
    assert msg.verified is False  # Unsigned

    stats = consumer.get_stats()
    assert stats["received"] == 1
    assert stats["unsigned"] == 1
    print("PASS: Unsigned DataCollector message accepted")


@_asyncio_mark
async def test_signed_message():
    """Publisher sends HMAC-signed envelope — consumer should verify and accept."""
    token = "user_token_abc123"
    redis = MockRedis()
    config = ReachRichStreamConfig(token=token, verify_hmac=True)
    consumer = make_test_consumer(redis, config)

    received: list[StreamMessage] = []
    consumer.on_message(lambda msg: received.append(msg))

    # Create signed envelope (same as ReachRichPublisher would produce)
    payload = {"ts_code": "000001.SZ", "price": 10.5}
    payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    sig = hmac.new(token.encode(), payload_json.encode(), hashlib.sha256).hexdigest()
    envelope = json.dumps({
        "data": payload_json,
        "_sig": sig,
        "_ts": str(time.time()),
    })

    redis._pubsub.inject_message(CHANNEL_QUOTES, envelope)
    await consumer._consume_loop_once()

    assert len(received) == 1
    assert received[0].verified is True
    assert received[0].data["ts_code"] == "000001.SZ"

    stats = consumer.get_stats()
    assert stats["verified"] == 1
    print("PASS: Signed message verified and accepted")


@_asyncio_mark
async def test_bad_signature():
    """Bad HMAC signature — consumer should reject."""
    redis = MockRedis()
    config = ReachRichStreamConfig(token="correct_token", verify_hmac=True)
    consumer = make_test_consumer(redis, config)

    received: list[StreamMessage] = []
    consumer.on_message(lambda msg: received.append(msg))

    payload_json = '{"price": 10.5}'
    bad_sig = hmac.new(b"wrong_token", payload_json.encode(), hashlib.sha256).hexdigest()
    envelope = json.dumps({
        "data": payload_json,
        "_sig": bad_sig,
        "_ts": str(time.time()),
    })

    redis._pubsub.inject_message(CHANNEL_QUOTES, envelope)
    await consumer._consume_loop_once()

    assert len(received) == 0  # Rejected
    assert consumer.get_stats()["rejected_sig"] == 1
    print("PASS: Bad signature rejected")


@_asyncio_mark
async def test_stale_message():
    """Message older than max_message_age_s — should be rejected."""
    redis = MockRedis()
    config = ReachRichStreamConfig(
        token="tok", verify_hmac=True, max_message_age_s=5.0
    )
    consumer = make_test_consumer(redis, config)

    received: list[StreamMessage] = []
    consumer.on_message(lambda msg: received.append(msg))

    payload_json = '{"price": 10.5}'
    sig = hmac.new(b"tok", payload_json.encode(), hashlib.sha256).hexdigest()
    envelope = json.dumps({
        "data": payload_json,
        "_sig": sig,
        "_ts": str(time.time() - 60),  # 60 seconds ago
    })

    redis._pubsub.inject_message(CHANNEL_QUOTES, envelope)
    await consumer._consume_loop_once()

    assert len(received) == 0
    assert consumer.get_stats()["rejected_stale"] == 1
    print("PASS: Stale message rejected")


@_asyncio_mark
async def test_publisher_consumer_roundtrip():
    """Publisher signs, consumer verifies — full roundtrip."""
    token = "shared_token_xyz"
    redis = MockRedis()

    publisher = ReachRichPublisher(redis=redis, token=token)
    await publisher.publish(CHANNEL_QUOTES, {"code": "000001", "price": 10.5})

    # Publisher put a message in redis._published
    assert len(redis._published) == 1
    channel, envelope_str = redis._published[0]
    assert channel == CHANNEL_QUOTES

    # Now feed it to the consumer
    config = ReachRichStreamConfig(token=token, verify_hmac=True)
    consumer = make_test_consumer(redis, config)

    received: list[StreamMessage] = []
    consumer.on_message(lambda msg: received.append(msg))

    redis._pubsub.inject_message(CHANNEL_QUOTES, envelope_str)
    await consumer._consume_loop_once()

    assert len(received) == 1
    assert received[0].verified is True
    assert received[0].data["code"] == "000001"
    print("PASS: Publisher → Consumer roundtrip verified")


def test_mcp_tools_match_bridge_client():
    """MCP server tool schemas should match BridgeClient's actual method signatures."""
    from rragent.tools.mcp.reachrich_server import ReachRichMCPServer

    srv = ReachRichMCPServer()

    # BridgeClient actual methods and their parameters
    bridge_api = {
        "market_snapshot": ("get_snapshot", []),
        "market_limitup": ("get_limitup", ["trade_date"]),
        "market_concepts": ("get_concepts", ["limit"]),
        "market_kline": ("get_kline", ["ts_code", "period", "start_date", "end_date", "limit"]),
        "market_indicators": ("get_indicators", ["ts_code", "limit"]),
        "market_sentiment": ("get_sentiment", ["limit"]),
        "market_dragon_tiger": ("get_dragon_tiger", ["trade_date"]),
        "market_presets": ("get_presets", []),
        "market_screener": ("run_screener", ["payload", "limit"]),
        "market_ledger": ("get_ledger", ["status", "page"]),
        "market_system_schema": ("get_system_schema", []),
    }

    for tool in srv.TOOLS:
        name = tool["name"]
        assert name in bridge_api, f"MCP tool {name} has no BridgeClient mapping"
        method_name, expected_params = bridge_api[name]
        schema_params = set(tool["inputSchema"].get("properties", {}).keys())
        for p in expected_params:
            assert p in schema_params, f"{name}: missing param '{p}' (BridgeClient.{method_name} needs it)"

    # Check old wrong param names are gone
    kline = next(t for t in srv.TOOLS if t["name"] == "market_kline")
    assert "code" not in kline["inputSchema"]["properties"], "Should use ts_code, not code"
    assert "count" not in kline["inputSchema"]["properties"], "Should use limit, not count"

    limitup = next(t for t in srv.TOOLS if t["name"] == "market_limitup")
    assert "page_size" not in limitup["inputSchema"]["properties"], "Should use trade_date, not page_size"

    print(f"PASS: All {len(srv.TOOLS)} MCP tools aligned with BridgeClient")


def test_config_env_overrides():
    """Config should propagate all ReachRich fields via env vars."""
    import os

    os.environ["REACHRICH_URL"] = "http://192.168.1.100:8001/api/bridge"
    os.environ["REACHRICH_TOKEN"] = "42:abc123def456"
    os.environ["BRIDGE_CLIENT_PATH"] = "/opt/rragent/agents"

    try:
        from rragent.runtime.config import load_config
        cfg = load_config()

        assert cfg["reachrich"]["base_url"] == "http://192.168.1.100:8001/api/bridge"
        assert cfg["reachrich"]["token"] == "42:abc123def456"
        assert "secret" not in cfg["reachrich"], "secret should be removed from config"
        assert cfg["reachrich"]["bridge_client_path"] == "/opt/rragent/agents"
        assert cfg["reachrich"]["stream_verify_hmac"] is True
    finally:
        del os.environ["REACHRICH_URL"]
        del os.environ["REACHRICH_TOKEN"]
        del os.environ["BRIDGE_CLIENT_PATH"]

    print("PASS: All env var overrides working")


# ── Patches for testability ──

async def _consume_loop_once(self):
    """Process one message (for testing)."""
    msg = await self._pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
    if msg and msg["type"] == "message":
        channel = msg["channel"] if isinstance(msg["channel"], str) else msg["channel"].decode()
        await self._process_message(channel=channel, raw=msg["data"])

ReachRichStreamConsumer._consume_loop_once = _consume_loop_once


def make_test_consumer(redis, config):
    """Create consumer with _pubsub initialized (without starting the infinite loop)."""
    consumer = ReachRichStreamConsumer(redis=redis, config=config)
    consumer._pubsub = redis.pubsub()
    return consumer


def test_composite_token_parsing():
    """Composite token '{user_id}:{hmac}' should extract raw HMAC for stream."""
    cfg = {"token": "42:abc123def456", "stream_verify_hmac": True}
    stream_cfg = ReachRichStreamConfig.from_config(cfg)
    assert stream_cfg.token == "abc123def456", "Should extract raw HMAC from composite token"
    assert stream_cfg.verify_hmac is True

    # Plain HMAC (backward compatible)
    cfg2 = {"token": "abc123def456", "stream_verify_hmac": False}
    stream_cfg2 = ReachRichStreamConfig.from_config(cfg2)
    assert stream_cfg2.token == "abc123def456", "Plain token should pass through unchanged"
    assert stream_cfg2.verify_hmac is False

    # Empty token
    cfg3 = {}
    stream_cfg3 = ReachRichStreamConfig.from_config(cfg3)
    assert stream_cfg3.token == ""

    print("PASS: Composite token parsing correct")


if __name__ == "__main__":
    # Run synchronous tests
    test_mcp_tools_match_bridge_client()
    test_config_env_overrides()
    test_composite_token_parsing()

    # Run async tests
    async def run_async():
        await test_unsigned_message()
        await test_signed_message()
        await test_bad_signature()
        await test_stale_message()
        await test_publisher_consumer_roundtrip()

    asyncio.run(run_async())
    print("\n=== All ReachRich stream tests passed ===")

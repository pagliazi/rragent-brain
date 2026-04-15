"""
ReachRich real-time market data stream consumer.

Subscribes to Redis Pub/Sub channels published by ReachRich's data pipeline
(data_factory Celery workers + FastAPI BridgePublisher). Supports optional
HMAC signature verification for authenticated data streams.

ReachRich server-side channels:
  - reachrich:realtime:quotes  — full market quotes (≤60s updates via Celery Beat)
  - reachrich:realtime:hot     — hot stocks ranking updates
  - reachrich:realtime:concepts — concept board updates
  - reachrich:realtime:sentiment — market sentiment updates

Protocol alignment:
  ReachRich server uses Redis Pub/Sub (PUBLISH), not Redis Streams (XADD).
  This consumer matches that protocol via SUBSCRIBE.

Authentication model (two layers):
  Layer 1: Redis AUTH — connection-level password (configured in REDIS_URL)
  Layer 2: Message HMAC (optional) — if the publisher signs messages with the
           user's REACHRICH_TOKEN, this consumer verifies the _sig field.
           When stream_verify_hmac=False or no token configured, messages
           are accepted without signature verification (backward compatible
           with existing unsigned publishers).

Data flow:
    data_factory Celery Beat → Celery Worker → DataCollector
        → data_factory.redis_client.publish() / BridgePublisher.publish()
            → Redis PUBLISH reachrich:realtime:quotes
                → FastAPI SSE/WebSocket (browser clients)
                → RRAgent StreamConsumer (AI agent) — this module
                    → handlers (ConversationRuntime / Evolution Engine)

Publisher stack:
    - data_factory/redis_client.py   — unsigned orjson publish (default)
    - fastapi_backend/api/bridge_publisher.py — HMAC-signed envelope (optional)
    Both publish to the same Redis Pub/Sub channels.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable

logger = logging.getLogger("rragent.data_sources.reachrich_stream")

# Redis Pub/Sub channels (matching ReachRich FastAPI SSE/WebSocket)
CHANNEL_QUOTES = "reachrich:realtime:quotes"
CHANNEL_HOT = "reachrich:realtime:hot"
CHANNEL_CONCEPTS = "reachrich:realtime:concepts"
CHANNEL_SENTIMENT = "reachrich:realtime:sentiment"


@dataclass
class StreamMessage:
    """A message from the ReachRich real-time stream."""

    channel: str
    data: dict[str, Any] | list[Any]
    timestamp: float
    verified: bool = False  # True if HMAC signature was verified


@dataclass
class ReachRichStreamConfig:
    """Configuration for the real-time stream consumer."""

    token: str = ""                    # Raw HMAC key for stream signature verification
    verify_hmac: bool = True           # Verify message signatures (if token set)
    channels: list[str] = field(
        default_factory=lambda: [
            CHANNEL_QUOTES, CHANNEL_HOT,
            CHANNEL_CONCEPTS, CHANNEL_SENTIMENT,
        ]
    )
    max_message_age_s: float = 30.0    # Reject messages older than this (if ts present)

    @classmethod
    def from_config(cls, cfg: dict) -> "ReachRichStreamConfig":
        """Build from rragent config['reachrich'] section.

        Token may be composite format "{user_id}:{hmac_hex}" — extract raw
        HMAC part for stream signature verification.
        """
        raw_token = cfg.get("token", "")
        # Extract raw HMAC from composite token (stream signing uses raw HMAC only)
        if ":" in raw_token:
            raw_token = raw_token.split(":", 1)[1]
        return cls(
            token=raw_token,
            verify_hmac=cfg.get("stream_verify_hmac", True),
        )


class ReachRichStreamConsumer:
    """
    Consume real-time market data from Redis Pub/Sub.

    Matches ReachRich server's actual publishing protocol:
      - data_factory Celery Worker → redis_client.publish() → Redis Pub/Sub
      - FastAPI BridgePublisher → optional HMAC-signed envelope → Redis Pub/Sub
      - FastAPI SSE/WebSocket → SUBSCRIBE same channels for browser streaming

    Message formats:
      Unsigned (data_factory default, orjson):
        {"type": "quote_update", "data": [...], "count": N, "update_time": "...", ...}
      Signed (BridgePublisher envelope):
        {"data": <payload_json>, "_sig": <hmac_hex>, "_ts": <unix_ts>}
      Legacy pickle (django-redis compat):
        zlib-compressed pickle bytes
    """

    def __init__(
        self,
        redis: Any,
        config: ReachRichStreamConfig,
    ):
        self._redis = redis
        self._config = config
        self._running = False
        self._pubsub: Any = None
        self._handlers: list[Callable[[StreamMessage], Any]] = []
        self._stats = {
            "received": 0,
            "verified": 0,
            "unsigned": 0,
            "rejected_sig": 0,
            "rejected_stale": 0,
            "errors": 0,
        }

    def on_message(self, handler: Callable[[StreamMessage], Any]):
        """Register a handler for stream messages."""
        self._handlers.append(handler)

    async def start(self):
        """Start subscribing to configured channels."""
        if self._running:
            return

        self._pubsub = self._redis.pubsub()
        await self._pubsub.subscribe(*self._config.channels)
        self._running = True

        logger.info(
            "ReachRich stream consumer started (channels=%s, verify_hmac=%s, has_token=%s)",
            self._config.channels,
            self._config.verify_hmac,
            bool(self._config.token),
        )
        asyncio.create_task(self._consume_loop())

    async def stop(self):
        """Stop consuming."""
        self._running = False
        if self._pubsub:
            await self._pubsub.unsubscribe(*self._config.channels)
            await self._pubsub.close()
            self._pubsub = None
        logger.info("ReachRich stream consumer stopped (stats=%s)", self._stats)

    async def _consume_loop(self):
        """Main Pub/Sub consumption loop."""
        while self._running:
            try:
                msg = await self._pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=0.5
                )
                if msg and msg["type"] == "message":
                    await self._process_message(
                        channel=msg["channel"] if isinstance(msg["channel"], str)
                                else msg["channel"].decode(),
                        raw=msg["data"],
                    )
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._stats["errors"] += 1
                logger.error("Stream consume error: %s", e)
                await asyncio.sleep(1.0)

    async def _process_message(self, channel: str, raw: bytes):
        """Parse, optionally verify, and dispatch a Pub/Sub message."""
        self._stats["received"] += 1

        # Parse raw bytes — support JSON and pickle (matching ReachRich sse.py)
        data = self._parse_raw(raw)
        if data is None:
            self._stats["errors"] += 1
            return

        verified = False
        now = time.time()

        # Check for signed message format: {"data": ..., "_sig": ..., "_ts": ...}
        if isinstance(data, dict) and "_sig" in data:
            raw_payload = data.get("data", "")
            sig = data.get("_sig", "")
            ts_str = data.get("_ts", "0")

            # HMAC verification
            if self._config.verify_hmac and self._config.token:
                if not self._verify_signature(raw_payload, sig):
                    self._stats["rejected_sig"] += 1
                    logger.warning("Rejected signed message with invalid HMAC (channel=%s)", channel)
                    return
                verified = True

            # Staleness check
            try:
                msg_ts = float(ts_str)
                if now - msg_ts > self._config.max_message_age_s:
                    self._stats["rejected_stale"] += 1
                    return
            except (ValueError, TypeError):
                pass

            # Unwrap the inner payload
            if isinstance(raw_payload, str):
                try:
                    data = json.loads(raw_payload)
                except json.JSONDecodeError:
                    data = raw_payload
            else:
                data = raw_payload

            self._stats["verified"] += 1
        else:
            # Unsigned message (data_factory default format)
            self._stats["unsigned"] += 1

        msg = StreamMessage(
            channel=channel,
            data=data,
            timestamp=now,
            verified=verified,
        )

        for handler in self._handlers:
            try:
                result = handler(msg)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error("Stream handler error: %s", e)

    def _parse_raw(self, raw: bytes) -> Any:
        """Parse Redis Pub/Sub message bytes (JSON or pickle)."""
        # Try JSON first
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

        # Try pickle (data_factory uses django-redis compatible pickle format)
        try:
            import zlib
            try:
                raw = zlib.decompress(raw)
            except zlib.error:
                pass
            import pickle
            return pickle.loads(raw)
        except Exception:
            pass

        return None

    def _verify_signature(self, data_json: str, signature: str) -> bool:
        """Verify HMAC-SHA256 signature against user token."""
        if not signature or not self._config.token:
            return False
        if isinstance(data_json, bytes):
            data_json = data_json.decode()
        expected = hmac.new(
            self._config.token.encode(),
            data_json.encode(),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    def get_stats(self) -> dict[str, int]:
        """Return consumption statistics."""
        return dict(self._stats)


class ReachRichPublisher:
    """
    Publish HMAC-signed messages to Redis Pub/Sub.

    For use by PyAgent or any component that pushes real-time data.
    Messages are signed with the user's REACHRICH_TOKEN so the consumer
    can verify authenticity.

    Publish format:
        PUBLISH reachrich:realtime:quotes '{"data": <json>, "_sig": <hmac>, "_ts": <ts>}'
    """

    def __init__(self, redis: Any, token: str):
        self._redis = redis
        self._token = token

    async def publish(self, channel: str, data: dict | list) -> int:
        """
        Publish a signed message to a Redis Pub/Sub channel.

        Returns:
            Number of subscribers that received the message.
        """
        ts = time.time()
        data_json = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        sig = hmac.new(
            self._token.encode(),
            data_json.encode(),
            hashlib.sha256,
        ).hexdigest()

        envelope = json.dumps({
            "data": data_json,
            "_sig": sig,
            "_ts": str(ts),
        })
        return await self._redis.publish(channel, envelope)


async def iter_stream(
    redis: Any,
    config: ReachRichStreamConfig,
) -> AsyncGenerator[StreamMessage, None]:
    """
    Convenience async generator for consuming stream messages.

    Usage:
        async for msg in iter_stream(redis, config):
            print(msg.channel, msg.data)
    """
    consumer = ReachRichStreamConsumer(redis, config)
    queue: asyncio.Queue[StreamMessage] = asyncio.Queue(maxsize=1000)
    consumer.on_message(queue.put)
    await consumer.start()
    try:
        while True:
            msg = await queue.get()
            yield msg
    finally:
        await consumer.stop()

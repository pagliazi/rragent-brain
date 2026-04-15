"""
IM Gateway Channel — WebSocket connection using v3 protocol.

Refactored from bridge/gateway_client.py to work with ConversationRuntime.
Gateway is now a pure channel layer; RRAgent controls the LLM loop.

v3 protocol handshake:
1. Receive `connect.challenge` event with nonce
2. Send connect request with auth token and protocol version
3. Receive connect response confirming protocol 3
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Callable, Optional

import websockets
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger("rragent.channels.gateway")


class GatewayChannel:
    """
    WebSocket client for IM Gateway (v3 protocol).

    In the new architecture, Gateway only routes messages.
    RRAgent's ConversationRuntime handles all agent logic.
    """

    def __init__(
        self,
        gateway_url: str = "ws://127.0.0.1:18789",
        auth_token: str = "",
        agent_id: str = "rragent",
        on_user_message: Callable | None = None,
    ):
        self.gateway_url = gateway_url
        self.auth_token = auth_token
        self.agent_id = agent_id
        self.on_user_message = on_user_message
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._connected = asyncio.Event()
        self._reconnect_delay = 5.0
        self._max_reconnect_delay = 60.0
        self._should_run = True
        self._req_counter = 0

    def _next_req_id(self) -> str:
        self._req_counter += 1
        return f"req-{self._req_counter}"

    async def connect(self):
        """Connect to Gateway and perform v3 handshake."""
        try:
            self._ws = await websockets.connect(
                self.gateway_url,
                ping_interval=20,
                ping_timeout=10,
            )

            # Step 1: Wait for connect.challenge
            raw = await asyncio.wait_for(self._ws.recv(), timeout=10)
            challenge = json.loads(raw)
            if challenge.get("type") == "event" and challenge.get("event") == "connect.challenge":
                logger.debug(f"Received connect.challenge, nonce={challenge.get('payload', {}).get('nonce', '?')}")
            else:
                logger.warning(f"Expected connect.challenge, got: {challenge}")

            # Step 2: Send connect request (v3)
            req_id = self._next_req_id()
            await self._send({
                "type": "req",
                "id": req_id,
                "method": "connect",
                "params": {
                    "auth": {
                        "token": self.auth_token,
                    },
                    "minProtocol": 3,
                    "maxProtocol": 3,
                    "client": {
                        "id": "gateway-client",
                        "version": "0.1.0",
                        "platform": "python",
                        "mode": "backend",
                    },
                },
            })

            # Step 3: Wait for connect response
            raw = await asyncio.wait_for(self._ws.recv(), timeout=10)
            res = json.loads(raw)
            if res.get("type") == "res" and res.get("ok"):
                protocol = res.get("payload", {}).get("protocol", "?")
                logger.info(f"Gateway v3 handshake OK (protocol={protocol})")
            else:
                error = res.get("error", res)
                logger.error(f"Gateway handshake failed: {error}")
                raise ConnectionError(f"Gateway handshake failed: {error}")

            self._connected.set()
            self._reconnect_delay = 5.0
            logger.info(f"Connected to Gateway: {self.gateway_url}")

        except Exception as e:
            logger.error(f"Gateway connection failed: {e}")
            raise

    async def listen(self):
        """Main listen loop with auto-reconnect."""
        while self._should_run:
            try:
                if not self._connected.is_set():
                    await self.connect()

                await self._connected.wait()
                async for raw in self._ws:
                    try:
                        frame = json.loads(raw)
                        await self._dispatch(frame)
                    except json.JSONDecodeError:
                        logger.warning(f"Invalid JSON from Gateway: {raw[:200]}")
                    except Exception as e:
                        logger.error(f"Error processing frame: {e}")

            except ConnectionClosed as e:
                logger.warning(f"Gateway disconnected: {e}")
                self._connected.clear()
            except Exception as e:
                logger.error(f"Gateway error: {e}")
                self._connected.clear()

            if self._should_run:
                logger.info(f"Reconnecting in {self._reconnect_delay}s...")
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(
                    self._reconnect_delay * 2, self._max_reconnect_delay
                )

    async def _dispatch(self, frame: dict):
        """Route incoming Gateway v3 frames to handlers."""
        frame_type = frame.get("type", "")

        if frame_type == "event":
            event_name = frame.get("event", "")
            payload = frame.get("payload", {})

            if event_name == "chat.send" or event_name == "chat":
                # Incoming user message
                if self.on_user_message:
                    session_id = payload.get("sessionId", payload.get("session_id", ""))
                    text = payload.get("text", payload.get("content", payload.get("prompt", "")))
                    await self.on_user_message(
                        session_id=session_id,
                        text=text,
                    )

            elif event_name in ("connect.challenge", "heartbeat", "pong"):
                pass  # internal

            else:
                logger.debug(f"Unhandled event: {event_name}")

        elif frame_type == "req":
            # Server-initiated request — respond if needed
            method = frame.get("method", "")
            req_id = frame.get("id", "")

            if method == "chat.send":
                payload = frame.get("params", {})
                if self.on_user_message:
                    session_id = payload.get("sessionId", payload.get("session_id", ""))
                    text = payload.get("text", payload.get("content", payload.get("prompt", "")))
                    await self.on_user_message(
                        session_id=session_id,
                        text=text,
                    )
                # Acknowledge
                await self._send({
                    "type": "res",
                    "id": req_id,
                    "ok": True,
                    "payload": {},
                })

            else:
                logger.debug(f"Unhandled request method: {method}")

        elif frame_type == "res":
            # Response to our request — currently ignored (fire-and-forget)
            pass

        else:
            logger.debug(f"Unhandled frame type: {frame_type}")

    # ── Outbound methods (v3 protocol) ──

    async def send_text_delta(self, session_id: str, delta: str):
        """Send a streaming text delta to the session."""
        await self._connected.wait()
        await self._send({
            "type": "event",
            "event": "session.message",
            "payload": {
                "sessionId": session_id,
                "text": delta,
                "streaming": True,
            },
        })

    async def send_text_complete(self, session_id: str, text: str):
        """Send a complete text response to the session."""
        await self._connected.wait()
        await self._send({
            "type": "event",
            "event": "session.message",
            "payload": {
                "sessionId": session_id,
                "text": text,
                "streaming": False,
                "done": True,
            },
        })

    async def send_stream_end(self, session_id: str):
        """Signal end of streaming response."""
        await self._connected.wait()
        await self._send({
            "type": "event",
            "event": "session.stream.end",
            "payload": {
                "sessionId": session_id,
            },
        })

    async def send_tool_status(
        self, session_id: str, tool_name: str, status: str, result: str = ""
    ):
        """Notify Gateway about tool execution status."""
        await self._connected.wait()
        await self._send({
            "type": "event",
            "event": "session.tool",
            "payload": {
                "sessionId": session_id,
                "tool": tool_name,
                "status": status,
                "result": result,
            },
        })

    async def canvas_present(self, session_id: str, html: str, title: str = ""):
        """Render HTML on Canvas."""
        await self._connected.wait()
        await self._send({
            "type": "event",
            "event": "canvas.render",
            "payload": {
                "sessionId": session_id,
                "html": html,
                "title": title,
            },
        })

    async def close(self):
        self._should_run = False
        if self._ws:
            await self._ws.close()
        self._connected.clear()

    async def _send(self, frame: dict):
        if self._ws:
            await self._ws.send(json.dumps(frame, ensure_ascii=False))

    @property
    def is_connected(self) -> bool:
        return self._connected.is_set()

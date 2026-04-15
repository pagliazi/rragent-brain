"""
ACP Runtime — Agent Communication Protocol external runtime.

Implements the RRAgent ACP protocol so RRAgent can fully take over
the agent loop from the Gateway's embedded Pi runtime.

Gateway → ACP WebSocket → RRAgent ConversationRuntime
                       ← streaming responses back

ACP Protocol (v1):
- Client (Gateway) sends: { type: "message", content: "...", sessionId: "..." }
- Server (RRAgent) streams: { type: "delta", text: "..." }
- Server signals: { type: "tool_use", name: "...", input: {...} }
- Server completes: { type: "done", usage: {...} }
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rragent.runtime.server import RRClawServer

logger = logging.getLogger("rragent.channels.acp_runtime")


class ACPRuntime:
    """
    ACP (Agent Communication Protocol) external runtime for RRAgent.

    Runs a WebSocket server that Gateway connects to.
    Receives user messages, runs them through ConversationRuntime,
    and streams responses back via ACP protocol.

    Config in rragent.json:
    {
      "agents": [{
        "id": "rragent",
        "runtime": { "type": "acp", "url": "ws://127.0.0.1:7790" }
      }]
    }
    """

    def __init__(
        self,
        server: RRClawServer,
        host: str = "127.0.0.1",
        port: int = 7790,
    ):
        self._server = server
        self.host = host
        self.port = port
        self._ws_server: Any = None
        self._connections: dict[str, Any] = {}

    async def start(self):
        """Start the ACP WebSocket server."""
        try:
            import websockets
            self._ws_server = await websockets.serve(
                self._handle_connection,
                self.host,
                self.port,
            )
            logger.info(f"ACP runtime listening on ws://{self.host}:{self.port}")
        except ImportError:
            logger.error("websockets package required for ACP runtime")
            raise

    async def stop(self):
        """Stop the ACP server."""
        if self._ws_server:
            self._ws_server.close()
            await self._ws_server.wait_closed()
            logger.info("ACP runtime stopped")

    async def _handle_connection(self, ws: Any, path: str = ""):
        """Handle a new ACP connection from Gateway."""
        connection_id = id(ws)
        self._connections[str(connection_id)] = ws
        logger.info(f"ACP connection established: {connection_id}")

        try:
            async for raw_message in ws:
                try:
                    msg = json.loads(raw_message)
                    await self._handle_message(ws, msg)
                except json.JSONDecodeError:
                    await self._send(ws, {
                        "type": "error",
                        "error": "Invalid JSON",
                    })
                except Exception as e:
                    logger.error(f"ACP message handling error: {e}")
                    await self._send(ws, {
                        "type": "error",
                        "error": str(e),
                    })
        finally:
            del self._connections[str(connection_id)]
            logger.info(f"ACP connection closed: {connection_id}")

    async def _handle_message(self, ws: Any, msg: dict):
        """Route an ACP message to the appropriate handler."""
        msg_type = msg.get("type", "")

        if msg_type == "message":
            await self._handle_user_message(ws, msg)
        elif msg_type == "ping":
            await self._send(ws, {"type": "pong"})
        elif msg_type == "cancel":
            # TODO: implement cancellation
            pass
        else:
            await self._send(ws, {
                "type": "error",
                "error": f"Unknown message type: {msg_type}",
            })

    async def _handle_user_message(self, ws: Any, msg: dict):
        """
        Handle a user message: run through ConversationRuntime
        and stream results back via ACP protocol.
        """
        content = msg.get("content", "")
        session_id = msg.get("sessionId", msg.get("session_id", "default"))
        peer_id = msg.get("peerId", msg.get("peer_id", ""))

        if not content:
            await self._send(ws, {
                "type": "error",
                "error": "Empty message content",
            })
            return

        # Get or create runtime for this session
        runtime = self._server._get_or_create_runtime(session_id)

        # Stream turn events back via ACP protocol
        try:
            from rragent.runtime.conversation import EventType

            async for event in runtime.run_turn(content):
                if event.type == EventType.TEXT_DELTA:
                    await self._send(ws, {
                        "type": "delta",
                        "text": event.data,
                    })

                elif event.type == EventType.TOOL_START:
                    tu = event.data  # ToolUse object
                    await self._send(ws, {
                        "type": "tool_use",
                        "name": tu.name,
                        "input": tu.input,
                    })

                elif event.type == EventType.TOOL_RESULT:
                    tu = event.data["tool_use"]  # ToolUse
                    result = event.data["result"]  # ToolResult
                    await self._send(ws, {
                        "type": "tool_result",
                        "name": tu.name,
                        "result": result.content[:2000],
                        "is_error": result.is_error,
                    })

                elif event.type == EventType.WARNING:
                    await self._send(ws, {
                        "type": "warning",
                        "message": event.data,
                    })

                elif event.type == EventType.ERROR:
                    await self._send(ws, {
                        "type": "error",
                        "error": event.data,
                    })

                elif event.type == EventType.USAGE:
                    # Include usage in done message
                    pass

                elif event.type == EventType.TURN_COMPLETE:
                    await self._send(ws, {
                        "type": "done",
                        "sessionId": session_id,
                    })

        except Exception as e:
            logger.error(f"ACP turn error: {e}")
            await self._send(ws, {
                "type": "error",
                "error": str(e),
            })
            await self._send(ws, {
                "type": "done",
                "sessionId": session_id,
            })

    async def _send(self, ws: Any, data: dict):
        """Send a JSON message to the WebSocket."""
        try:
            await ws.send(json.dumps(data, ensure_ascii=False))
        except Exception as e:
            logger.debug(f"ACP send failed: {e}")

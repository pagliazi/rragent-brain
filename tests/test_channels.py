"""
Integration tests for IM (Gateway/Telegram) and Web (ACP/WebChat) channels.

Tests the full message flow:
1. Mock Gateway WS server → RRAgent GatewayChannel → ConversationRuntime → response
2. WebSocket client → RRAgent ACPRuntime → ConversationRuntime → streaming response
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import websockets
from websockets.exceptions import ConnectionClosed

from rragent.runtime.conversation import ConversationRuntime, TurnConfig, EventType
from rragent.runtime.session import Session
from rragent.tools.base import Tool, ToolSpec, ToolResult
from rragent.tools.registry import GlobalToolRegistry, ToolIndex
from rragent.tools.executor import ToolExecutor
from rragent.channels.gateway import GatewayChannel
from rragent.channels.acp_runtime import ACPRuntime

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("test_channels")


# ── Mock tools ──

class MockMarketTool(Tool):
    def __init__(self):
        self.spec = ToolSpec(
            name="market_query",
            description="查询A股市场数据",
            input_schema={
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["limitup", "concepts", "hot"]},
                },
                "required": ["type"],
            },
            is_concurrent_safe=True,
        )

    async def call(self, input_data):
        qtype = input_data.get("type", "")
        if qtype == "limitup":
            return ToolResult(content=json.dumps({
                "涨停板": [
                    {"code": "301000", "name": "半导体A", "change": "+10.0%", "sector": "半导体"},
                    {"code": "600100", "name": "中科曙光", "change": "+9.98%", "sector": "计算机"},
                    {"code": "002049", "name": "紫光国微", "change": "+10.0%", "sector": "半导体"},
                    {"code": "688396", "name": "华润微", "change": "+9.85%", "sector": "半导体"},
                ]
            }, ensure_ascii=False))
        elif qtype == "concepts":
            return ToolResult(content=json.dumps({
                "热门板块": [
                    {"name": "半导体", "change": "+5.2%", "count": 12},
                    {"name": "人工智能", "change": "+3.8%", "count": 8},
                ]
            }, ensure_ascii=False))
        return ToolResult(content="暂无数据")


class MockLLM:
    """Mock LLM that simulates realistic tool-calling behavior."""

    def __init__(self):
        self.call_count = 0

    async def stream(self, messages, system, tools, model):
        self.call_count += 1
        user_msg = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                content = m.get("content", "")
                if isinstance(content, str):
                    user_msg = content
                elif isinstance(content, list):
                    # tool_result messages
                    for block in content:
                        if block.get("type") == "tool_result":
                            # This is a tool result, extract data
                            user_msg = block.get("content", "")
                break

        # First call with user question → call tools
        if self.call_count == 1 and ("涨停" in str(messages) or "limitup" in str(messages)):
            yield {"type": "tool_use", "id": "tu_1", "name": "market_query", "input": {"type": "limitup"}}
            yield {"type": "usage", "input_tokens": 200, "output_tokens": 50}
        elif self.call_count == 1 and ("板块" in str(messages) or "concept" in str(messages)):
            yield {"type": "tool_use", "id": "tu_1", "name": "market_query", "input": {"type": "concepts"}}
            yield {"type": "usage", "input_tokens": 200, "output_tokens": 50}
        else:
            # After tool results or simple question → text response
            text = "今天涨停板有4只股票：半导体A(+10%)、中科曙光(+9.98%)、紫光国微(+10%)、华润微(+9.85%)。其中3只来自半导体板块。"
            for i in range(0, len(text), 20):
                yield {"type": "text_delta", "text": text[i : i + 20]}
            yield {"type": "usage", "input_tokens": 300, "output_tokens": 80}

    def rotate_credential(self):
        pass

    def switch_to_fallback(self):
        return False


def create_test_server():
    """Create a minimal RRClawServer-like object for testing."""
    registry = GlobalToolRegistry()
    registry.register_tier0(MockMarketTool())

    class TestServer:
        def __init__(self):
            self.registry = registry
            self._sessions = {}

        def _get_or_create_runtime(self, session_id):
            if session_id not in self._sessions:
                session = Session(session_id=session_id)
                runtime = ConversationRuntime(
                    session=session,
                    registry=registry,
                    executor=ToolExecutor(registry),
                    llm_provider=MockLLM(),
                    config=TurnConfig(max_tool_rounds=5, iteration_budget=20),
                )
                self._sessions[session_id] = runtime
            return self._sessions[session_id]

    return TestServer()


# ══════════════════════════════════════════════════════════════
# Test 1: IM Channel (Mock IM Gateway → GatewayChannel)
# ══════════════════════════════════════════════════════════════

async def test_im_gateway():
    """
    Simulate Telegram message flow:
    User (Telegram) → IM Gateway → RRAgent GatewayChannel → ConversationRuntime → response

    We create a mock Gateway WS server, then connect RRAgent's GatewayChannel to it.
    """
    print("\n" + "=" * 60)
    print("  Test IM: Gateway (Telegram/IM) Channel")
    print("=" * 60)

    received_responses = []
    test_done = asyncio.Event()
    gateway_port = 19789

    # ── Mock IM Gateway WS Server ──
    async def mock_gateway_handler(ws, path=""):
        """Simulate IM Gateway behavior."""
        # 1. Expect channel.register from RRAgent
        raw = await asyncio.wait_for(ws.recv(), timeout=5)
        reg = json.loads(raw)
        assert reg["type"] == "channel.register", f"Expected register, got {reg['type']}"
        assert reg["channel"] == "rragent"
        print(f"  ✓ Gateway received registration: channel={reg['channel']}")
        print(f"    Capabilities: {', '.join(reg.get('capabilities', [])[:5])}...")

        # 2. Send ack
        await ws.send(json.dumps({"type": "channel.ack", "status": "ok"}))

        # 3. Simulate user message from Telegram
        await asyncio.sleep(0.2)
        print("  → Sending user message: '今天涨停板有哪些半导体？'")
        await ws.send(json.dumps({
            "type": "user.message",
            "sessionId": "tg_user_12345",
            "content": "今天涨停板有哪些半导体？",
            "context": {"channel": "telegram", "userId": "12345"},
            "metadata": {"platform": "telegram"},
        }))

        # 4. Collect all responses
        try:
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=10)
                frame = json.loads(raw)
                received_responses.append(frame)

                if frame["type"] == "agent.stream":
                    pass  # streaming delta
                elif frame["type"] == "agent.tool_status":
                    print(f"  ← Tool: {frame['tool']} → {frame['status']}")
                elif frame["type"] == "agent.stream.end":
                    print(f"  ← Stream end")
                    test_done.set()
                    break
        except (asyncio.TimeoutError, ConnectionClosed):
            test_done.set()

    # Start mock gateway
    mock_server = await websockets.serve(mock_gateway_handler, "127.0.0.1", gateway_port)

    # ── RRAgent side ──
    test_server = create_test_server()

    async def handle_user_message(session_id, prompt, context=None, metadata=None):
        """RRAgent message handler — same as RRClawServer._handle_user_message."""
        runtime = test_server._get_or_create_runtime(session_id)
        full_response = ""
        async for event in runtime.run_turn(prompt):
            if event.type == EventType.TEXT_DELTA:
                full_response += event.data
                await gateway.send_stream_delta(session_id, event.data)
            elif event.type == EventType.TOOL_START:
                await gateway.send_tool_status(session_id, event.data.name, "running")
            elif event.type == EventType.TOOL_RESULT:
                tu = event.data["tool_use"]
                result = event.data["result"]
                status = "error" if result.is_error else "completed"
                await gateway.send_tool_status(session_id, tu.name, status, result.content[:200])
            elif event.type == EventType.TURN_COMPLETE:
                await gateway.send_stream_end(session_id)

    gateway = GatewayChannel(
        gateway_url=f"ws://127.0.0.1:{gateway_port}",
        agent_id="rragent",
        on_user_message=handle_user_message,
    )

    # Run gateway listener in background
    listen_task = asyncio.create_task(gateway.listen())

    try:
        await asyncio.wait_for(test_done.wait(), timeout=15)
    except asyncio.TimeoutError:
        print("  ⚠ Timeout waiting for response")

    # Verify results
    stream_deltas = [r for r in received_responses if r["type"] == "agent.stream"]
    tool_statuses = [r for r in received_responses if r["type"] == "agent.tool_status"]
    stream_ends = [r for r in received_responses if r["type"] == "agent.stream.end"]

    full_text = "".join(d.get("delta", "") for d in stream_deltas)

    print(f"\n  Results:")
    print(f"    Stream deltas: {len(stream_deltas)}")
    print(f"    Tool statuses: {len(tool_statuses)}")
    print(f"    Full response: {full_text[:100]}...")

    assert len(stream_deltas) > 0, "Should have streaming text"
    assert len(tool_statuses) > 0, "Should have tool status updates"
    assert len(stream_ends) == 1, "Should have stream end"
    assert "涨停" in full_text or "半导体" in full_text, f"Response should mention 涨停/半导体: {full_text}"

    print(f"\n  ✓ IM Gateway test PASSED")

    # Cleanup
    listen_task.cancel()
    await gateway.close()
    mock_server.close()
    await mock_server.wait_closed()


# ══════════════════════════════════════════════════════════════
# Test 2: Web Channel (ACP WebSocket Server)
# ══════════════════════════════════════════════════════════════

async def test_web_acp():
    """
    Simulate WebChat flow:
    Browser → ACP WebSocket → RRAgent ConversationRuntime → streaming response

    We start RRAgent's ACP server and connect a mock WebSocket client to it.
    """
    print("\n" + "=" * 60)
    print("  Test Web: ACP (WebChat) Channel")
    print("=" * 60)

    acp_port = 17790
    test_server = create_test_server()

    # Start ACP runtime
    acp = ACPRuntime(server=test_server, host="127.0.0.1", port=acp_port)
    await acp.start()
    print(f"  ✓ ACP server started on ws://127.0.0.1:{acp_port}")

    # ── Simulate browser WebSocket client ──
    received = []

    async with websockets.connect(f"ws://127.0.0.1:{acp_port}") as ws:
        print("  ✓ WebSocket client connected")

        # Test 2a: Ping/pong
        await ws.send(json.dumps({"type": "ping"}))
        pong = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        assert pong["type"] == "pong", f"Expected pong, got {pong}"
        print("  ✓ Ping/pong works")

        # Test 2b: Send user message
        print("  → Sending message: '今天涨停板有哪些？'")
        await ws.send(json.dumps({
            "type": "message",
            "content": "今天涨停板有哪些？",
            "sessionId": "web_session_001",
        }))

        # Collect all responses until "done"
        try:
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=10)
                msg = json.loads(raw)
                received.append(msg)

                if msg["type"] == "done":
                    break
                elif msg["type"] == "delta":
                    pass  # streaming text
                elif msg["type"] == "tool_use":
                    print(f"  ← Tool call: {msg.get('name', '?')}")
                elif msg["type"] == "tool_result":
                    print(f"  ← Tool result: {msg.get('name', '?')}")
                elif msg["type"] == "error":
                    print(f"  ✗ Error: {msg.get('error', '?')}")
        except asyncio.TimeoutError:
            print("  ⚠ Timeout")

    # Verify results
    deltas = [m for m in received if m["type"] == "delta"]
    tool_uses = [m for m in received if m["type"] == "tool_use"]
    done_msgs = [m for m in received if m["type"] == "done"]

    full_text = "".join(d.get("text", "") for d in deltas)

    print(f"\n  Results:")
    print(f"    Text deltas: {len(deltas)}")
    print(f"    Tool calls: {len(tool_uses)}")
    print(f"    Done: {len(done_msgs)}")
    print(f"    Full response: {full_text[:100]}...")

    assert len(deltas) > 0, "Should have streaming text deltas"
    assert len(done_msgs) == 1, "Should have done message"
    assert "涨停" in full_text or "半导体" in full_text, f"Response should mention stocks: {full_text}"

    print(f"\n  ✓ Web ACP test PASSED")

    # Test 2c: Multi-turn conversation (same session)
    print("\n  --- Multi-turn test ---")
    async with websockets.connect(f"ws://127.0.0.1:{acp_port}") as ws:
        print("  → Sending follow-up: '板块行情呢？'")
        await ws.send(json.dumps({
            "type": "message",
            "content": "板块行情呢？",
            "sessionId": "web_session_001",  # same session
        }))

        received2 = []
        try:
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=10)
                msg = json.loads(raw)
                received2.append(msg)
                if msg["type"] == "done":
                    break
        except asyncio.TimeoutError:
            pass

        deltas2 = [m for m in received2 if m["type"] == "delta"]
        full_text2 = "".join(d.get("text", "") for d in deltas2)
        print(f"    Follow-up response: {full_text2[:100]}...")
        assert len(deltas2) > 0, "Follow-up should have text"
        print("  ✓ Multi-turn conversation works")

    # Test 2d: Error handling — empty message
    async with websockets.connect(f"ws://127.0.0.1:{acp_port}") as ws:
        await ws.send(json.dumps({"type": "message", "content": "", "sessionId": "s1"}))
        resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        assert resp["type"] == "error"
        print("  ✓ Empty message returns error")

    # Test 2e: Error handling — invalid type
    async with websockets.connect(f"ws://127.0.0.1:{acp_port}") as ws:
        await ws.send(json.dumps({"type": "invalid_type"}))
        resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        assert resp["type"] == "error"
        print("  ✓ Invalid message type returns error")

    await acp.stop()
    print("\n  ✓ ACP server stopped cleanly")


# ══════════════════════════════════════════════════════════════
# Test 3: Concurrent sessions (multiple users)
# ══════════════════════════════════════════════════════════════

async def test_concurrent_sessions():
    """Test multiple users sending messages simultaneously."""
    print("\n" + "=" * 60)
    print("  Test Concurrent: Multiple Users")
    print("=" * 60)

    acp_port = 17791
    test_server = create_test_server()

    acp = ACPRuntime(server=test_server, host="127.0.0.1", port=acp_port)
    await acp.start()

    async def user_session(session_id: str, message: str) -> dict:
        """Simulate a single user session."""
        result = {"session_id": session_id, "deltas": [], "done": False}
        async with websockets.connect(f"ws://127.0.0.1:{acp_port}") as ws:
            await ws.send(json.dumps({
                "type": "message",
                "content": message,
                "sessionId": session_id,
            }))
            try:
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=10)
                    msg = json.loads(raw)
                    if msg["type"] == "delta":
                        result["deltas"].append(msg["text"])
                    elif msg["type"] == "done":
                        result["done"] = True
                        break
            except asyncio.TimeoutError:
                pass
        return result

    # Launch 3 concurrent users
    results = await asyncio.gather(
        user_session("user_A", "今天涨停板有哪些？"),
        user_session("user_B", "今天涨停板有哪些？"),
        user_session("user_C", "今天涨停板有哪些？"),
    )

    for r in results:
        text = "".join(r["deltas"])
        assert r["done"], f"Session {r['session_id']} should complete"
        assert len(r["deltas"]) > 0, f"Session {r['session_id']} should have text"
        print(f"  ✓ {r['session_id']}: {len(r['deltas'])} deltas, {len(text)} chars")

    print(f"\n  ✓ Concurrent sessions test PASSED ({len(results)} users)")

    await acp.stop()


# ══════════════════════════════════════════════════════════════
# Run all tests
# ══════════════════════════════════════════════════════════════

async def run_all():
    print("\n" + "═" * 60)
    print("  RRAgent Channel Integration Tests")
    print("  IM (Gateway/Telegram) + Web (ACP/WebChat)")
    print("═" * 60)

    await test_im_gateway()
    await test_web_acp()
    await test_concurrent_sessions()

    print("\n" + "═" * 60)
    print("  ALL CHANNEL TESTS PASSED")
    print("═" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(run_all())

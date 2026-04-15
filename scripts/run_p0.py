"""
P0 最小运行 — Gateway → ConversationRuntime → PyAgent
"""
import asyncio
import logging
import os
import signal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("rragent.p0")

# Suppress noisy loggers
logging.getLogger("websockets").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

from rragent.runtime.conversation import ConversationRuntime, TurnConfig, EventType
from rragent.runtime.providers.simple import SimpleLLMProvider
from rragent.runtime.session import Session
from rragent.tools.registry import GlobalToolRegistry
from rragent.tools.executor import ToolExecutor
from rragent.tools.pyagent.bridge import PyAgentBridge, PyAgentTool
from rragent.channels.gateway import GatewayChannel

# Config
GATEWAY_URL = os.getenv("GATEWAY_URL", "ws://127.0.0.1:18789")
GATEWAY_TOKEN = os.getenv("GATEWAY_TOKEN", "")
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")

SYSTEM_PROMPT = """你是 RRAgent 量化分析助手，专注于 A 股市场。
你有以下工具可用：
- pyagent_market_data: 获取全市场行情数据（涨停板、板块概念、热门股票）
- pyagent_analysis_ask: 让分析 Agent 回答市场分析问题

收到行情数据后，用中文简要总结，给出结构化分析。
标注"不构成投资建议"。"""

# State
sessions: dict[str, Session] = {}
pyagent_bridge: PyAgentBridge = None
registry: GlobalToolRegistry = None
executor: ToolExecutor = None
llm: SimpleLLMProvider = None
gateway: GatewayChannel = None


async def handle_user_message(session_id: str, text: str):
    """Handle incoming message from Gateway."""
    if not text.strip():
        return

    logger.info(f"User [{session_id[:8]}]: {text[:80]}")

    # Get or create session
    if session_id not in sessions:
        sessions[session_id] = Session(session_id=session_id)
    session = sessions[session_id]

    # Create runtime for this turn
    runtime = ConversationRuntime(
        session=session,
        registry=registry,
        executor=executor,
        llm_provider=llm,
        system_prompt=SYSTEM_PROMPT,
        config=TurnConfig(max_tool_rounds=5),
    )

    # Run the turn and stream results back
    full_text = ""
    try:
        async for event in runtime.run_turn(text):
            if event.type == EventType.TEXT_DELTA:
                full_text += event.data
                # Stream delta to Gateway
                if gateway.is_connected:
                    await gateway.send_text_delta(session_id, event.data)

            elif event.type == EventType.TOOL_START:
                logger.info(f"  Tool: {event.data.name}")
                if gateway.is_connected:
                    await gateway.send_tool_status(
                        session_id, event.data.name, "running"
                    )

            elif event.type == EventType.TOOL_RESULT:
                r = event.data["result"]
                logger.info(f"  Result: {'OK' if not r.is_error else 'ERROR'}")

            elif event.type == EventType.USAGE:
                logger.info(
                    f"  Tokens: in={event.data.input_tokens} out={event.data.output_tokens}"
                )

            elif event.type == EventType.ERROR:
                logger.error(f"  Error: {event.data}")
                if gateway.is_connected:
                    await gateway.send_text_complete(
                        session_id, f"错误: {event.data}"
                    )
                return

        # Send complete signal
        if gateway.is_connected:
            await gateway.send_stream_end(session_id)

        logger.info(f"Reply [{session_id[:8]}]: {full_text[:80]}...")

    except Exception as e:
        logger.error(f"Turn error: {e}", exc_info=True)
        if gateway.is_connected:
            await gateway.send_text_complete(session_id, f"内部错误: {e}")


async def main():
    global pyagent_bridge, registry, executor, llm, gateway

    logger.info("=== RRAgent P0 启动 ===")

    # 1. PyAgent bridge
    pyagent_bridge = PyAgentBridge(redis_url=REDIS_URL)
    await pyagent_bridge.connect()
    logger.info("✓ Redis PyAgent connected")

    # 2. Tool registry
    registry = GlobalToolRegistry()

    market_tool = PyAgentTool(
        command="market_data",
        agent="market",
        action="get_all_raw",
        description="获取 A 股全市场行情数据（涨停板、板块概念、热门股票等）",
        timeout=20,
        bridge=pyagent_bridge,
        input_schema={"type": "object", "properties": {}, "required": []},
    )
    registry.register_tier0(market_tool)

    analysis_tool = PyAgentTool(
        command="analysis_ask",
        agent="analysis",
        action="ask",
        description="让分析 Agent 回答市场分析问题。参数: question (string)",
        timeout=30,
        bridge=pyagent_bridge,
        input_schema={
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "分析问题"},
            },
            "required": ["question"],
        },
    )
    registry.register_tier0(analysis_tool)

    logger.info(f"✓ {len(registry.get_all_active_schemas())} tools registered")

    # 3. LLM provider
    llm = SimpleLLMProvider()
    logger.info("✓ LLM provider ready")

    # 4. Tool executor
    executor = ToolExecutor(registry)

    # 5. Gateway channel
    gateway = GatewayChannel(
        gateway_url=GATEWAY_URL,
        auth_token=GATEWAY_TOKEN,
        on_user_message=handle_user_message,
    )

    if GATEWAY_TOKEN:
        try:
            await gateway.connect()
            logger.info("✓ Gateway connected")
            # Start listening in background
            asyncio.create_task(gateway.listen())
        except Exception as e:
            logger.warning(f"Gateway connection failed: {e}")
            logger.info("Running in standalone mode (no IM channels)")
    else:
        logger.info("No GATEWAY_TOKEN — running standalone (stdin mode)")

    # 6. Stdin mode (for testing without Gateway)
    logger.info("\n=== RRAgent Ready. Type messages below (Ctrl+C to quit) ===\n")

    loop = asyncio.get_event_loop()
    try:
        while True:
            try:
                line = await loop.run_in_executor(None, input, "You> ")
                if line.strip():
                    await handle_user_message("stdin-session", line.strip())
                    print()
            except EOFError:
                break
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass

    logger.info("Shutting down...")
    await gateway.close()
    await pyagent_bridge.close()


if __name__ == "__main__":
    asyncio.run(main())

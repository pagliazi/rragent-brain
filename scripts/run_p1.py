"""
P1 运行 — ToolSearch + Context Engineering

Upgrades from P0:
- build_tool_registry() auto-generates tools from PYAGENT_COMMANDS + skills YAML
- ContextEngine provides 5-layer compression
- PromptBuilder generates system prompt with tool index
- ToolSearchTool as Tier 0 for lazy tool discovery
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
logger = logging.getLogger("rragent.p1")

# Suppress noisy loggers
logging.getLogger("websockets").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

from rragent.runtime.conversation import ConversationRuntime, TurnConfig, EventType
from rragent.runtime.providers.simple import SimpleLLMProvider
from rragent.runtime.session import Session
from rragent.runtime.config import RRClawConfig
from rragent.tools.registry import GlobalToolRegistry
from rragent.tools.executor import ToolExecutor
from rragent.tools.pyagent.bridge import PyAgentBridge
from rragent.tools.index_builder import build_tool_registry
from rragent.context.engine import ContextEngine
from rragent.runtime.prompt import PromptBuilder
from rragent.channels.gateway import GatewayChannel

# Config
GATEWAY_URL = os.getenv("GATEWAY_URL", "ws://127.0.0.1:18789")
GATEWAY_TOKEN = os.getenv("GATEWAY_TOKEN", "")
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
SKILLS_DIR = os.getenv(
    "RRAGENT_SKILLS_DIR",
    os.path.expanduser("~/RRAgent-Universe/rragent-brain/agents/skills"),
)

# State
sessions: dict[str, Session] = {}
pyagent_bridge: PyAgentBridge = None
registry: GlobalToolRegistry = None
executor: ToolExecutor = None
llm: SimpleLLMProvider = None
gateway: GatewayChannel = None
config: RRClawConfig = None
context_engine: ContextEngine = None


async def handle_user_message(session_id: str, text: str):
    """Handle incoming message from Gateway or stdin."""
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
        context_provider=context_engine,
        config=TurnConfig(max_tool_rounds=10),
    )

    # Run the turn and stream results back
    full_text = ""
    try:
        async for event in runtime.run_turn(text):
            if event.type == EventType.TEXT_DELTA:
                full_text += event.data
                # Stream delta to Gateway
                if gateway and gateway.is_connected:
                    await gateway.send_text_delta(session_id, event.data)

            elif event.type == EventType.TOOL_START:
                logger.info(f"  Tool: {event.data.name}")
                if gateway and gateway.is_connected:
                    await gateway.send_tool_status(
                        session_id, event.data.name, "running"
                    )

            elif event.type == EventType.TOOL_RESULT:
                r = event.data["result"]
                status = "OK" if not r.is_error else "ERROR"
                preview = r.content[:100] if r.content else ""
                logger.info(f"  Result: {status} — {preview}")

            elif event.type == EventType.USAGE:
                logger.info(
                    f"  Tokens: in={event.data.input_tokens} out={event.data.output_tokens}"
                )

            elif event.type == EventType.ERROR:
                logger.error(f"  Error: {event.data}")
                if gateway and gateway.is_connected:
                    await gateway.send_text_complete(
                        session_id, f"错误: {event.data}"
                    )
                return

        # Send complete signal
        if gateway and gateway.is_connected:
            await gateway.send_stream_end(session_id)

        if full_text:
            logger.info(f"Reply [{session_id[:8]}]: {full_text[:120]}...")

    except Exception as e:
        logger.error(f"Turn error: {e}", exc_info=True)
        if gateway and gateway.is_connected:
            await gateway.send_text_complete(session_id, f"内部错误: {e}")


async def main():
    global pyagent_bridge, registry, executor, llm, gateway, config, context_engine

    logger.info("=== RRAgent P1 启动 (ToolSearch + Context Engineering) ===")

    # 0. Load config
    config = RRClawConfig.from_file()

    # 1. PyAgent bridge
    pyagent_bridge = PyAgentBridge(redis_url=REDIS_URL)
    await pyagent_bridge.connect()
    logger.info("PyAgent Redis connected")

    # 2. Build tool registry (auto-generated from PYAGENT_COMMANDS + skills YAML)
    registry = build_tool_registry(
        bridge=pyagent_bridge,
        skills_dir=SKILLS_DIR,
    )
    stats = registry.stats()
    logger.info(
        f"Tool registry: {stats['tier0']} tier0, "
        f"{stats['tier1_indexed']} tier1 indexed"
    )

    # 3. LLM provider
    llm = SimpleLLMProvider()
    logger.info("LLM provider ready")

    # 4. Tool executor
    executor = ToolExecutor(registry)

    # 5. Context engine (5-layer compression)
    context_engine = ContextEngine(config, registry)

    # Verify system prompt token count
    builder = PromptBuilder(registry, config)
    system_prompt = builder.build_system_prompt()
    prompt_tokens = len(system_prompt) // 3  # rough CJK estimate
    logger.info(f"System prompt: {len(system_prompt)} chars, ~{prompt_tokens} tokens")
    if prompt_tokens > 8000:
        logger.warning(f"System prompt exceeds 8K token target ({prompt_tokens})")

    # 6. Gateway channel
    gateway = GatewayChannel(
        gateway_url=GATEWAY_URL,
        auth_token=GATEWAY_TOKEN,
        on_user_message=handle_user_message,
    )

    if GATEWAY_TOKEN:
        try:
            await gateway.connect()
            logger.info("Gateway connected")
            asyncio.create_task(gateway.listen())
        except Exception as e:
            logger.warning(f"Gateway connection failed: {e}")
            logger.info("Running in standalone mode (no IM channels)")
    else:
        logger.info("No GATEWAY_TOKEN — running standalone (stdin mode)")

    # 7. Stdin mode (for testing)
    logger.info("\n=== RRAgent P1 Ready. Type messages below (Ctrl+C to quit) ===\n")

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
    if gateway:
        await gateway.close()
    await pyagent_bridge.close()


if __name__ == "__main__":
    asyncio.run(main())

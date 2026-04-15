"""
P2 运行 — Fault Tolerance (Resilience Layer)

Upgrades from P1:
- ProviderRouter with fallback chain instead of SimpleLLMProvider
- ErrorClassifier wired into ConversationRuntime
- Circuit breakers on provider failures
- PyAgent bridge auto-reconnect
- Provider switch logging
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
logger = logging.getLogger("rragent.p2")

# Suppress noisy loggers
logging.getLogger("websockets").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

from rragent.runtime.conversation import ConversationRuntime, TurnConfig, EventType
from rragent.runtime.providers.router import ProviderRouter, ProviderConfig
from rragent.runtime.resilience.error_classifier import RRClawErrorClassifier
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
llm: ProviderRouter = None
error_classifier: RRClawErrorClassifier = None
gateway: GatewayChannel = None
config: RRClawConfig = None
context_engine: ContextEngine = None


def build_provider_router() -> ProviderRouter:
    """Build ProviderRouter with fallback chain from env vars."""
    configs = []

    # Primary: DashScope (qwen3.5-plus)
    primary_key = os.getenv("OPENAI_API_KEY", "")
    primary_url = os.getenv("OPENAI_BASE_URL", "https://coding.dashscope.aliyuncs.com/v1")
    primary_model = os.getenv("RRAGENT_DEFAULT_MODEL", "qwen3.5-plus")

    if primary_key:
        configs.append(ProviderConfig(
            name="dashscope-primary",
            api_key=primary_key,
            base_url=primary_url,
            model=primary_model,
        ))

    # Fallback 1: DeepSeek via SiliconFlow
    fallback_key = os.getenv("SILICONFLOW_API_KEY", "")
    if fallback_key:
        configs.append(ProviderConfig(
            name="siliconflow-deepseek",
            api_key=fallback_key,
            base_url="https://api.siliconflow.cn/v1",
            model="deepseek-ai/DeepSeek-V3",
        ))

    # Fallback 2: DeepSeek direct
    deepseek_key = os.getenv("DEEPSEEK_API_KEY", "")
    if deepseek_key:
        configs.append(ProviderConfig(
            name="deepseek-direct",
            api_key=deepseek_key,
            base_url="https://api.deepseek.com/v1",
            model="deepseek-chat",
        ))

    # If no fallbacks configured, add a second DashScope entry as
    # a dummy fallback so the router still works with single provider
    if len(configs) == 0:
        # No API keys at all — use defaults from SimpleLLMProvider
        configs.append(ProviderConfig(
            name="dashscope-default",
            api_key="sk-sp-0dd17ca1a5ed4a108b13d7942216e107",
            base_url="https://coding.dashscope.aliyuncs.com/v1",
            model="qwen3.5-plus",
        ))

    return ProviderRouter(configs)


async def handle_user_message(session_id: str, text: str):
    """Handle incoming message from Gateway or stdin."""
    if not text.strip():
        return

    logger.info(f"User [{session_id[:8]}]: {text[:80]}")

    # Get or create session
    if session_id not in sessions:
        sessions[session_id] = Session(session_id=session_id)
    session = sessions[session_id]

    # Create runtime for this turn — with error_classifier wired in
    runtime = ConversationRuntime(
        session=session,
        registry=registry,
        executor=executor,
        llm_provider=llm,
        context_provider=context_engine,
        error_classifier=error_classifier,
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

        # Log provider status after each turn
        router_status = llm.status()
        logger.info(f"  Provider: {router_status['current']}")

    except Exception as e:
        logger.error(f"Turn error: {e}", exc_info=True)
        if gateway and gateway.is_connected:
            await gateway.send_text_complete(session_id, f"内部错误: {e}")


async def main():
    global pyagent_bridge, registry, executor, llm, error_classifier
    global gateway, config, context_engine

    logger.info("=== RRAgent P2 启动 (Fault Tolerance) ===")

    # 0. Load config
    config = RRClawConfig.from_file()

    # 1. PyAgent bridge (with auto-reconnect)
    pyagent_bridge = PyAgentBridge(redis_url=REDIS_URL)
    try:
        await pyagent_bridge.connect()
        logger.info("PyAgent Redis connected")
    except Exception as e:
        logger.warning(f"PyAgent Redis connection failed: {e}")
        logger.info("PyAgent tools will be unavailable until Redis reconnects")

    # 2. Build tool registry
    registry = build_tool_registry(
        bridge=pyagent_bridge,
        skills_dir=SKILLS_DIR,
    )
    stats = registry.stats()
    logger.info(
        f"Tool registry: {stats['tier0']} tier0, "
        f"{stats['tier1_indexed']} tier1 indexed"
    )

    # 3. LLM provider — ProviderRouter with fallback chain
    llm = build_provider_router()
    router_status = llm.status()
    logger.info(
        f"Provider router ready: {router_status['current']} "
        f"({len(router_status['providers'])} providers in chain)"
    )
    for p in router_status["providers"]:
        logger.info(f"  - {p['name']} (model: {p['model']})")

    # 4. Error classifier
    error_classifier = RRClawErrorClassifier()
    logger.info("Error classifier ready")

    # 5. Tool executor
    executor = ToolExecutor(registry)

    # 6. Context engine (5-layer compression)
    context_engine = ContextEngine(config, registry)

    # Verify system prompt token count
    builder = PromptBuilder(registry, config)
    system_prompt = builder.build_system_prompt()
    prompt_tokens = len(system_prompt) // 3  # rough CJK estimate
    logger.info(f"System prompt: {len(system_prompt)} chars, ~{prompt_tokens} tokens")
    if prompt_tokens > 8000:
        logger.warning(f"System prompt exceeds 8K token target ({prompt_tokens})")

    # 7. Gateway channel
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

    # 8. Stdin mode (for testing)
    logger.info("\n=== RRAgent P2 Ready. Type messages below (Ctrl+C to quit) ===\n")

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

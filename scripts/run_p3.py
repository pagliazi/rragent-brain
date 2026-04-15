"""
P3 运行 — Self-Learning (自学习)

Upgrades from P2:
- BackgroundReviewSystem wired into ConversationRuntime
- EvolutionEngine started as background async task
- SkillLoader + SkillExecutor for skill matching
- Execution event publishing after each turn
- Memory tiers (session/user/system) initialized
- Hermes runtime registered as Tier 1 tool (hermes_delegate)
"""
import asyncio
import logging
import os
import json
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("rragent.p3")

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

# P3 imports
from rragent.tools.hermes.runtime import HermesNativeRuntime
from rragent.evolution.background_review import BackgroundReviewSystem
from rragent.evolution.engine import EvolutionEngine
from rragent.skills.loader import SkillLoader
from rragent.skills.executor import SkillExecutor
from rragent.context.memory.tier1_session import SessionMemory
from rragent.context.memory.tier2_user import UserMemory
from rragent.context.memory.tier3_system import SystemMemory

# P4 imports
from rragent.evolution.gepa_pipeline import GEPAPipeline
from rragent.evolution.autoresearch_loop import StrategyResearchLoop
from rragent.workers.boot import RedisWorker, PyAgentWorker, HermesWorker, GatewayWorker
from rragent.workers.coordinator import WorkerCoordinator
from rragent.commands.evolve import EvolveCommand
from rragent.commands.research import ResearchCommand

# P5 imports
from rragent.tools.builtin.canvas import CanvasTool
from rragent.channels.acp_runtime import ACPRuntime

# Config
GATEWAY_URL = os.getenv("GATEWAY_URL", "ws://127.0.0.1:18789")
GATEWAY_TOKEN = os.getenv("GATEWAY_TOKEN", "")
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
SKILLS_DIR = os.getenv(
    "RRAGENT_SKILLS_DIR",
    os.path.join(os.getenv("BRAIN_PATH", os.path.expanduser("~/RRAgent-Universe/rragent-brain")), "agents/skills"),
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

# P3 state
hermes_runtime: HermesNativeRuntime = None
background_review_system: BackgroundReviewSystem = None
evolution_engine: EvolutionEngine = None
skill_loader: SkillLoader = None
skill_executor: SkillExecutor = None
session_memory: SessionMemory = None
user_memory: UserMemory = None
system_memory: SystemMemory = None

# P4 state
gepa_pipeline: GEPAPipeline = None
research_loop: StrategyResearchLoop = None
worker_coordinator: WorkerCoordinator = None
evolve_command: EvolveCommand = None
research_command: ResearchCommand = None

# P5 state
acp_runtime: ACPRuntime = None


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

    if len(configs) == 0:
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

    # --- P4: Slash command routing ---
    stripped = text.strip()
    if stripped.startswith("/evolve") and evolve_command:
        args = stripped[len("/evolve"):].strip()
        result = await evolve_command.execute(args)
        print(f"\n{result}\n")
        if gateway and gateway.is_connected:
            await gateway.send_text_complete(session_id, result)
        return
    if stripped.startswith("/research") and research_command:
        args = stripped[len("/research"):].strip()
        result = await research_command.execute(args)
        print(f"\n{result}\n")
        if gateway and gateway.is_connected:
            await gateway.send_text_complete(session_id, result)
        return

    # Get or create session
    if session_id not in sessions:
        sessions[session_id] = Session(session_id=session_id)
    session = sessions[session_id]

    # --- P3: Skill matching before runtime ---
    if skill_executor:
        matched_skill = skill_executor.match_skill(text)
        if matched_skill:
            logger.info(f"Skill matched: {matched_skill}")
            skill_instruction = skill_executor.prepare_skill(
                matched_skill, session_id=session_id
            )
            if skill_instruction:
                # Inject skill instruction as system message into session
                session.append_system(skill_instruction)

    # Create runtime for this turn
    runtime = ConversationRuntime(
        session=session,
        registry=registry,
        executor=executor,
        llm_provider=llm,
        context_provider=context_engine,
        error_classifier=error_classifier,
        config=TurnConfig(max_tool_rounds=10),
    )

    # --- P3: Wire background review ---
    if background_review_system:
        runtime.background_review = background_review_system
        background_review_system.increment_turn()

    # Run the turn and stream results back
    full_text = ""
    tool_calls_in_turn = []
    turn_start = time.time()

    try:
        async for event in runtime.run_turn(text):
            if event.type == EventType.TEXT_DELTA:
                full_text += event.data
                if gateway and gateway.is_connected:
                    await gateway.send_text_delta(session_id, event.data)

            elif event.type == EventType.TOOL_START:
                logger.info(f"  Tool: {event.data.name}")
                tool_calls_in_turn.append({
                    "tool": event.data.name,
                    "start": time.time(),
                })
                if gateway and gateway.is_connected:
                    await gateway.send_tool_status(
                        session_id, event.data.name, "running"
                    )

            elif event.type == EventType.TOOL_RESULT:
                r = event.data["result"]
                status = "OK" if not r.is_error else "ERROR"
                preview = r.content[:100] if r.content else ""
                logger.info(f"  Result: {status} -- {preview}")

                # Track for evolution engine
                if tool_calls_in_turn:
                    tc = tool_calls_in_turn[-1]
                    tc["success"] = not r.is_error
                    tc["latency_ms"] = (time.time() - tc["start"]) * 1000
                    tc["result_summary"] = preview

                # Increment skill counter for background review
                if background_review_system:
                    background_review_system.increment_iterations(1)

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

        # Log provider status
        router_status = llm.status()
        logger.info(f"  Provider: {router_status['current']}")

        # --- P3: Publish execution events to Redis Stream for Evolution Engine ---
        if evolution_engine and tool_calls_in_turn:
            for tc in tool_calls_in_turn:
                try:
                    await evolution_engine.record_execution(
                        tool_name=tc.get("tool", ""),
                        action="call",
                        params={},
                        result_summary=tc.get("result_summary", ""),
                        success=tc.get("success", True),
                        latency_ms=tc.get("latency_ms", 0),
                        session_id=session_id,
                    )
                except Exception as e:
                    logger.debug(f"Failed to record execution event: {e}")

        # --- P3: Complete skill if one was active ---
        if skill_executor and skill_executor.get_active_skill(session_id):
            skill_executor.complete_skill(session_id, success=True)

        # --- P3: Store discoveries in session memory ---
        if session_memory and full_text:
            # Store last response topic as session context
            session_memory.set(
                "last_topic",
                text[:200],
                category="context",
                source="conversation",
            )

    except Exception as e:
        logger.error(f"Turn error: {e}", exc_info=True)
        if gateway and gateway.is_connected:
            await gateway.send_text_complete(session_id, f"内部错误: {e}")


async def main():
    global pyagent_bridge, registry, executor, llm, error_classifier
    global gateway, config, context_engine
    global hermes_runtime, background_review_system, evolution_engine
    global skill_loader, skill_executor
    global session_memory, user_memory, system_memory
    global gepa_pipeline, research_loop, worker_coordinator
    global evolve_command, research_command
    global acp_runtime

    logger.info("=== RRAgent P3 启动 (Self-Learning) ===")

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

    # 2. Hermes runtime (P3)
    hermes_runtime = HermesNativeRuntime(
        hermes_path="/tmp/full-deploy-test/hermes-venv",
        model=os.getenv("RRAGENT_DEFAULT_MODEL", "qwen3.5-plus"),
    )
    if hermes_runtime.available:
        logger.info("Hermes runtime loaded")
    else:
        logger.warning("Hermes runtime not available (will run without delegation)")

    # 3. Build tool registry (with hermes_delegate)
    registry = build_tool_registry(
        bridge=pyagent_bridge,
        skills_dir=SKILLS_DIR,
        hermes_runtime=hermes_runtime if hermes_runtime.available else None,
    )
    # P5: Register canvas tool as Tier 0
    canvas_tool = CanvasTool(gateway=None)  # gateway set later if connected
    registry.register_tier0(canvas_tool)

    stats = registry.stats()
    logger.info(
        f"Tool registry: {stats['tier0']} tier0, "
        f"{stats['tier1_indexed']} tier1 indexed"
    )

    # 4. LLM provider
    llm = build_provider_router()
    router_status = llm.status()
    logger.info(
        f"Provider router ready: {router_status['current']} "
        f"({len(router_status['providers'])} providers in chain)"
    )
    for p in router_status["providers"]:
        logger.info(f"  - {p['name']} (model: {p['model']})")

    # 5. Error classifier
    error_classifier = RRClawErrorClassifier()
    logger.info("Error classifier ready")

    # 6. Tool executor
    executor = ToolExecutor(registry)

    # 7. Memory tiers (P3)
    session_memory = SessionMemory(session_id="default")
    user_memory = UserMemory()
    system_memory = SystemMemory()
    logger.info(
        f"Memory tiers: session={session_memory.size}, "
        f"user={user_memory.stats['total_entries']}, "
        f"system={system_memory.stats['total_entries']}"
    )

    # 8. Context engine (5-layer compression + memory injection)
    context_engine = ContextEngine(config, registry)
    context_engine.session_memory = session_memory
    context_engine.user_memory = user_memory
    context_engine.system_memory = system_memory

    # Verify system prompt
    builder = PromptBuilder(registry, config)
    system_prompt = builder.build_system_prompt()
    prompt_tokens = len(system_prompt) // 3
    logger.info(f"System prompt: {len(system_prompt)} chars, ~{prompt_tokens} tokens")
    if prompt_tokens > 8000:
        logger.warning(f"System prompt exceeds 8K token target ({prompt_tokens})")

    # 9. Background Review System (P3)
    background_review_system = BackgroundReviewSystem(
        hermes_runtime=hermes_runtime if hermes_runtime.available else None,
    )
    logger.info(
        f"Background review: available={background_review_system.available}, "
        f"stats={background_review_system.stats}"
    )

    # 10. Skill Loader + Executor (P3)
    skill_loader = SkillLoader()
    skills = skill_loader.load_all()
    skill_executor = SkillExecutor(skill_loader, registry)
    logger.info(f"Skills loaded: {len(skills)}")

    # 11. Evolution Engine (P3) — background task
    evolution_engine = EvolutionEngine(redis_url=REDIS_URL)
    try:
        await evolution_engine.start()
        logger.info(f"Evolution engine started: {evolution_engine.stats}")
    except Exception as e:
        logger.warning(f"Evolution engine start failed: {e}")

    # --- P4: GEPA Pipeline ---
    gepa_pipeline = GEPAPipeline(
        hermes_runtime=hermes_runtime if hermes_runtime and hermes_runtime.available else None,
    )
    logger.info("GEPA pipeline initialized")

    # --- P4: Autoresearch Loop ---
    research_loop = StrategyResearchLoop(
        hermes_runtime=hermes_runtime if hermes_runtime and hermes_runtime.available else None,
        pyagent_bridge=pyagent_bridge,
    )
    logger.info("Autoresearch loop initialized")

    # --- P4: Slash commands ---
    evolve_command = EvolveCommand(
        evolution_engine=evolution_engine,
        gepa_pipeline=gepa_pipeline,
        system_memory=system_memory,
    )
    research_command = ResearchCommand(research_loop=research_loop)
    logger.info("Commands registered: /evolve, /research")

    # --- P4: Worker Boot ---
    worker_coordinator = WorkerCoordinator()
    redis_worker = RedisWorker(redis_url=REDIS_URL)
    pyagent_worker = PyAgentWorker(redis_url=REDIS_URL)
    hermes_worker = HermesWorker(
        hermes_path="/tmp/full-deploy-test/hermes-venv",
    )
    hermes_worker.set_runtime(hermes_runtime)
    gateway_worker = GatewayWorker(gateway_url=GATEWAY_URL)

    worker_coordinator.register(redis_worker)
    worker_coordinator.register(pyagent_worker)
    worker_coordinator.register(hermes_worker)
    worker_coordinator.register(gateway_worker)

    try:
        boot_ok = await worker_coordinator.boot_all()
        if boot_ok:
            await worker_coordinator.start_all()
            logger.info(f"Worker coordinator: all workers started (state={worker_coordinator.status.state})")
        else:
            logger.warning(f"Worker coordinator: boot incomplete (state={worker_coordinator.status.state})")
            # Log individual worker states
            for wname, wstatus in worker_coordinator.status.workers.items():
                logger.info(f"  Worker [{wname}]: {wstatus.state.value} (error: {wstatus.error or 'none'})")
    except Exception as e:
        logger.warning(f"Worker coordinator boot failed: {e}")

    # 12. Gateway channel
    gateway = GatewayChannel(
        gateway_url=GATEWAY_URL,
        auth_token=GATEWAY_TOKEN,
        on_user_message=handle_user_message,
    )

    if GATEWAY_TOKEN:
        try:
            await gateway.connect()
            logger.info("Gateway connected")
            # Don't start listen() here — main loop will handle it
            # P5: wire canvas tool to gateway for live rendering
            canvas_tool._gateway = gateway
        except Exception as e:
            logger.warning(f"Gateway connection failed: {e}")
            logger.info("Running in standalone mode (no IM channels)")
    else:
        logger.info("No GATEWAY_TOKEN -- running standalone (stdin mode)")

    # --- P5: ACP Runtime ---
    if os.getenv("ACP_ENABLED", "").lower() == "true":
        # Lightweight adapter so ACPRuntime can create ConversationRuntime instances
        class _ACPServerAdapter:
            def _get_or_create_runtime(self, session_id: str) -> ConversationRuntime:
                if session_id not in sessions:
                    sessions[session_id] = Session(session_id=session_id)
                sess = sessions[session_id]
                rt = ConversationRuntime(
                    session=sess,
                    registry=registry,
                    executor=executor,
                    llm_provider=llm,
                    context_provider=context_engine,
                    error_classifier=error_classifier,
                    config=TurnConfig(max_tool_rounds=10),
                )
                if background_review_system:
                    rt.background_review = background_review_system
                return rt

        acp_port = int(os.getenv("ACP_PORT", "7790"))
        acp_runtime = ACPRuntime(
            server=_ACPServerAdapter(),
            host="127.0.0.1",
            port=acp_port,
        )
        try:
            await acp_runtime.start()
            logger.info(f"ACP runtime started on :{acp_port}")
        except Exception as e:
            logger.warning(f"ACP runtime failed to start: {e}")
            acp_runtime = None

    # 13. Main loop — Redis orchestrator mode, Gateway mode, or stdin mode
    redis_mode = os.getenv("RRAGENT_REDIS_MODE", "").lower() == "true"

    if redis_mode and pyagent_bridge and pyagent_bridge.is_connected:
        # --- RRAgent 替代 orchestrator：直接订阅 Redis 频道处理 IM 消息 ---
        import redis.asyncio as aioredis
        listen_channel = os.getenv("RRAGENT_LISTEN_CHANNEL", "rragent:orchestrator")
        r = aioredis.from_url(REDIS_URL, decode_responses=True)
        pubsub = r.pubsub()
        await pubsub.subscribe(listen_channel)
        logger.info(f"\n=== RRAgent running in Redis mode (subscribed to {listen_channel}) ===\n")

        async def _handle_redis_message(raw_data):
            try:
                data = json.loads(raw_data)
                params = data.get("params", {})
                reply_channel = params.get("reply_channel", "")
                text = params.get("args", params.get("command", ""))
                uid = params.get("uid", data.get("sender", "unknown"))
                msg_id = data.get("id", "")

                if not text:
                    return

                # Send progress
                if reply_channel:
                    await r.publish(reply_channel, json.dumps({
                        "type": "progress", "text": "🤖 RRAgent 正在处理...",
                        "in_reply_to": msg_id,
                    }, ensure_ascii=False))

                # Run through ConversationRuntime
                session_key = f"im-{uid}"
                if session_key not in sessions:
                    sessions[session_key] = Session(session_id=session_key)
                session = sessions[session_key]

                runtime = ConversationRuntime(
                    session=session, registry=registry, executor=executor,
                    llm_provider=llm, context_provider=context_engine,
                    error_classifier=error_classifier,
                    system_prompt=system_prompt,
                    config=TurnConfig(max_tool_rounds=10),
                )
                if background_review_system:
                    runtime.background_review = background_review_system
                    background_review_system.increment_turn()

                full_text = ""
                async for event in runtime.run_turn(text):
                    if event.type == EventType.TEXT_DELTA:
                        full_text += event.data
                    elif event.type == EventType.TOOL_START:
                        if reply_channel:
                            await r.publish(reply_channel, json.dumps({
                                "type": "progress",
                                "text": f"🔧 调用 {event.data.name}...",
                                "in_reply_to": msg_id,
                            }, ensure_ascii=False))

                # Reply
                if reply_channel and full_text:
                    await r.publish(reply_channel, json.dumps({
                        "type": "done",
                        "text": full_text,
                        "in_reply_to": msg_id,
                        "source": "rragent",
                        "timestamp": time.time(),
                    }, ensure_ascii=False))
                    logger.info(f"Reply [{uid[:8]}]: {full_text[:80]}...")

            except Exception as e:
                logger.error(f"Redis message error: {e}", exc_info=True)
                if reply_channel:
                    await r.publish(reply_channel, json.dumps({
                        "type": "done", "text": f"❌ RRAgent 错误: {e}",
                        "in_reply_to": msg_id,
                    }, ensure_ascii=False))

        try:
            async for raw in pubsub.listen():
                if raw["type"] != "message":
                    continue
                asyncio.create_task(_handle_redis_message(raw["data"]))
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await pubsub.unsubscribe(listen_channel)
            await r.aclose()

    elif GATEWAY_TOKEN and gateway and gateway.is_connected:
        logger.info("\n=== RRAgent running in Gateway mode (listening for IM messages) ===\n")
        try:
            await gateway.listen()
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
    else:
        logger.info("\n=== RRAgent P3 Ready. Type messages below (Ctrl+C to quit) ===\n")
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

    # Shutdown
    logger.info("Shutting down...")
    if acp_runtime:
        await acp_runtime.stop()
    if worker_coordinator:
        await worker_coordinator.shutdown_all()
    if evolution_engine:
        await evolution_engine.stop()
    if hermes_runtime:
        hermes_runtime.shutdown()
    if gateway:
        await gateway.close()
    await pyagent_bridge.close()


if __name__ == "__main__":
    asyncio.run(main())

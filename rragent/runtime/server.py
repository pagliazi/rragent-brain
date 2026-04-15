"""
RRAgent Server — Main entry point.

Orchestrates all harness components:
1. Loads configuration
2. Connects to Redis (PyAgent bridge)
3. Connects to IM Gateway
4. Initializes tool registry
5. Runs ConversationRuntime for each incoming message
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

from rragent.runtime.config import RRClawConfig
from rragent.runtime.conversation import ConversationRuntime, TurnConfig, EventType
from rragent.runtime.session import Session
from rragent.tools.registry import GlobalToolRegistry
from rragent.tools.executor import ToolExecutor
from rragent.tools.pyagent.bridge import PyAgentBridge, register_pyagent_tools
from rragent.channels.gateway import GatewayChannel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("rragent.server")


class RRClawServer:
    """Main RRAgent harness server."""

    def __init__(self, config: RRClawConfig):
        self.config = config
        self._sessions: dict[str, ConversationRuntime] = {}
        self._shutdown_event = asyncio.Event()

        # Components (initialized in start())
        self.registry: GlobalToolRegistry | None = None
        self.executor: ToolExecutor | None = None
        self.pyagent_bridge: PyAgentBridge | None = None
        self.gateway: GatewayChannel | None = None
        self.llm_provider = None
        self.context_provider = None
        self.error_classifier = None
        self.background_review = None
        self.health_monitor = None
        self.stream_consumer = None

    async def start(self):
        """Initialize all components and start the server."""
        logger.info("RRAgent Harness starting...")

        # 1. Tool registry
        self.registry = GlobalToolRegistry()

        # 2. Redis / PyAgent
        self.pyagent_bridge = PyAgentBridge(
            redis_url=self.config.get("redis", "url", default="redis://127.0.0.1:6379/0"),
        )
        try:
            await self.pyagent_bridge.connect()
            register_pyagent_tools(self.registry, self.pyagent_bridge)
        except Exception as e:
            logger.warning(f"PyAgent bridge connection failed: {e}. Running without PyAgent tools.")

        # 3. Register built-in Tier 0 tools
        self._register_builtin_tools()

        # 4. LLM Provider
        self._init_llm_provider()

        # 5. Context Provider (P1)
        self._init_context_provider()

        # 6. Error Classifier (P2)
        self._init_error_classifier()

        # 7. Health Monitor (P2)
        self._init_health_monitor()

        # 8. Background Review (P3)
        self._init_background_review()

        # 8b. ReachRich real-time stream consumer
        await self._init_stream_consumer()

        # 9. Tool executor
        self.executor = ToolExecutor(registry=self.registry)

        # 10. Gateway
        self.gateway = GatewayChannel(
            gateway_url=self.config.get("gateway", "url", default="ws://127.0.0.1:18789"),
            auth_token=self.config.get("gateway", "auth_token", default=""),
            agent_id=self.config.get("gateway", "agent_id", default="rragent"),
            on_user_message=self._handle_user_message,
        )

        # Launch background tasks
        tasks = [
            asyncio.create_task(self.gateway.listen(), name="gateway"),
        ]

        if self.health_monitor:
            tasks.append(
                asyncio.create_task(self.health_monitor.run(), name="health_monitor")
            )

        logger.info(
            f"RRAgent Harness ready. "
            f"Tools: {self.registry.stats()}"
        )

        # Wait for shutdown
        try:
            await self._shutdown_event.wait()
        finally:
            for t in tasks:
                t.cancel()
            await self.shutdown()

    async def _handle_user_message(
        self,
        session_id: str,
        prompt: str,
        context: dict | None = None,
        metadata: dict | None = None,
    ):
        """Handle an incoming user message from Gateway."""
        logger.info(f"[{session_id}] User: {prompt[:100]}...")

        # Get or create runtime for this session
        runtime = self._get_or_create_runtime(session_id)

        # Stream response back to Gateway
        full_response = ""
        async for event in runtime.run_turn(prompt):
            if event.type == EventType.TEXT_DELTA:
                full_response += event.data
                if self.gateway:
                    await self.gateway.send_stream_delta(session_id, event.data)

            elif event.type == EventType.TOOL_START:
                tu = event.data
                if self.gateway:
                    await self.gateway.send_tool_status(
                        session_id, tu.name, "running"
                    )

            elif event.type == EventType.TOOL_RESULT:
                tu = event.data["tool_use"]
                result = event.data["result"]
                if self.gateway:
                    status = "error" if result.is_error else "completed"
                    await self.gateway.send_tool_status(
                        session_id, tu.name, status, result.content[:500]
                    )

            elif event.type == EventType.WARNING:
                logger.warning(f"[{session_id}] {event.data}")

            elif event.type == EventType.ERROR:
                logger.error(f"[{session_id}] {event.data}")
                if self.gateway:
                    await self.gateway.send_text(
                        session_id, f"Error: {event.data}"
                    )
                return

            elif event.type == EventType.TURN_COMPLETE:
                if self.gateway:
                    await self.gateway.send_stream_end(session_id)

        if full_response and self.gateway:
            # Final complete response for non-streaming clients
            pass  # stream_end already sent

    def _get_or_create_runtime(self, session_id: str) -> ConversationRuntime:
        """Get existing runtime or create new one for a session."""
        if session_id not in self._sessions:
            session = Session(
                session_id=session_id,
                session_dir=self.config.get("session", "dir", default="~/.rragent/sessions"),
                rotation_size=self.config.get("session", "rotation_size", default=262144),
            )

            runtime = ConversationRuntime(
                session=session,
                registry=self.registry,
                executor=self.executor,
                llm_provider=self.llm_provider,
                context_provider=self.context_provider,
                error_classifier=self.error_classifier,
                config=TurnConfig(
                    max_tool_rounds=30,
                    iteration_budget=self.config.get(
                        "hermes", "iteration_budget", default=90
                    ) or 90,
                ),
                system_prompt=self._build_system_prompt(),
            )

            if self.background_review:
                runtime.background_review = self.background_review

            self._sessions[session_id] = runtime

        return self._sessions[session_id]

    def _register_builtin_tools(self):
        """Register Tier 0 built-in tools."""
        from rragent.tools.builtin.bash import BashTool
        from rragent.tools.builtin.file_ops import ReadFileTool, WriteFileTool, EditFileTool
        from rragent.tools.builtin.market_query import MarketQueryTool

        builtin_tools = [
            BashTool(),
            ReadFileTool(),
            WriteFileTool(),
            EditFileTool(),
        ]

        # MarketQueryTool needs pyagent bridge
        if self.pyagent_bridge and self.pyagent_bridge.is_connected:
            builtin_tools.append(MarketQueryTool(self.pyagent_bridge))

        for tool in builtin_tools:
            self.registry.register_tier0(tool)

        # tool_search is registered in P1
        try:
            from rragent.tools.search import ToolSearchTool
            self.registry.register_tier0(ToolSearchTool(self.registry))
        except ImportError:
            pass

        # memory tool registered in P3
        try:
            from rragent.context.memory.tier2_user import MemoryTool
            self.registry.register_tier0(MemoryTool())
        except ImportError:
            pass

    def _build_system_prompt(self) -> str:
        """Build system prompt for ConversationRuntime."""
        try:
            from rragent.runtime.prompt import PromptBuilder
            builder = PromptBuilder(self.registry, self.config)
            return builder.build_system_prompt()
        except ImportError:
            return self._default_system_prompt()

    def _default_system_prompt(self) -> str:
        return """你是 RRAgent，一个统一的A股量化分析和多功能智能助手。

你可以：
- 查询市场行情（涨停板、连板、板块、热门股）
- 运行量化回测和因子分析
- 执行开发任务（代码审查、重构、部署）
- 管理日程、提醒、邮件等
- 监控系统告警和服务健康
- 进行深度研究和新闻搜索

使用 tool_search 搜索更多可用工具。"""

    def _init_llm_provider(self):
        """Initialize LLM provider with fallback chain."""
        try:
            from rragent.runtime.providers.router import ProviderRouter
            self.llm_provider = ProviderRouter(self.config)
        except ImportError:
            from rragent.runtime.providers.anthropic import AnthropicProvider
            self.llm_provider = AnthropicProvider(
                model=self.config.get("providers", "primary", default="qwen3.5-plus"),
            )

    def _init_context_provider(self):
        """Initialize context compression engine (P1)."""
        try:
            from rragent.context.engine import ContextEngine
            self.context_provider = ContextEngine(self.config, self.registry)
        except ImportError:
            self.context_provider = None

    def _init_error_classifier(self):
        """Initialize error classifier (P2)."""
        try:
            from rragent.runtime.resilience.error_classifier import RRClawErrorClassifier
            self.error_classifier = RRClawErrorClassifier()
        except ImportError:
            self.error_classifier = None

    def _init_health_monitor(self):
        """Initialize health monitor (P2)."""
        try:
            from rragent.runtime.resilience.health_monitor import HealthMonitor
            self.health_monitor = HealthMonitor(
                redis_url=self.config.get("redis", "url"),
                check_interval=self.config.get("resilience", "health_check_interval", default=10),
            )
        except ImportError:
            self.health_monitor = None

    def _init_background_review(self):
        """Initialize background review system (P3)."""
        try:
            from rragent.evolution.background_review import BackgroundReviewSystem
            self.background_review = BackgroundReviewSystem(self.config)
        except ImportError:
            self.background_review = None

    async def _init_stream_consumer(self):
        """Initialize ReachRich real-time stream consumer.

        Subscribes to Redis Pub/Sub channels published by ReachRich's
        data_factory Celery workers and FastAPI BridgePublisher.

        Works with or without REACHRICH_TOKEN:
          - With token: verifies HMAC signatures on signed messages
          - Without token: accepts all unsigned messages (default mode)
        """
        if not (self.pyagent_bridge and self.pyagent_bridge.is_connected):
            logger.debug("ReachRich stream consumer disabled (no Redis connection)")
            return

        try:
            from rragent.data_sources.reachrich_stream import (
                ReachRichStreamConsumer,
                ReachRichStreamConfig,
            )
            rr_cfg = self.config.raw.get("reachrich", {})
            stream_config = ReachRichStreamConfig.from_config(rr_cfg)
            # Use binary-mode Redis for Pub/Sub (raw message parsing)
            self.stream_consumer = ReachRichStreamConsumer(
                redis=self.pyagent_bridge.redis_raw,
                config=stream_config,
            )
            await self.stream_consumer.start()
            logger.info(
                "ReachRich real-time stream consumer started (hmac_verify=%s)",
                stream_config.verify_hmac and bool(stream_config.token),
            )
        except Exception as e:
            logger.warning(f"ReachRich stream consumer init failed: {e}")

    async def shutdown(self):
        """Graceful shutdown."""
        logger.info("RRAgent shutting down...")

        # Stop real-time stream consumer
        if self.stream_consumer:
            await self.stream_consumer.stop()

        # Close all sessions
        for sid, runtime in self._sessions.items():
            runtime.session.close()

        if self.gateway:
            await self.gateway.close()
        if self.pyagent_bridge:
            await self.pyagent_bridge.close()

        logger.info("RRAgent shutdown complete")

    def request_shutdown(self):
        self._shutdown_event.set()


def main():
    parser = argparse.ArgumentParser(description="RRAgent Harness Server")
    parser.add_argument("--config", default=None, help="Path to config YAML")
    args = parser.parse_args()

    config = RRClawConfig.from_file(args.config)
    server = RRClawServer(config)

    loop = asyncio.new_event_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, server.request_shutdown)

    try:
        loop.run_until_complete(server.start())
    finally:
        loop.close()


if __name__ == "__main__":
    main()

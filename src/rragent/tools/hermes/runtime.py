"""
Hermes Native Runtime — run Hermes AIAgent in ThreadPoolExecutor.

Wraps the synchronous Hermes agent loop for async consumption.
Reuses Hermes native capabilities:
- IterationBudget (with PTC refund)
- Credential Pool (4 strategies)
- Error Classification + Failover
- Context Compressor
- Background Review (daemon thread)
- PTC (execute_code via UDS)
- Session Persistence (SQLite FTS5)
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("rragent.hermes.runtime")


@dataclass
class HermesResult:
    """Result from a Hermes agent run."""

    success: bool
    output: str
    tool_calls: list[dict] = field(default_factory=list)
    iterations_used: int = 0
    skills_created: list[str] = field(default_factory=list)
    memories_saved: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class HermesNativeRuntime:
    """
    Run Hermes AIAgent natively in a thread pool.

    Hermes is synchronous (run_agent.py while loop), so we wrap it
    in run_in_executor for async compatibility.

    Usage:
        runtime = HermesNativeRuntime("/opt/hermes-agent")
        result = await runtime.run_task(
            "Analyze the conversation and create a skill",
            toolsets=["core", "web"],
            max_iterations=10,
        )
    """

    def __init__(
        self,
        hermes_path: str = "/Users/clawagent/hermes-agent",
        model: str = "anthropic/claude-sonnet-4-6",
        max_workers: int = 3,
    ):
        self.hermes_path = Path(hermes_path)
        self.model = model
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="hermes",
        )
        self._agent_class: Any = None
        self._available = False
        self._lock = threading.Lock()

        self._try_load()

    def _try_load(self):
        """Try to import Hermes AIAgent class."""
        try:
            hermes_src = self.hermes_path / "src"
            if hermes_src.exists() and str(hermes_src) not in sys.path:
                sys.path.insert(0, str(hermes_src))

            if str(self.hermes_path) not in sys.path:
                sys.path.insert(0, str(self.hermes_path))

            # Also check venv site-packages
            for sp in self.hermes_path.glob("lib/*/site-packages"):
                if str(sp) not in sys.path:
                    sys.path.insert(0, str(sp))

            # Try importing the agent module
            run_agent = importlib.import_module("run_agent")
            self._agent_class = getattr(run_agent, "AIAgent", None)

            if self._agent_class is None:
                logger.warning("Hermes AIAgent class not found in run_agent module")
                return

            self._available = True
            logger.info(f"Hermes runtime loaded from {self.hermes_path}")

        except ImportError as e:
            logger.warning(f"Hermes not available: {e}")
        except Exception as e:
            logger.error(f"Failed to load Hermes: {e}")

    @property
    def available(self) -> bool:
        return self._available

    def list_tools(self) -> list[dict]:
        """List available Hermes tools (for ToolSearch index building).

        Hermes 0.9.0 removed agent/tool_registry.py — we enumerate tools
        from the agent's toolset manifest instead, or fall back to a static
        list of known hermes core tools.
        """
        if not self._available:
            return []

        # Try hermes 0.9.0 toolset manifest
        try:
            toolset_dir = self.hermes_path / "agent" / "toolsets"
            if toolset_dir.exists():
                import importlib.util
                tools = []
                for f in sorted(toolset_dir.glob("*.py")):
                    if f.name.startswith("_"):
                        continue
                    spec = importlib.util.spec_from_file_location(f.stem, f)
                    if spec and spec.loader:
                        mod = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(mod)  # type: ignore
                        for attr in dir(mod):
                            obj = getattr(mod, attr)
                            if callable(obj) and hasattr(obj, "__tool_name__"):
                                tools.append({
                                    "name": obj.__tool_name__,
                                    "description": getattr(obj, "__doc__", "") or "",
                                    "category": f.stem,
                                    "timeout": 60,
                                    "is_read_only": False,
                                })
                if tools:
                    return tools
        except Exception as e:
            logger.debug(f"Toolset manifest enumeration failed: {e}")

        # Static fallback — known hermes core tools
        return [
            {"name": "execute_code", "description": "Execute Python code via UDS PTC", "category": "core", "timeout": 120, "is_read_only": False},
            {"name": "read_file", "description": "Read file contents", "category": "core", "timeout": 30, "is_read_only": True},
            {"name": "write_file", "description": "Write file contents", "category": "core", "timeout": 30, "is_read_only": False},
            {"name": "bash", "description": "Run shell commands", "category": "core", "timeout": 60, "is_read_only": False},
            {"name": "web_search", "description": "Search the web", "category": "web", "timeout": 30, "is_read_only": True},
            {"name": "save_memory", "description": "Save to persistent memory", "category": "memory", "timeout": 10, "is_read_only": False},
            {"name": "recall_memory", "description": "Recall from persistent memory", "category": "memory", "timeout": 10, "is_read_only": True},
            {"name": "create_skill", "description": "Create reusable skill", "category": "skills", "timeout": 30, "is_read_only": False},
        ]

    async def run_task(
        self,
        prompt: str,
        *,
        toolsets: list[str] | None = None,
        max_iterations: int = 30,
        quiet_mode: bool = False,
        background_review: bool = False,
        memory_nudge_interval: int = 10,
        skill_nudge_interval: int = 10,
        extra_context: str = "",
    ) -> HermesResult:
        """
        Run a Hermes agent task in the thread pool.

        Args:
            prompt: The task description for the agent
            toolsets: Which tool sets to enable (core, web, terminal, etc.)
            max_iterations: Maximum tool call iterations
            quiet_mode: Suppress agent output
            background_review: Enable background review daemon
            memory_nudge_interval: Turns between memory reviews
            skill_nudge_interval: Iterations between skill reviews
            extra_context: Additional context to prepend
        """
        if not self._available:
            return HermesResult(
                success=False,
                output="",
                errors=["Hermes runtime not available"],
            )

        full_prompt = f"{extra_context}\n\n{prompt}" if extra_context else prompt

        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                self._executor,
                self._run_sync,
                full_prompt,
                toolsets or ["core"],
                max_iterations,
                quiet_mode,
                background_review,
                memory_nudge_interval,
                skill_nudge_interval,
            )
            return result
        except Exception as e:
            logger.error(f"Hermes task failed: {e}")
            return HermesResult(
                success=False,
                output="",
                errors=[str(e)],
            )

    def _run_sync(
        self,
        prompt: str,
        toolsets: list[str],
        max_iterations: int,
        quiet_mode: bool,
        background_review: bool,
        memory_nudge_interval: int,
        skill_nudge_interval: int,
    ) -> HermesResult:
        """Synchronous Hermes agent execution (runs in thread pool)."""
        try:
            agent = self._agent_class(
                model=self.model,
                enabled_toolsets=toolsets,
                max_iterations=max_iterations,
                quiet_mode=quiet_mode,
            )

            # Prefer chat() for simple string return, fall back to run_conversation
            if hasattr(agent, "chat"):
                raw_result = agent.chat(prompt)
            elif hasattr(agent, "run_conversation"):
                raw_result = agent.run_conversation(prompt)
            else:
                return HermesResult(
                    success=False,
                    output="",
                    errors=["AIAgent has no chat or run_conversation method"],
                )

            return self._parse_result(raw_result)

        except Exception as e:
            logger.error(f"Hermes sync execution failed: {e}")
            return HermesResult(
                success=False,
                output="",
                errors=[str(e)],
            )

    def _parse_result(self, raw: Any) -> HermesResult:
        """Parse raw Hermes agent result into HermesResult."""
        if isinstance(raw, str):
            return HermesResult(success=True, output=raw)

        if isinstance(raw, dict):
            return HermesResult(
                success=raw.get("success", True),
                output=raw.get("output", str(raw)),
                tool_calls=raw.get("tool_calls", []),
                iterations_used=raw.get("iterations_used", 0),
                skills_created=raw.get("skills_created", []),
                memories_saved=raw.get("memories_saved", []),
                errors=raw.get("errors", []),
            )

        # Fallback: convert to string
        return HermesResult(success=True, output=str(raw))

    async def run_background_review(
        self,
        conversation_context: str,
        review_prompt: str,
        max_iterations: int = 8,
    ) -> HermesResult:
        """
        Run a background review task (daemon-style).

        This is the Hermes _spawn_background_review() pattern:
        - Fork agent with same model/tools
        - Inject review prompt + conversation context
        - max_iterations=8, quiet_mode=True
        - Shared memory/skill store (writes persist immediately)
        """
        return await self.run_task(
            prompt=review_prompt,
            toolsets=["core"],
            max_iterations=max_iterations,
            quiet_mode=True,
            background_review=False,  # Don't nest reviews
            extra_context=f"## Recent Conversation Context\n\n{conversation_context}",
        )

    def shutdown(self):
        """Shutdown the thread pool."""
        self._executor.shutdown(wait=False)
        logger.info("Hermes runtime shut down")


class HermesDelegateTool:
    """
    Tier 1 tool that delegates tasks to Hermes AIAgent.

    Registered as `hermes_delegate` in the tool registry.
    """

    def __init__(self, hermes_runtime: HermesNativeRuntime):
        self._runtime = hermes_runtime
        from rragent.tools.base import ToolSpec
        self.spec = ToolSpec(
            name="hermes_delegate",
            description="将复杂任务委派给 Hermes Agent 执行（支持代码执行、文件操作、深度分析等）",
            input_schema={
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "要委派给 Hermes 的任务描述",
                    },
                    "toolsets": {
                        "type": "string",
                        "description": "启用的工具集，逗号分隔（默认: core）",
                    },
                    "max_iterations": {
                        "type": "integer",
                        "description": "最大迭代次数（默认: 30）",
                    },
                },
                "required": ["task"],
            },
            is_tier0=False,
            should_defer=True,
            is_concurrent_safe=False,
            timeout=300.0,
            category="agent",
            keywords=["hermes", "delegate", "委派", "代理", "子任务", "执行"],
        )

    async def call(self, input: dict) -> "ToolResult":
        from rragent.tools.base import ToolResult

        if not self._runtime.available:
            return ToolResult.error("Hermes runtime not available")

        task = input.get("task", "")
        if not task:
            return ToolResult.error("Missing required field: task")

        toolsets_str = input.get("toolsets", "core")
        toolsets = [t.strip() for t in toolsets_str.split(",")]
        max_iterations = input.get("max_iterations", 30)

        result = await self._runtime.run_task(
            prompt=task,
            toolsets=toolsets,
            max_iterations=max_iterations,
        )

        if result.success:
            return ToolResult.success(result.output)
        else:
            error_msg = "; ".join(result.errors) if result.errors else "Unknown error"
            return ToolResult.error(f"Hermes task failed: {error_msg}")

    def validate_input(self, input: dict):
        if "task" not in input:
            from rragent.tools.base import ToolResult
            return ToolResult.error("Missing required field: task")
        return None

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def schema_dict(self) -> dict:
        return {
            "name": self.spec.name,
            "description": self.spec.description,
            "input_schema": self.spec.input_schema,
        }

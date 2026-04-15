"""
Hooks System — PreToolUse / PostToolUse lifecycle hooks.

Reference: claude-code hooks system.

Hooks allow intercepting tool execution for:
- Logging and auditing
- Permission checks
- Result transformation
- Side effects (notifications, metrics)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine

from rragent.tools.base import ToolUse, ToolResult

logger = logging.getLogger("rragent.runtime.hooks")


class HookPhase(str, Enum):
    PRE_TOOL = "pre_tool"
    POST_TOOL = "post_tool"
    PRE_TURN = "pre_turn"
    POST_TURN = "post_turn"


@dataclass
class HookResult:
    """Result from running hooks."""

    allow: bool = True
    inject_message: str = ""
    modified_input: dict | None = None
    modified_result: ToolResult | None = None
    errors: list[str] = field(default_factory=list)


HookFn = Callable[[ToolUse, dict], Coroutine[Any, Any, HookResult]]
PostHookFn = Callable[[ToolUse, ToolResult, dict], Coroutine[Any, Any, HookResult]]


class HookRegistry:
    """
    Registry for lifecycle hooks.

    Hooks are called in registration order.
    PreToolUse hooks can modify input or deny execution.
    PostToolUse hooks can modify results or trigger side effects.

    Death spiral prevention: if skip_hooks is True (API error recovery),
    all hooks are skipped to prevent error -> hook -> error cycles.
    """

    def __init__(self):
        self._pre_tool_hooks: list[tuple[str, HookFn]] = []
        self._post_tool_hooks: list[tuple[str, PostHookFn]] = []
        self._pre_turn_hooks: list[tuple[str, Callable]] = []
        self._post_turn_hooks: list[tuple[str, Callable]] = []

    def register_pre_tool(self, name: str, hook: HookFn):
        """Register a pre-tool-use hook."""
        self._pre_tool_hooks.append((name, hook))

    def register_post_tool(self, name: str, hook: PostHookFn):
        """Register a post-tool-use hook."""
        self._post_tool_hooks.append((name, hook))

    def register_pre_turn(self, name: str, hook: Callable):
        """Register a pre-turn hook."""
        self._pre_turn_hooks.append((name, hook))

    def register_post_turn(self, name: str, hook: Callable):
        """Register a post-turn hook."""
        self._post_turn_hooks.append((name, hook))

    async def run_pre_tool(
        self,
        tool_use: ToolUse,
        context: dict | None = None,
    ) -> HookResult:
        """
        Run all pre-tool hooks.

        Returns combined result. If any hook denies, execution is denied.
        """
        combined = HookResult()
        ctx = context or {}

        for name, hook in self._pre_tool_hooks:
            try:
                result = await hook(tool_use, ctx)
                if not result.allow:
                    combined.allow = False
                    combined.inject_message = result.inject_message
                    return combined
                if result.modified_input is not None:
                    combined.modified_input = result.modified_input
                if result.inject_message:
                    combined.inject_message += result.inject_message + "\n"
            except Exception as e:
                combined.errors.append(f"Hook {name} error: {e}")
                logger.warning(f"Pre-tool hook {name} failed: {e}")

        return combined

    async def run_post_tool(
        self,
        tool_use: ToolUse,
        tool_result: ToolResult,
        context: dict | None = None,
    ) -> HookResult:
        """Run all post-tool hooks."""
        combined = HookResult()
        ctx = context or {}

        for name, hook in self._post_tool_hooks:
            try:
                result = await hook(tool_use, tool_result, ctx)
                if result.modified_result is not None:
                    combined.modified_result = result.modified_result
                if result.inject_message:
                    combined.inject_message += result.inject_message + "\n"
            except Exception as e:
                combined.errors.append(f"Hook {name} error: {e}")
                logger.warning(f"Post-tool hook {name} failed: {e}")

        return combined

    async def run_post_turn(self, context: dict | None = None) -> HookResult:
        """Run all post-turn hooks."""
        combined = HookResult()
        ctx = context or {}

        for name, hook in self._post_turn_hooks:
            try:
                result = await hook(ctx)
                if result.inject_message:
                    combined.inject_message += result.inject_message + "\n"
            except Exception as e:
                combined.errors.append(f"Hook {name} error: {e}")

        return combined


# Built-in hooks

async def logging_pre_hook(tool_use: ToolUse, context: dict) -> HookResult:
    """Log all tool calls."""
    logger.info(f"Tool call: {tool_use.name} (id={tool_use.id})")
    return HookResult()


async def logging_post_hook(
    tool_use: ToolUse,
    tool_result: ToolResult,
    context: dict,
) -> HookResult:
    """Log tool results."""
    status = "error" if tool_result.is_error else "success"
    content_preview = tool_result.content[:100] if tool_result.content else ""
    logger.info(f"Tool result: {tool_use.name} [{status}] {content_preview}")
    return HookResult()


async def metrics_post_hook(
    tool_use: ToolUse,
    tool_result: ToolResult,
    context: dict,
) -> HookResult:
    """Record tool execution metrics for Evolution Engine."""
    # This hook records execution events to Redis Stream
    # for consumption by the Evolution Engine
    evolution_engine = context.get("evolution_engine")
    if evolution_engine:
        latency = context.get("latency_ms", 0)
        await evolution_engine.record_execution(
            tool_name=tool_use.name,
            action="call",
            params=tool_use.input,
            result_summary=tool_result.content[:200] if tool_result.content else "",
            success=not tool_result.is_error,
            latency_ms=latency,
            session_id=context.get("session_id", ""),
        )
    return HookResult()


def create_default_hooks() -> HookRegistry:
    """Create hook registry with default hooks."""
    registry = HookRegistry()
    registry.register_pre_tool("logging", logging_pre_hook)
    registry.register_post_tool("logging", logging_post_hook)
    registry.register_post_tool("metrics", metrics_post_hook)
    return registry

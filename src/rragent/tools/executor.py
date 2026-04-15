"""
Tool Executor — runs tools with error containment + concurrency partitioning.

Key principles (from claude-code):
- Tool errors become tool_results, not exceptions
- LLM sees errors and self-corrects (up to 3 attempts)
- Large results persisted to disk with preview
- Concurrent-safe tools run in parallel, others serial
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from rragent.tools.base import Tool, ToolResult, ToolUse
from rragent.tools.registry import GlobalToolRegistry

logger = logging.getLogger("rragent.tools.executor")


class ToolExecutor:
    """Execute tools with error containment and result budgeting."""

    MAX_SELF_CORRECT_ATTEMPTS = 3

    def __init__(
        self,
        registry: GlobalToolRegistry,
        persist_dir: str = "~/.rragent/tool_results",
    ):
        self.registry = registry
        self.persist_dir = Path(os.path.expanduser(persist_dir))
        self.persist_dir.mkdir(parents=True, exist_ok=True)

    async def execute(self, tool_use: ToolUse) -> ToolResult:
        """
        Execute a single tool call with full error containment.

        Never raises — always returns a ToolResult.
        """
        tool = self.registry.get(tool_use.name)

        # Unknown tool
        if tool is None:
            available = self.registry.list_all_names()
            return ToolResult.error(
                f"Unknown tool: {tool_use.name}\n"
                f"Use tool_search to discover available tools.\n"
                f"Currently loaded: {', '.join(list(available)[:20])}"
            )

        try:
            # 1. Validate input
            validation_error = tool.validate_input(tool_use.input)
            if validation_error:
                return validation_error

            # 2. Execute with timeout
            result = await asyncio.wait_for(
                tool.call(tool_use.input),
                timeout=tool.spec.timeout,
            )

            # 3. Result budget — persist large results
            if len(result.content) > tool.spec.max_result_size:
                result = self._persist_large_result(tool_use, result)

            return result

        except asyncio.TimeoutError:
            return ToolResult.error(
                f"Tool '{tool_use.name}' timed out after {tool.spec.timeout}s. "
                f"Try with smaller input or a simpler approach."
            )
        except Exception as e:
            logger.error(f"Tool execution error [{tool_use.name}]: {e}", exc_info=True)
            return ToolResult.error(f"Error executing {tool_use.name}: {e}")

    async def execute_batch(
        self, tool_uses: list[ToolUse]
    ) -> list[tuple[ToolUse, ToolResult]]:
        """
        Execute a batch of tool calls with concurrency partitioning.

        Concurrent-safe tools run in parallel.
        Non-concurrent tools run sequentially after parallel batch.
        """
        concurrent = []
        sequential = []

        for tu in tool_uses:
            if self.registry.is_concurrent_safe(tu.name):
                concurrent.append(tu)
            else:
                sequential.append(tu)

        results: list[tuple[ToolUse, ToolResult]] = []

        # Run concurrent tools in parallel
        if concurrent:
            coro_results = await asyncio.gather(
                *[self.execute(tu) for tu in concurrent],
                return_exceptions=True,
            )
            for tu, r in zip(concurrent, coro_results):
                if isinstance(r, Exception):
                    r = ToolResult.error(f"Unexpected error: {r}")
                results.append((tu, r))

        # Run sequential tools one by one
        for tu in sequential:
            r = await self.execute(tu)
            results.append((tu, r))

        return results

    def _persist_large_result(self, tool_use: ToolUse, result: ToolResult) -> ToolResult:
        """Persist a large result to disk and return a preview."""
        filename = f"{tool_use.name}_{tool_use.id}_{int(time.time())}.txt"
        path = self.persist_dir / filename
        path.write_text(result.content, encoding="utf-8")

        preview = result.content[:2000]
        return ToolResult(
            content=(
                f"Result too large ({len(result.content)} chars). "
                f"Saved to {path}.\n\nPreview:\n{preview}"
            ),
            metadata={"persisted_path": str(path), "original_size": len(result.content)},
        )

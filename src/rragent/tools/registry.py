"""
Global Tool Registry — manages all tools across tiers.

Tier 0: Always loaded (schema in every LLM call)
Tier 1: Deferred (name+description indexed, schema via tool_search)
Tier 2: External (MCP tools, discovered dynamically)
"""

from __future__ import annotations

import logging
from typing import Any

from rragent.tools.base import Tool, ToolSpec, ToolResult, ToolUse

logger = logging.getLogger("rragent.tools.registry")


class ToolIndex:
    """Lightweight index entry for deferred tools."""

    def __init__(
        self,
        name: str,
        description: str,
        keywords: list[str],
        agent: str = "",
        category: str = "general",
        timeout: float = 30.0,
        is_concurrent_safe: bool = True,
    ):
        self.name = name
        self.description = description
        self.keywords = keywords
        self.agent = agent
        self.category = category
        self.timeout = timeout
        self.is_concurrent_safe = is_concurrent_safe


class GlobalToolRegistry:
    """
    Central registry for all RRAgent tools.

    Tools are organized in tiers:
    - tier0: Tool instances, always available
    - tier1_index: ToolIndex entries, schema loaded on demand
    - tier1_tools: Tool instances loaded by tool_search
    - session_cache: Tools discovered during current session
    """

    def __init__(self):
        self._tier0: dict[str, Tool] = {}
        self._tier1_index: list[ToolIndex] = []
        self._tier1_tools: dict[str, Tool] = {}
        self._session_cache: dict[str, Tool] = {}
        self._all_tools: dict[str, Tool] = {}

    def register_tier0(self, tool: Tool):
        """Register a Tier 0 tool (always loaded)."""
        self._tier0[tool.name] = tool
        self._all_tools[tool.name] = tool
        logger.debug(f"Tier 0 tool registered: {tool.name}")

    def register_tier1(self, tool: Tool, index: ToolIndex | None = None):
        """Register a Tier 1 tool (deferred loading)."""
        self._tier1_tools[tool.name] = tool
        self._all_tools[tool.name] = tool
        if index:
            self._tier1_index.append(index)
        logger.debug(f"Tier 1 tool registered: {tool.name}")

    def add_index(self, index: ToolIndex):
        """Add an index entry for a deferred tool."""
        self._tier1_index.append(index)

    def get(self, name: str) -> Tool | None:
        """Look up a tool by name (any tier)."""
        return (
            self._tier0.get(name)
            or self._session_cache.get(name)
            or self._tier1_tools.get(name)
        )

    def discover(self, name: str) -> Tool | None:
        """Load a deferred tool into session cache."""
        tool = self._tier1_tools.get(name)
        if tool:
            self._session_cache[name] = tool
            return tool
        return None

    def is_concurrent_safe(self, name: str) -> bool:
        """Check if a tool can be run concurrently."""
        tool = self.get(name)
        if tool:
            return tool.spec.is_concurrent_safe
        # Check index
        for idx in self._tier1_index:
            if idx.name == name:
                return idx.is_concurrent_safe
        return True  # default safe

    def get_tier0_schemas(self) -> list[dict[str, Any]]:
        """Get schemas for all Tier 0 tools (for LLM prompt)."""
        return [t.schema_dict for t in self._tier0.values()]

    def get_session_schemas(self) -> list[dict[str, Any]]:
        """Get schemas for session-discovered tools."""
        return [t.schema_dict for t in self._session_cache.values()]

    def get_all_active_schemas(self) -> list[dict[str, Any]]:
        """Get all currently callable tool schemas."""
        schemas = self.get_tier0_schemas()
        schemas.extend(self.get_session_schemas())
        return schemas

    @property
    def tier0_tools(self) -> dict[str, Tool]:
        return self._tier0

    @property
    def tier1_index(self) -> list[ToolIndex]:
        return self._tier1_index

    def clear_session_cache(self):
        """Clear session-discovered tools (new conversation)."""
        self._session_cache.clear()

    def search(self, query: str, max_results: int = 5) -> list[ToolIndex]:
        """Search tier1 index by keywords (used by ToolSearchTool)."""
        query_terms = query.lower().split()
        scored: list[tuple[float, ToolIndex]] = []

        for idx in self._tier1_index:
            score = 0.0
            for term in query_terms:
                if term in [k.lower() for k in idx.keywords]:
                    score += 3.0
                if term in idx.description.lower():
                    score += 1.5
                if term in idx.category.lower() or term in idx.agent.lower():
                    score += 1.0
            if score > 0:
                scored.append((score, idx))

        scored.sort(key=lambda x: -x[0])
        return [idx for _, idx in scored[:max_results]]

    def list_all_names(self) -> list[str]:
        """List all registered tool names."""
        names = set(self._tier0.keys())
        names.update(self._tier1_tools.keys())
        names.update(idx.name for idx in self._tier1_index)
        return sorted(names)

    def stats(self) -> dict[str, int]:
        return {
            "tier0": len(self._tier0),
            "tier1_indexed": len(self._tier1_index),
            "tier1_loaded": len(self._tier1_tools),
            "session_cached": len(self._session_cache),
        }

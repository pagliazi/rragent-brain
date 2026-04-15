"""
ToolSearch — lazy tool discovery (inspired by claude-code ToolSearch.ts).

Only Tier 0 tools have schemas in every LLM call.
LLM uses tool_search to discover and load Tier 1+ tools on demand.
Three-layer matching: keyword exact → description substring → category.
"""

from __future__ import annotations

import json
from typing import Any

from rragent.tools.base import Tool, ToolSpec, ToolResult
from rragent.tools.registry import GlobalToolRegistry, ToolIndex


class ToolSearchTool(Tool):
    """Tier 0 tool that discovers and loads deferred tools."""

    spec = ToolSpec(
        name="tool_search",
        description=(
            "搜索可用工具。输入关键词（中文或英文），返回匹配工具的完整参数说明。"
            "搜索后工具即可直接调用。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词，如 '回测'、'backtest'、'涨停板'",
                },
                "max_results": {
                    "type": "integer",
                    "default": 5,
                    "description": "最大返回数量",
                },
            },
            "required": ["query"],
        },
        is_tier0=True,
        should_defer=False,
        is_concurrent_safe=True,
        timeout=5,
        category="meta",
    )

    def __init__(self, registry: GlobalToolRegistry):
        self.registry = registry

    async def call(self, input: dict[str, Any]) -> ToolResult:
        query = input.get("query", "")
        max_results = input.get("max_results", 5)

        if not query:
            return ToolResult.error("Please provide a search query.")

        # Score all indexed tools
        scored: list[tuple[float, ToolIndex]] = []
        for idx in self.registry.tier1_index:
            score = self._match_score(query, idx)
            if score > 0:
                scored.append((score, idx))

        scored.sort(key=lambda x: -x[0])
        matches = [idx for _, idx in scored[:max_results]]

        if not matches:
            categories = set()
            for idx in self.registry.tier1_index:
                categories.add(idx.category)
            return ToolResult(
                content=(
                    f"没有找到与 '{query}' 匹配的工具。\n"
                    f"可用分类: {', '.join(sorted(categories))}\n"
                    f"共 {len(self.registry.tier1_index)} 个工具可搜索。"
                )
            )

        # Load matched tools into session cache and format results
        results = []
        for idx in matches:
            tool = self.registry.discover(idx.name)
            if tool:
                results.append(self._format_tool(idx, tool))
            else:
                results.append(self._format_index(idx))

        header = f"找到 {len(results)} 个工具匹配 '{query}':\n"
        return ToolResult.success(header + "\n\n---\n\n".join(results))

    def _match_score(self, query: str, idx: ToolIndex) -> float:
        """Three-layer matching: keyword (3.0) → description (1.5) → category (1.0)."""
        score = 0.0
        terms = query.lower().split()

        for term in terms:
            # Layer 1: exact keyword match
            if term in [k.lower() for k in idx.keywords]:
                score += 3.0
            # Layer 2: description substring
            elif term in idx.description.lower():
                score += 1.5
            # Layer 3: category/agent match
            elif term in idx.category.lower() or term in idx.agent.lower():
                score += 1.0

        return score

    def _format_tool(self, idx: ToolIndex, tool: Tool) -> str:
        schema_str = json.dumps(tool.spec.input_schema, ensure_ascii=False, indent=2)
        return (
            f"**{idx.name}**\n"
            f"  描述: {idx.description}\n"
            f"  分类: {idx.category} | Agent: {idx.agent}\n"
            f"  超时: {idx.timeout}s | 并发安全: {idx.is_concurrent_safe}\n"
            f"  参数:\n```json\n{schema_str}\n```"
        )

    def _format_index(self, idx: ToolIndex) -> str:
        return (
            f"**{idx.name}**\n"
            f"  描述: {idx.description}\n"
            f"  分类: {idx.category} | Agent: {idx.agent}\n"
            f"  (工具未加载，请重试)"
        )

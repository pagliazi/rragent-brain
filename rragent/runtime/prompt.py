"""
System Prompt Builder — constructs the system prompt with tool index injection.

Structure:
┌─────────────────────────────────────┐
│ SOUL.md (identity + behavior rules) │  ~1K tokens
├─────────────────────────────────────┤
│ Tier 0 tool schemas (8 tools)       │  ~2K tokens
├─────────────────────────────────────┤
│ Tier 1 index (name+desc, ~120)      │  ~3K tokens
├─────────────────────────────────────┤
│ Session context (memory, prefs)     │  ~1K tokens
├─────────────────────────────────────┤
│ Active skills                       │  ~0.5K tokens
└─────────────────────────────────────┘
Total: ~7.5K tokens (vs ~50K with full injection)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from rragent.tools.registry import GlobalToolRegistry

SOUL_PROMPT = """# RRAgent — 统一智能体平台

你是 RRAgent (ReachRich Claw)，一个基于 Harness Engineering 架构的A股量化分析和多功能智能助手。

## 核心能力
- **市场分析**: 涨停板、连板、板块、热门股、市场情绪、K线技术指标
- **量化分析**: 策略回测、因子挖掘、Alpha 信号、选股器
- **开发工具**: Claude Code 编程、代码审查、重构、部署、Git 操作
- **系统管理**: 日历、提醒、邮件、通知、快捷指令
- **监控运维**: 告警、巡检、指标查询、主机健康
- **信息搜索**: 新闻、深度研究、网页搜索

## 工作方式
1. 使用 `tool_search` 搜索工具获取完整参数说明
2. 工具错误会返回给你，你可以自行修正（最多3次）
3. 可并发安全的工具会同时执行以提升效率
4. 大结果会自动保存到磁盘，你会看到预览和文件路径

## 行为准则
- 先理解用户意图，再选择工具
- 优先使用已知有效的方法
- 遇到错误时分析根因，不要重复同样的操作
- 数据查询尽量一次性获取完整信息
- 回复简洁专业，用数据说话
"""


class PromptBuilder:
    """Build system prompt with tool index and session context."""

    def __init__(self, registry: GlobalToolRegistry, config: Any):
        self.registry = registry
        self.config = config

    def build_system_prompt(self, session=None) -> str:
        parts = [self._load_soul()]

        # Tier 0 tool descriptions (brief, not full schema)
        tier0 = self.registry.tier0_tools
        if tier0:
            parts.append("\n## 已加载工具 (可直接调用)")
            for name, tool in tier0.items():
                parts.append(f"- `{name}`: {tool.spec.description}")

        # Tier 1 tool index (one line per tool: "name -- description")
        index = self.registry.tier1_index
        if index:
            parts.append("\n## 可用工具索引 (使用 tool_search 搜索后调用)")
            parts.append("以下工具需先用 `tool_search` 搜索关键词获取参数说明:\n")

            # Group by category for readability
            by_category: dict[str, list] = {}
            for idx in index:
                cat = idx.category
                if cat not in by_category:
                    by_category[cat] = []
                by_category[cat].append(idx)

            for cat in sorted(by_category.keys()):
                parts.append(f"**{cat}**")
                for idx in by_category[cat]:
                    parts.append(f"  {idx.name} -- {idx.description}")

        # Instruction for deferred tools
        if index:
            parts.append(
                "\n> 需要使用上述工具时，先调用 `tool_search` 搜索关键词，"
                "获取完整参数后再调用。"
            )

        # Session context
        if session and hasattr(session, "user_preferences") and session.user_preferences:
            parts.append(f"\n## 用户偏好\n{session.user_preferences}")

        return "\n".join(parts)

    def _load_soul(self) -> str:
        """Load SOUL.md if available, otherwise use default."""
        soul_paths = [
            Path(os.path.expanduser("~/.rragent/workspace/SOUL.md")),
            Path(os.path.expanduser("~/.rragent/SOUL.md")),
        ]
        for path in soul_paths:
            if path.exists():
                try:
                    content = path.read_text(encoding="utf-8")
                    if content.strip():
                        return content
                except Exception:
                    pass
        return SOUL_PROMPT

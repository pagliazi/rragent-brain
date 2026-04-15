"""
Auto-generate tool registry from skills YAML + PYAGENT_COMMANDS.

Reads *.yaml from the skills directory, creates ToolIndex + PyAgentTool
for each skill, and registers them with appropriate tiers.

Tier 0 (always loaded):
  - tool_search (meta-tool)
  - pyagent_market_data (get_all_raw)
  - pyagent_analysis_ask (ask)

Tier 1 (deferred, index only):
  - Everything else from PYAGENT_COMMANDS + YAML skills
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from rragent.tools.base import ToolSpec
from rragent.tools.registry import GlobalToolRegistry, ToolIndex
from rragent.tools.search import ToolSearchTool
from rragent.tools.builtin.factor_tools import FactorMineTool, FactorEvaluateTool, FactorCombineTool, FactorListTool, StrategyBacktestTool
from rragent.tools.pyagent.bridge import PyAgentBridge, PyAgentTool, PYAGENT_COMMANDS

logger = logging.getLogger("rragent.tools.index_builder")

# Tools promoted to Tier 0 (always in prompt)
TIER0_COMMANDS = {"summary", "ask"}

# Extra Tier 0 tools not in PYAGENT_COMMANDS (custom-registered like P0)
# Factor Mining & Quant Tools (Tier 0 — core quant capabilities)
QUANT_TOOLS = [
    {
        "command": "factor_mine",
        "agent": "orchestrator",
        "action": "digger",
        "description": "挖掘新的 Alpha 因子。指定挖掘轮数和每轮因子数量。返回发现的因子列表及其 Sharpe/IC/IR 指标。",
        "timeout": 600,
        "input_schema": {
            "type": "object",
            "properties": {
                "rounds": {"type": "integer", "default": 3, "description": "挖掘轮数"},
                "factors": {"type": "integer", "default": 5, "description": "每轮生成因子数"},
            },
        },
    },
    {
        "command": "factor_evaluate",
        "agent": "backtest",
        "action": "quant_validate",
        "description": "评估因子质量：计算 Sharpe、IC、IR、胜率、最大回撤。自动进行 PBO 交叉验证检测过拟合。",
        "timeout": 300,
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "因子 Python 代码"},
                "start_date": {"type": "string", "default": "", "description": "回测起始日期 (YYYY-MM-DD)"},
                "end_date": {"type": "string", "default": "", "description": "回测结束日期"},
            },
            "required": ["code"],
        },
    },
    {
        "command": "factor_combine",
        "agent": "orchestrator",
        "action": "combine",
        "description": "智能融合多个因子。从因子库选取表现最好的因子进行加权/乘法/排名融合，生成新的组合因子。",
        "timeout": 600,
        "input_schema": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "default": 2, "description": "融合因子数量"},
                "mode": {"type": "string", "default": "smart", "description": "融合模式: smart/add/multiply/rank"},
            },
        },
    },
    {
        "command": "factor_list",
        "agent": "backtest",
        "action": "list_ledger",
        "description": "查看因子库：列出所有活跃因子及其 Sharpe、胜率、评级。",
        "timeout": 15,
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "command": "strategy_backtest",
        "agent": "backtest",
        "action": "run_backtest",
        "description": "执行策略回测：支持 backtrader 和 vectorbt 引擎。输入策略代码和标的，返回收益曲线、夏普、最大回撤。",
        "timeout": 300,
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "策略 Python 代码"},
                "stock": {"type": "string", "default": "000001.SZ", "description": "回测标的代码"},
                "start_date": {"type": "string", "default": "2025-01-01", "description": "起始日期"},
                "end_date": {"type": "string", "default": "2026-01-01", "description": "结束日期"},
                "mode": {"type": "string", "default": "backtrader", "description": "引擎: backtrader/vectorbt"},
            },
            "required": ["code"],
        },
    },
    {
        "command": "factor_screen",
        "agent": "orchestrator",
        "action": "screen",
        "description": "使用因子进行选股筛选。输入选股条件 DSL，返回符合条件的股票列表。",
        "timeout": 60,
        "input_schema": {
            "type": "object",
            "properties": {
                "dsl": {"type": "string", "description": "选股 DSL 条件，如 'pct_chg > 5 AND volume_ratio > 3'"},
                "limit": {"type": "integer", "default": 20, "description": "返回数量"},
            },
            "required": ["dsl"],
        },
    },
]

EXTRA_TIER0_TOOLS = [
    {
        "command": "market_data",
        "agent": "market",
        "action": "get_all_raw",
        "description": "获取 A 股全市场行情数据（涨停板、板块概念、热门股票等）",
        "timeout": 20,
        "keywords": ["行情", "市场", "数据", "涨停", "板块", "热门"],
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "command": "analysis_ask",
        "agent": "analysis",
        "action": "ask",
        "description": "让分析 Agent 回答市场分析问题",
        "timeout": 180,
        "keywords": ["分析", "analysis", "研判", "市场"],
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "分析问题"},
            },
            "required": ["question"],
        },
        "concurrent": False,
    },
]


def _build_input_schema_from_yaml(skill: dict) -> dict[str, Any]:
    """Convert YAML params definition to JSON Schema."""
    params = skill.get("params")
    if not params or not isinstance(params, dict):
        return {"type": "object", "properties": {}, "required": []}

    properties: dict[str, Any] = {}
    required: list[str] = []

    for name, spec in params.items():
        if not isinstance(spec, dict):
            # Simple type like "params: {}" or string value
            continue
        prop: dict[str, Any] = {}
        ptype = spec.get("type", "string")
        type_map = {
            "string": "string",
            "str": "string",
            "int": "integer",
            "integer": "integer",
            "float": "number",
            "number": "number",
            "bool": "boolean",
            "boolean": "boolean",
            "dict": "object",
            "object": "object",
            "list": "array",
            "array": "array",
        }
        prop["type"] = type_map.get(str(ptype), "string")

        desc = spec.get("desc", spec.get("description", ""))
        if desc:
            prop["description"] = desc
        if "default" in spec:
            prop["default"] = spec["default"]

        properties[name] = prop
        if spec.get("required", False):
            required.append(name)

    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _extract_keywords_from_yaml(skill: dict, agent_name: str) -> list[str]:
    """Extract search keywords from a YAML skill definition."""
    keywords = []
    name = skill.get("name", "")
    desc = skill.get("description", "")

    # Add skill name parts
    if name:
        keywords.append(name)
        # Split underscores
        keywords.extend(p for p in name.split("_") if len(p) > 1)

    # Extract Chinese keywords from description (short segments)
    if desc:
        # Add whole description words that are useful
        for word in desc.replace("—", " ").replace("(", " ").replace(")", " ").split():
            if len(word) >= 2 and len(word) <= 6:
                keywords.append(word)

    keywords.append(agent_name)
    return list(set(keywords))


def _load_skills_from_yaml(skills_dir: str) -> list[dict[str, Any]]:
    """Load all skill definitions from YAML files."""
    skills_path = Path(skills_dir)
    if not skills_path.exists():
        logger.warning(f"Skills directory not found: {skills_dir}")
        return []

    all_skills = []
    for yaml_file in sorted(skills_path.glob("*.yaml")):
        try:
            with open(yaml_file, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if not data or "skills" not in data:
                continue

            agent_name = data.get("agent", yaml_file.stem.replace("_skills", ""))
            agent_desc = data.get("description", "")

            for skill in data["skills"]:
                skill_entry = {
                    "agent": agent_name,
                    "agent_description": agent_desc,
                    "name": skill.get("name", ""),
                    "description": skill.get("description", ""),
                    "params": skill.get("params", {}),
                    "returns": skill.get("returns", ""),
                    "trigger": skill.get("trigger", ""),
                }
                all_skills.append(skill_entry)

        except Exception as e:
            logger.warning(f"Failed to parse {yaml_file}: {e}")

    logger.info(f"Loaded {len(all_skills)} skills from {skills_dir}")
    return all_skills


def build_tool_registry(
    bridge: PyAgentBridge,
    skills_dir: str = "",
    hermes_runtime: "Any | None" = None,
) -> GlobalToolRegistry:
    """
    Build a complete GlobalToolRegistry from PYAGENT_COMMANDS + skills YAML.

    1. Register ToolSearchTool as Tier 0
    2. Register PYAGENT_COMMANDS (some as Tier 0, rest as Tier 1)
    3. Enrich Tier 1 index with YAML skill metadata (better descriptions, schemas)

    Args:
        bridge: PyAgentBridge for creating PyAgentTool instances
        skills_dir: Path to skills YAML directory (optional, for enrichment)

    Returns:
        Fully populated GlobalToolRegistry
    """
    registry = GlobalToolRegistry()
    tier0_count = 0
    tier1_count = 0

    # ── Step 0: Register extra Tier 0 tools (market_data, analysis_ask) ──
    for extra in EXTRA_TIER0_TOOLS:
        tool = PyAgentTool(
            command=extra["command"],
            agent=extra["agent"],
            action=extra["action"],
            description=extra["description"],
            timeout=extra.get("timeout", 30),
            bridge=bridge,
            input_schema=extra.get("input_schema"),
            keywords=extra.get("keywords", []),
            is_concurrent_safe=extra.get("concurrent", True),
        )
        registry.register_tier0(tool)
        tier0_count += 1

    # ── Step 0b: Register Factor/Quant tools as Tier 0 (direct implementation) ──
    registry.register_tier0(FactorMineTool())
    registry.register_tier0(FactorEvaluateTool(bridge=bridge))
    registry.register_tier0(FactorCombineTool())
    registry.register_tier0(FactorListTool())
    registry.register_tier0(StrategyBacktestTool())
    tier0_count += 5
    # strategy_backtest and factor_screen still go through PyAgent
    for qt in QUANT_TOOLS:
        if qt["command"] in ("factor_screen",):
            tool = PyAgentTool(
                command=qt["command"], agent=qt["agent"], action=qt["action"],
                description=qt["description"], timeout=qt.get("timeout", 30),
                bridge=bridge, input_schema=qt.get("input_schema"),
            )
            registry.register_tier0(tool)
            tier0_count += 1

    # ── Step 1: Register all PYAGENT_COMMANDS ──
    yaml_skills = _load_skills_from_yaml(skills_dir) if skills_dir else []

    # Build lookup: (agent, action) -> yaml skill
    yaml_lookup: dict[tuple[str, str], dict] = {}
    for skill in yaml_skills:
        key = (skill["agent"], skill["name"])
        yaml_lookup[key] = skill

    for cmd in PYAGENT_COMMANDS:
        command = cmd["command"]
        agent = cmd["agent"]
        action = cmd["action"]

        # Check if we have YAML enrichment
        yaml_skill = yaml_lookup.get((agent, action))

        # Build input schema: prefer YAML if available, else default
        if yaml_skill:
            input_schema = _build_input_schema_from_yaml(yaml_skill)
            description = yaml_skill.get("description") or cmd["description"]
            extra_keywords = _extract_keywords_from_yaml(yaml_skill, agent)
        else:
            input_schema = None  # PyAgentTool will use default
            description = cmd["description"]
            extra_keywords = []

        # Merge keywords
        base_keywords = cmd.get("keywords", [command])
        all_keywords = list(set(base_keywords + extra_keywords))

        tool = PyAgentTool(
            command=command,
            agent=agent,
            action=action,
            description=description,
            timeout=cmd.get("timeout", 30),
            bridge=bridge,
            input_schema=input_schema if input_schema and input_schema["properties"] else None,
            aliases=cmd.get("aliases", []),
            keywords=all_keywords,
            is_concurrent_safe=cmd.get("concurrent", True),
        )

        index = ToolIndex(
            name=tool.spec.name,
            description=description,
            keywords=all_keywords,
            agent=agent,
            category=tool.spec.category,
            timeout=cmd.get("timeout", 30),
            is_concurrent_safe=cmd.get("concurrent", True),
        )

        if command in TIER0_COMMANDS:
            registry.register_tier0(tool)
            tier0_count += 1
        else:
            registry.register_tier1(tool, index)
            tier1_count += 1

    # ── Step 2: Add YAML-only skills (not in PYAGENT_COMMANDS) as index entries ──
    registered_actions = {(c["agent"], c["action"]) for c in PYAGENT_COMMANDS}
    yaml_only_count = 0

    for skill in yaml_skills:
        key = (skill["agent"], skill["name"])
        if key in registered_actions:
            continue  # Already registered via PYAGENT_COMMANDS

        # Create index-only entry (no PyAgentTool, since no command mapping)
        tool_name = f"pyagent_{skill['agent']}_{skill['name']}"
        keywords = _extract_keywords_from_yaml(skill, skill["agent"])
        description = skill.get("description", skill["name"])

        # Still create a PyAgentTool so it can be discovered and called
        tool = PyAgentTool(
            command=f"{skill['agent']}_{skill['name']}",
            agent=skill["agent"],
            action=skill["name"],
            description=description,
            timeout=30,
            bridge=bridge,
            input_schema=_build_input_schema_from_yaml(skill) or None,
            keywords=keywords,
        )

        index = ToolIndex(
            name=tool.spec.name,
            description=description,
            keywords=keywords,
            agent=skill["agent"],
            category=PyAgentTool._infer_category(skill["agent"]),
        )

        registry.register_tier1(tool, index)
        yaml_only_count += 1

    # ── Step 3b: Register new quant research/evolution tools as Tier 1 ──
    try:
        from rragent.tools.builtin.quant_tools import (
            FactorResearchTool,
            BacktestAnalysisTool,
            StrategyOptimizeTool,
            FactorEvolveTool,
            FactorCompareTool,
        )
        quant_new_tools = [
            FactorResearchTool(),
            BacktestAnalysisTool(),
            StrategyOptimizeTool(),
            FactorEvolveTool(),
            FactorCompareTool(),
        ]
        for qt in quant_new_tools:
            idx = ToolIndex(
                name=qt.spec.name,
                description=qt.spec.description,
                keywords=qt.spec.keywords,
                agent="quant",
                category=qt.spec.category,
                timeout=qt.spec.timeout,
                is_concurrent_safe=qt.spec.is_concurrent_safe,
            )
            registry.register_tier1(qt, idx)
            tier1_count += 1
        logger.info(f"Quant research tools registered: {len(quant_new_tools)} Tier 1")
    except Exception as e:
        logger.warning(f"Failed to register quant tools: {e}")

    # ── Step 3: Register hermes_delegate as Tier 1 tool ──
    if hermes_runtime is not None:
        try:
            from rragent.tools.hermes.runtime import HermesDelegateTool
            hermes_tool = HermesDelegateTool(hermes_runtime)
            hermes_index = ToolIndex(
                name="hermes_delegate",
                description="将复杂任务委派给 Hermes Agent 执行（代码执行、文件操作、深度分析）",
                keywords=["hermes", "delegate", "委派", "代理", "子任务", "执行"],
                agent="hermes",
                category="agent",
                timeout=300,
                is_concurrent_safe=False,
            )
            registry.register_tier1(hermes_tool, hermes_index)
            tier1_count += 1
            logger.info("Hermes delegate tool registered as Tier 1")
        except Exception as e:
            logger.warning(f"Failed to register hermes_delegate: {e}")

    # ── Step 4: Register ToolSearchTool as Tier 0 (last, so it sees the full index) ──
    search_tool = ToolSearchTool(registry)
    registry.register_tier0(search_tool)
    tier0_count += 1

    logger.info(
        f"Tool registry built: {tier0_count} tier0, {tier1_count} tier1 "
        f"(+{yaml_only_count} YAML-only)"
    )
    return registry

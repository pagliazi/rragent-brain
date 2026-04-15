"""
P1 component tests — no Redis/LLM needed.

Test 1: tool_search("回测") should return backtest tools
Test 2: System prompt token count < 8K
Test 3: Context engine layers work correctly
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from rragent.tools.registry import GlobalToolRegistry, ToolIndex
from rragent.tools.search import ToolSearchTool
from rragent.tools.pyagent.bridge import PyAgentBridge, PyAgentTool, PYAGENT_COMMANDS
from rragent.tools.index_builder import build_tool_registry
from rragent.runtime.prompt import PromptBuilder
from rragent.runtime.config import RRClawConfig
from rragent.context.engine import ContextEngine
from rragent.runtime.session import Session

SKILLS_DIR = os.path.expanduser(
    "~/RRAgent-Universe/rragent-brain/agents/skills"
)


class FakeBridge:
    """Fake bridge that doesn't connect to Redis."""
    async def call_agent(self, *a, **kw):
        return {"result": "fake"}

    @property
    def is_connected(self):
        return False


def test_1_tool_search():
    """Test: tool_search("回测") should return backtest tools."""
    print("=" * 60)
    print("Test 1: tool_search('回测')")
    print("=" * 60)

    bridge = FakeBridge()
    registry = build_tool_registry(bridge, SKILLS_DIR)

    stats = registry.stats()
    print(f"Registry stats: {stats}")

    # Search for backtest
    results = registry.search("回测")
    print(f"\nSearch '回测' returned {len(results)} results:")
    for idx in results:
        print(f"  - {idx.name}: {idx.description} [keywords: {idx.keywords}]")

    assert len(results) > 0, "Should find at least 1 backtest tool"
    assert any("backtest" in r.name for r in results), "Should find backtest tool"

    # Test via ToolSearchTool
    search_tool = ToolSearchTool(registry)
    result = asyncio.run(search_tool.call({"query": "回测"}))
    print(f"\nToolSearchTool result:\n{result.content[:500]}")
    assert not result.is_error
    assert "回测" in result.content or "backtest" in result.content

    # Also test English
    results_en = registry.search("backtest")
    print(f"\nSearch 'backtest' returned {len(results_en)} results:")
    for idx in results_en:
        print(f"  - {idx.name}: {idx.description}")
    assert len(results_en) > 0

    print("\n[PASS] Test 1")


def test_2_system_prompt():
    """Test: system prompt token count < 8K."""
    print("\n" + "=" * 60)
    print("Test 2: System prompt token count")
    print("=" * 60)

    bridge = FakeBridge()
    registry = build_tool_registry(bridge, SKILLS_DIR)
    config = RRClawConfig.from_file()
    builder = PromptBuilder(registry, config)

    prompt = builder.build_system_prompt()
    char_count = len(prompt)
    # CJK-heavy: ~3 chars per token
    token_estimate = char_count // 3

    print(f"System prompt: {char_count} chars, ~{token_estimate} tokens")
    print(f"First 500 chars:\n{prompt[:500]}")
    print(f"\nLast 300 chars:\n{prompt[-300:]}")

    # Count tool entries
    tier0_count = len(registry.tier0_tools)
    tier1_count = len(registry.tier1_index)
    print(f"\nTier 0 tools: {tier0_count}")
    print(f"Tier 1 index entries: {tier1_count}")

    assert token_estimate < 8000, f"Prompt too large: ~{token_estimate} tokens > 8K"
    print(f"\n[PASS] Test 2 — ~{token_estimate} tokens < 8K")


def test_3_context_engine():
    """Test: context engine 5-layer compression."""
    print("\n" + "=" * 60)
    print("Test 3: Context engine layers")
    print("=" * 60)

    bridge = FakeBridge()
    registry = build_tool_registry(bridge, SKILLS_DIR)
    config = RRClawConfig.from_file()
    engine = ContextEngine(config, registry)

    # Test Layer 1: tool result budget
    messages = [
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "x" * 15000}
        ]}
    ]
    result = engine._apply_tool_result_budget(messages)
    content = result[0]["content"][0]["content"]
    assert len(content) < 15000, "Should truncate"
    assert "truncated" in content
    print("  Layer 1 (tool result budget): OK")

    # Test Layer 2: history snip
    messages = [{"role": "user", "content": f"msg{i}"} for i in range(30)]
    result = engine._apply_history_snip(messages)
    assert len(result) == 13  # 2 + 1 marker + 10
    assert "已省略" in result[2]["content"]
    print("  Layer 2 (history snip): OK")

    # Test Layer 3: microcompact
    messages = []
    for i in range(20):
        messages.append({"role": "user", "content": f"question {i}"})
        messages.append({"role": "assistant", "content": [
            {"type": "text", "text": f"answer {i} " * 20}
        ]})
    result = engine._apply_microcompact(messages)
    assert len(result) <= len(messages)
    print(f"  Layer 3 (microcompact): {len(messages)} -> {len(result)} messages")

    # Test Layer 4: context collapse
    # Create messages with lots of content to exceed 150K tokens
    big_messages = []
    for i in range(100):
        big_messages.append({"role": "user", "content": "长文本 " * 2000})
        big_messages.append({"role": "assistant", "content": [
            {"type": "text", "text": "回复 " * 2000}
        ]})
    estimated = engine._estimate_tokens(big_messages)
    print(f"  Layer 4 pre-collapse: ~{estimated} tokens")
    result = engine._apply_context_collapse(big_messages)
    if estimated > 150000:
        assert len(result) < len(big_messages)
        print(f"  Layer 4 (context collapse): {len(big_messages)} -> {len(result)} messages")
    else:
        print(f"  Layer 4: no collapse needed ({estimated} < 150K)")

    print("\n[PASS] Test 3")


def test_4_full_integration():
    """Test: full prepare() call with ContextEngine."""
    print("\n" + "=" * 60)
    print("Test 4: Full ContextEngine.prepare()")
    print("=" * 60)

    bridge = FakeBridge()
    registry = build_tool_registry(bridge, SKILLS_DIR)
    config = RRClawConfig.from_file()
    engine = ContextEngine(config, registry)

    session = Session(session_id="test-p1")
    session.append_user("帮我回测突破20日均线策略")

    result = asyncio.run(engine.prepare(session))

    assert "messages" in result
    assert "system_prompt" in result
    assert "tools" in result
    assert "model" in result

    print(f"  Messages: {len(result['messages'])}")
    print(f"  System prompt: {len(result['system_prompt'])} chars")
    print(f"  Tools: {len(result['tools'])} schemas")
    print(f"  Model: {result['model']}")

    # Verify tool_search is in the tools list
    tool_names = [t["name"] for t in result["tools"]]
    assert "tool_search" in tool_names, f"tool_search not in tools: {tool_names}"
    print(f"  Tool names: {tool_names}")

    # Clean up test session file
    try:
        os.unlink(session._path())
    except Exception:
        pass

    print("\n[PASS] Test 4")


if __name__ == "__main__":
    test_1_tool_search()
    test_2_system_prompt()
    test_3_context_engine()
    test_4_full_integration()
    print("\n" + "=" * 60)
    print("ALL TESTS PASSED")
    print("=" * 60)

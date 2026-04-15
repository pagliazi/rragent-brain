"""
5-Layer Context Compression Engine.

Runs before every LLM call to keep context within token budget.

Layer 1: Tool Result Budget — large results → disk + preview
Layer 2: History Snip — old conversation segments → truncation markers
Layer 3: Microcompact — old tool results → rule-based folding
Layer 4: Context Collapse — old messages → summary blocks
Layer 5: Autocompact — full LLM-powered conversation summary (circuit breaker protected)
"""

from __future__ import annotations

import logging
from typing import Any

from rragent.runtime.session import Session
from rragent.tools.registry import GlobalToolRegistry

logger = logging.getLogger("rragent.context.engine")


class ContextEngine:
    """
    Orchestrates 5-layer context compression.

    Implements the ContextProvider protocol expected by ConversationRuntime.
    """

    def __init__(self, config: Any, registry: GlobalToolRegistry):
        self.config = config
        self.registry = registry

        max_tokens = config.get("context", "max_tokens", default=200000) or 200000
        threshold = config.get("context", "autocompact_threshold", default=0.8) or 0.8
        max_result = config.get("context", "tool_result_max_chars", default=50000) or 50000

        self.max_tokens = max_tokens
        self.autocompact_threshold = threshold
        self.max_tool_result_chars = max_result

        # Circuit breaker for autocompact
        self._autocompact_failures = 0
        self._autocompact_max_failures = 3
        self._has_attempted_reactive_compact = False

        # Memory tiers (set externally by run_p3.py)
        self.session_memory = None   # Tier 1
        self.user_memory = None      # Tier 2
        self.system_memory = None    # Tier 3

    async def prepare(self, session: Session) -> dict[str, Any]:
        """Prepare context for LLM call with 5-layer compression."""
        messages = session.to_api_messages()

        # Layer 1: Tool Result Budget
        messages = self._apply_tool_result_budget(messages)

        # Layer 2: History Snip
        messages = self._apply_history_snip(messages)

        # Layer 3: Microcompact
        messages = self._apply_microcompact(messages)

        # Layer 4: Context Collapse
        messages = self._apply_context_collapse(messages)

        # Layer 5: Autocompact (circuit breaker protected)
        estimated_tokens = self._estimate_tokens(messages)
        if estimated_tokens > self.max_tokens * self.autocompact_threshold:
            if self._autocompact_failures < self._autocompact_max_failures:
                try:
                    messages = await self._apply_autocompact(messages)
                    self._autocompact_failures = 0
                except Exception as e:
                    self._autocompact_failures += 1
                    logger.warning(
                        f"Autocompact failed ({self._autocompact_failures}/"
                        f"{self._autocompact_max_failures}): {e}"
                    )

        # Build prompt with tool index
        from rragent.runtime.prompt import PromptBuilder
        try:
            builder = PromptBuilder(self.registry, self.config)
            system_prompt = builder.build_system_prompt(session)
        except Exception:
            system_prompt = ""

        # Inject memory tier context into system prompt
        memory_parts = []
        try:
            if self.session_memory:
                s1 = self.session_memory.get_context_string()
                if s1:
                    memory_parts.append(f"## 会话记忆\n{s1}")
            if self.user_memory:
                s2 = self.user_memory.get_context_string()
                if s2:
                    memory_parts.append(f"## 用户档案\n{s2}")
            if self.system_memory:
                s3 = self.system_memory.get_context_string()
                if s3:
                    memory_parts.append(f"## 系统知识\n{s3}")
        except Exception as e:
            logger.debug(f"Memory context injection failed: {e}")

        if memory_parts:
            system_prompt += "\n\n" + "\n\n".join(memory_parts)

        return {
            "messages": messages,
            "system_prompt": system_prompt,
            "tools": self.registry.get_all_active_schemas(),
            "model": self.config.get("providers", "primary", default="qwen3.5-plus"),
        }

    async def force_compact(self, session: Session) -> bool:
        """Force a full compaction (called on context overflow)."""
        if self._has_attempted_reactive_compact:
            return False

        self._has_attempted_reactive_compact = True
        try:
            messages = session.to_api_messages()
            compacted = await self._apply_autocompact(messages)
            # Replace session messages with compacted version
            session.messages.clear()
            session.append_system("[Context compacted due to overflow]")
            return True
        except Exception as e:
            logger.error(f"Force compact failed: {e}")
            return False

    # ── Layer 1: Tool Result Budget ──

    def _apply_tool_result_budget(self, messages: list[dict]) -> list[dict]:
        """Truncate oversized tool results with preview (>10K chars)."""
        budget = 10000  # 10K char budget per tool result
        result = []
        for msg in messages:
            if msg.get("role") == "user" and isinstance(msg.get("content"), list):
                new_content = []
                for block in msg["content"]:
                    if (
                        block.get("type") == "tool_result"
                        and isinstance(block.get("content"), str)
                        and len(block["content"]) > budget
                    ):
                        original_len = len(block["content"])
                        truncated = block["content"][:budget]
                        block = {
                            **block,
                            "content": (
                                f"{truncated}\n\n"
                                f"[...truncated, {original_len} chars total]"
                            ),
                        }
                    new_content.append(block)
                result.append({**msg, "content": new_content})
            else:
                result.append(msg)
        return result

    # ── Layer 2: History Snip ──

    def _apply_history_snip(self, messages: list[dict]) -> list[dict]:
        """Keep first 2 messages + last 10 messages, drop middle."""
        keep_start = 2
        keep_end = 10

        if len(messages) <= keep_start + keep_end:
            return messages

        snipped_count = len(messages) - keep_start - keep_end
        marker = {
            "role": "user",
            "content": f"[--- 已省略 {snipped_count} 条历史消息 ---]",
        }
        return messages[:keep_start] + [marker] + messages[-keep_end:]

    # ── Layer 3: Microcompact ──

    def _apply_microcompact(self, messages: list[dict]) -> list[dict]:
        """
        Replace consecutive assistant messages (no tool calls) with summary.
        Also compact old tool results in the first half.
        """
        if len(messages) <= 10:
            return messages

        result = []
        midpoint = len(messages) // 2

        # Pass 1: compact old tool results
        compacted = []
        for i, msg in enumerate(messages):
            if i < midpoint and msg.get("role") == "user":
                content = msg.get("content")
                if isinstance(content, list):
                    new_content = []
                    for block in content:
                        if block.get("type") == "tool_result":
                            original = block.get("content", "")
                            if isinstance(original, str) and len(original) > 500:
                                block = {
                                    **block,
                                    "content": original[:200] + "\n[... compacted]",
                                }
                        new_content.append(block)
                    compacted.append({**msg, "content": new_content})
                else:
                    compacted.append(msg)
            else:
                compacted.append(msg)

        # Pass 2: merge consecutive text-only assistant messages in first half
        i = 0
        while i < len(compacted):
            msg = compacted[i]
            if (
                i < midpoint
                and msg.get("role") == "assistant"
                and self._is_text_only_assistant(msg)
            ):
                # Collect consecutive text-only assistant messages
                texts = [self._extract_assistant_text(msg)]
                j = i + 1
                while (
                    j < midpoint
                    and j < len(compacted)
                    and compacted[j].get("role") == "assistant"
                    and self._is_text_only_assistant(compacted[j])
                ):
                    texts.append(self._extract_assistant_text(compacted[j]))
                    j += 1

                if len(texts) > 1:
                    # Merge into summary
                    summary = " | ".join(t[:80] for t in texts if t)
                    result.append({
                        "role": "assistant",
                        "content": [{"type": "text", "text": f"[合并{len(texts)}条回复] {summary}"}],
                    })
                else:
                    result.append(msg)
                i = j
            else:
                result.append(msg)
                i += 1

        return result

    @staticmethod
    def _is_text_only_assistant(msg: dict) -> bool:
        """Check if an assistant message has only text content (no tool_use)."""
        content = msg.get("content", "")
        if isinstance(content, str):
            return True
        if isinstance(content, list):
            return all(b.get("type") != "tool_use" for b in content)
        return False

    @staticmethod
    def _extract_assistant_text(msg: dict) -> str:
        """Extract text from an assistant message."""
        content = msg.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [b.get("text", "") for b in content if b.get("type") == "text"]
            return " ".join(parts)
        return ""

    # ── Layer 4: Context Collapse ──

    def _apply_context_collapse(self, messages: list[dict]) -> list[dict]:
        """If total estimated tokens > 150K, summarize old messages into a [Context Summary]."""
        collapse_threshold = 150000
        estimated = self._estimate_tokens(messages)

        if estimated <= collapse_threshold:
            return messages

        logger.info(
            f"Context collapse triggered: ~{estimated} tokens > {collapse_threshold}"
        )

        # Keep last 6 messages intact, summarize the rest
        keep_tail = 6
        if len(messages) <= keep_tail:
            return messages

        old_messages = messages[:-keep_tail]
        tail_messages = messages[-keep_tail:]

        # Build a rule-based summary of old messages
        summary_parts = []
        tool_calls_seen: list[str] = []
        user_topics: list[str] = []

        for msg in old_messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "user":
                text = content if isinstance(content, str) else ""
                if isinstance(content, list):
                    for b in content:
                        if b.get("type") == "text":
                            text = b.get("text", "")
                        elif b.get("type") == "tool_result":
                            tool_id = b.get("tool_use_id", "")
                            if tool_id:
                                tool_calls_seen.append(tool_id)
                if text and not text.startswith("[") and not text.startswith("<system>"):
                    user_topics.append(text[:100])

            elif role == "assistant" and isinstance(content, list):
                for b in content:
                    if b.get("type") == "tool_use":
                        name = b.get("name", "?")
                        tool_calls_seen.append(name)

        if user_topics:
            summary_parts.append("用户话题: " + "; ".join(user_topics[:5]))
        if tool_calls_seen:
            unique_tools = list(dict.fromkeys(tool_calls_seen))[:10]
            summary_parts.append("已调用工具: " + ", ".join(unique_tools))
        summary_parts.append(f"(已压缩 {len(old_messages)} 条消息)")

        summary_msg = {
            "role": "user",
            "content": "[Context Summary]\n" + "\n".join(summary_parts),
        }

        return [summary_msg] + tail_messages

    # ── Layer 5: Autocompact ──

    async def _apply_autocompact(self, messages: list[dict]) -> list[dict]:
        """LLM-powered full conversation summary."""
        # Extract text content for summary
        text_parts = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if isinstance(content, str) and content:
                text_parts.append(f"[{role}]: {content[:500]}")
            elif isinstance(content, list):
                for block in content:
                    if block.get("type") == "text":
                        text_parts.append(f"[{role}]: {block.get('text', '')[:500]}")

        if not text_parts:
            return messages

        summary = "\n".join(text_parts[:30])

        # Replace all but last 6 messages with summary
        keep = 6
        if len(messages) <= keep:
            return messages

        summary_msg = {
            "role": "user",
            "content": (
                f"<system>以下是之前对话的摘要：\n{summary}\n"
                f"[已压缩 {len(messages) - keep} 条消息]</system>"
            ),
        }
        return [summary_msg] + messages[-keep:]

    def _estimate_tokens(self, messages: list[dict]) -> int:
        """Rough token estimation (4 chars ≈ 1 token for CJK-heavy content)."""
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total += len(content) // 3
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block.get("content"), str):
                        total += len(block["content"]) // 3
                    if isinstance(block.get("text"), str):
                        total += len(block["text"]) // 3
        return total

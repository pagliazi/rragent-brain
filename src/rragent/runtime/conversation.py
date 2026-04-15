"""
ConversationRuntime — the core LLM loop (the heart of the harness).

Design sources:
- claude-code query.ts: async generator yield pattern
- claw-code conversation.rs: ConversationRuntime<C, T> generics
- hermes run_agent.py: iteration budget + background review

Key design decisions:
1. async generator (not callback) — caller uses `async for`
2. Context and Tool providers injected via Protocol
3. 5-layer context compression before each LLM call
4. Tool errors don't break loop (errors as results)
5. Skip hooks after API errors (death spiral prevention)
6. Iteration budget with PTC refund
"""

from __future__ import annotations

import asyncio
import os
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncGenerator, Protocol, runtime_checkable

from rragent.tools.base import ToolResult, ToolUse
from rragent.tools.executor import ToolExecutor
from rragent.tools.registry import GlobalToolRegistry
from rragent.runtime.session import Session, UsageRecord, Message

logger = logging.getLogger("rragent.runtime.conversation")


# ── Event types yielded by the runtime ──

class EventType(str, Enum):
    TEXT_DELTA = "text_delta"
    TOOL_START = "tool_start"
    TOOL_RESULT = "tool_result"
    WARNING = "warning"
    ERROR = "error"
    TURN_COMPLETE = "turn_complete"
    USAGE = "usage"


@dataclass
class TurnEvent:
    type: EventType
    data: Any = None

    @classmethod
    def text_delta(cls, text: str) -> "TurnEvent":
        return cls(type=EventType.TEXT_DELTA, data=text)

    @classmethod
    def tool_start(cls, tool_use: ToolUse) -> "TurnEvent":
        return cls(type=EventType.TOOL_START, data=tool_use)

    @classmethod
    def tool_result(cls, tool_use: ToolUse, result: ToolResult) -> "TurnEvent":
        return cls(type=EventType.TOOL_RESULT, data={"tool_use": tool_use, "result": result})

    @classmethod
    def warning(cls, message: str) -> "TurnEvent":
        return cls(type=EventType.WARNING, data=message)

    @classmethod
    def error(cls, message: str) -> "TurnEvent":
        return cls(type=EventType.ERROR, data=message)

    @classmethod
    def turn_complete(cls) -> "TurnEvent":
        return cls(type=EventType.TURN_COMPLETE)

    @classmethod
    def usage(cls, record: UsageRecord) -> "TurnEvent":
        return cls(type=EventType.USAGE, data=record)


# ── Protocols for dependency injection ──

@runtime_checkable
class ContextProvider(Protocol):
    """Prepares context for each LLM call (5-layer compression)."""
    async def prepare(self, session: Session) -> dict[str, Any]: ...
    async def force_compact(self, session: Session) -> bool: ...


@runtime_checkable
class LLMProvider(Protocol):
    """Streams LLM responses."""
    async def stream(
        self,
        messages: list[dict],
        system: str,
        tools: list[dict],
        model: str,
    ) -> AsyncGenerator[dict, None]: ...

    def rotate_credential(self) -> None: ...
    def switch_to_fallback(self) -> bool: ...


@runtime_checkable
class ErrorClassifier(Protocol):
    """Classifies API errors for recovery decisions."""
    def classify(self, error: Exception) -> Any: ...


# ── Configuration ──

@dataclass
class TurnConfig:
    max_tool_rounds: int = 30
    iteration_budget: int = 90
    budget_refund_on_ptc: int = 5
    streaming: bool = True
    skip_hooks_on_api_error: bool = True


# ── The Core Runtime ──

class ConversationRuntime:
    """
    The core LLM loop — controls all decisions.

    Usage:
        async for event in runtime.run_turn("今天涨停板有哪些半导体？"):
            if event.type == EventType.TEXT_DELTA:
                send_to_user(event.data)
            elif event.type == EventType.TOOL_RESULT:
                log_tool_call(event.data)
    """

    def __init__(
        self,
        session: Session,
        registry: GlobalToolRegistry,
        executor: ToolExecutor,
        llm_provider: LLMProvider,
        context_provider: ContextProvider | None = None,
        error_classifier: ErrorClassifier | None = None,
        config: TurnConfig | None = None,
        system_prompt: str = "",
    ):
        self.session = session
        self.registry = registry
        self.executor = executor
        self.llm = llm_provider
        self.context_provider = context_provider
        self.error_classifier = error_classifier
        self.config = config or TurnConfig()
        self.system_prompt = system_prompt

        # Internal state
        self._iteration_budget = 0
        self._skip_hooks = False
        self._correction_tracker: list[dict] = []

        # Hooks (set externally)
        self.post_tool_hook = None
        self.background_review = None

    async def run_turn(self, user_message: str) -> AsyncGenerator[TurnEvent, None]:
        """Process one user message through the full agentic loop."""
        # 0. Append user message
        self.session.append_user(user_message)
        self._iteration_budget = self.config.iteration_budget
        self._skip_hooks = False
        tool_round = 0

        while tool_round < self.config.max_tool_rounds:
            # ── 1. Prepare context (with compression) ──
            if self.context_provider:
                try:
                    ctx = await self.context_provider.prepare(self.session)
                except Exception as e:
                    logger.error(f"Context preparation failed: {e}")
                    # Fallback: use raw messages
                    ctx = self._fallback_context()
            else:
                ctx = self._fallback_context()

            messages = ctx.get("messages", self.session.to_api_messages())
            system = ctx.get("system_prompt", self.system_prompt)
            tools = ctx.get("tools", self.registry.get_all_active_schemas())
            model = ctx.get("model", os.getenv("RRAGENT_DEFAULT_MODEL", "qwen3.5-plus"))

            # ── 2. LLM call (streaming) ──
            assistant_text = ""
            tool_uses: list[ToolUse] = []
            usage_record = UsageRecord(model=model)

            try:
                async for chunk in self.llm.stream(
                    messages=messages,
                    system=system,
                    tools=tools,
                    model=model,
                ):
                    chunk_type = chunk.get("type", "")

                    if chunk_type == "text_delta":
                        text = chunk.get("text", "")
                        assistant_text += text
                        yield TurnEvent.text_delta(text)

                    elif chunk_type == "tool_use":
                        tu = ToolUse(
                            id=chunk.get("id", ""),
                            name=chunk.get("name", ""),
                            input=chunk.get("input", {}),
                        )
                        tool_uses.append(tu)

                    elif chunk_type == "usage":
                        usage_record.input_tokens = chunk.get("input_tokens", 0)
                        usage_record.output_tokens = chunk.get("output_tokens", 0)
                        usage_record.cache_creation_input_tokens = chunk.get(
                            "cache_creation_input_tokens", 0
                        )
                        usage_record.cache_read_input_tokens = chunk.get(
                            "cache_read_input_tokens", 0
                        )

                    elif chunk_type == "error":
                        yield TurnEvent.error(chunk.get("message", "Unknown API error"))
                        return

            except Exception as e:
                # ── Death spiral prevention: skip hooks on API error ──
                self._skip_hooks = True

                if self.error_classifier:
                    classified = self.error_classifier.classify(e)

                    if getattr(classified, "should_compress", False):
                        if self.context_provider:
                            await self.context_provider.force_compact(self.session)
                            continue

                    if getattr(classified, "should_rotate_credential", False):
                        self.llm.rotate_credential()
                        continue

                    if getattr(classified, "should_fallback", False):
                        if self.llm.switch_to_fallback():
                            continue

                yield TurnEvent.error(f"API error: {e}")
                return

            # ── 3. Record usage ──
            self.session.record_usage(usage_record)
            yield TurnEvent.usage(usage_record)

            # ── 4. Save assistant message ──
            self.session.append_assistant(
                content=assistant_text,
                tool_uses=[
                    {"id": tu.id, "name": tu.name, "input": tu.input}
                    for tu in tool_uses
                ],
            )

            # ── 5. No tool calls = LLM is done ──
            if not tool_uses:
                break

            # ── 6. Iteration budget check ──
            self._iteration_budget -= len(tool_uses)
            if self._iteration_budget <= 0:
                yield TurnEvent.warning(
                    f"Iteration budget exhausted ({self.config.iteration_budget}). "
                    f"Stopping tool calls."
                )
                break

            # ── 7. Execute tools (concurrent/serial partitioning) ──
            results = await self.executor.execute_batch(tool_uses)

            for tu, result in results:
                yield TurnEvent.tool_start(tu)
                yield TurnEvent.tool_result(tu, result)

                # Save tool result to session
                self.session.append_tool_result(
                    tool_use_id=tu.id,
                    content=result.content,
                    is_error=result.is_error,
                )

                # PTC refund
                if tu.name == "execute_code" and not result.is_error:
                    self._iteration_budget += self.config.budget_refund_on_ptc

                # Correction tracking
                self._correction_tracker.append({
                    "tool": tu.name,
                    "success": not result.is_error,
                    "timestamp": time.time(),
                })

            # ── 8. Post-tool hooks (skip on API error) ──
            if not self._skip_hooks and self.post_tool_hook:
                try:
                    hook_msg = await self.post_tool_hook(tool_uses, results)
                    if hook_msg:
                        self.session.append_system(hook_msg)
                except Exception as e:
                    logger.warning(f"Post-tool hook error: {e}")

            tool_round += 1

        # ── 9. Background review ──
        if self.background_review:
            try:
                await self.background_review.check_and_spawn(
                    self.session, self._correction_tracker
                )
            except Exception as e:
                logger.warning(f"Background review error: {e}")

        # ── 10. Persist session ──
        self.session.persist()

        # ── 11. Save trajectory (ShareGPT JSONL for evolution/training) ──
        try:
            self._save_trajectory(completed=not self._skip_hooks)
        except Exception as e:
            logger.debug(f"Trajectory save failed (non-critical): {e}")

        yield TurnEvent.turn_complete()

    def _fallback_context(self) -> dict[str, Any]:
        """Minimal context when ContextProvider is unavailable."""
        return {
            "messages": self.session.to_api_messages(),
            "system_prompt": self.system_prompt,
            "tools": self.registry.get_all_active_schemas(),
            "model": os.getenv("RRAGENT_DEFAULT_MODEL", "qwen3.5-plus"),
        }

    def _save_trajectory(self, completed: bool = True) -> None:
        """
        Save conversation trajectory as ShareGPT JSONL for evolution/training.

        Files:
          ~/.rragent/trajectories/trajectory_samples.jsonl  (successful)
          ~/.rragent/trajectories/failed_trajectories.jsonl (failed/incomplete)

        Format (one JSON object per line):
          {
            "conversations": [{"from": "human"|"gpt", "value": str}, ...],
            "timestamp": float,
            "model": str,
            "session_id": str,
            "completed": bool,
            "tool_calls": int,
          }
        """
        import json
        from pathlib import Path

        traj_dir = Path.home() / ".rragent" / "trajectories"
        traj_dir.mkdir(parents=True, exist_ok=True)

        filename = "trajectory_samples.jsonl" if completed else "failed_trajectories.jsonl"
        traj_file = traj_dir / filename

        # Build ShareGPT conversation list from session messages
        conversations = []
        for msg in self.session.to_api_messages():
            role = msg.get("role", "")
            content = msg.get("content", "")

            # Flatten content
            if isinstance(content, list):
                parts = []
                for block in content:
                    if block.get("type") == "text":
                        parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        parts.append(
                            f"[tool:{block.get('name','')}] "
                            + json.dumps(block.get("input", {}), ensure_ascii=False)[:200]
                        )
                    elif block.get("type") == "tool_result":
                        result_content = block.get("content", "")
                        if isinstance(result_content, str):
                            parts.append(f"[result] {result_content[:500]}")
                text = "\n".join(p for p in parts if p)
            else:
                text = str(content)

            if not text.strip():
                continue

            from_role = "human" if role == "user" else "gpt"
            conversations.append({"from": from_role, "value": text})

        if len(conversations) < 2:
            return  # Not enough content to save

        # Determine model used
        model = os.getenv("RRAGENT_DEFAULT_MODEL", "unknown")
        try:
            last_usage = self.session.usage_history[-1] if self.session.usage_history else None
            if last_usage and hasattr(last_usage, "model"):
                model = last_usage.model
        except Exception:
            pass

        record = {
            "conversations": conversations,
            "timestamp": time.time(),
            "model": model,
            "session_id": getattr(self.session, "session_id", ""),
            "completed": completed,
            "tool_calls": len(self._correction_tracker),
        }

        with open(traj_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        logger.debug(
            f"Trajectory saved: {len(conversations)} turns → {traj_file.name}"
        )

    @property
    def corrections(self) -> list[dict]:
        return self._correction_tracker

"""
Background Review System — session-level self-reflection (Loop 2).

Counter-driven review system inspired by Hermes _spawn_background_review():
- _turns_since_memory: increments per user turn, resets when memory tool used
- _iters_since_skill: increments per tool iteration, resets when skill_manage used
- Triggers: turns >= 10 (memory review) OR iters >= 10 (skill review)
- Execution: fork agent in daemon thread with max_iterations=8
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .correction_tracker import CorrectionTracker
    from rragent.tools.hermes.runtime import HermesNativeRuntime
    from rragent.runtime.session import Session

logger = logging.getLogger("rragent.evolution.background_review")


MEMORY_REVIEW_PROMPT = """
Review the recent conversation. Did the user reveal information about themselves?
Consider:
- Trading preferences, sectors they follow, risk tolerance
- Workflow habits, frequently used commands
- Preferred analysis style or data presentation
- Time patterns (when they typically trade/analyze)

If you find valuable information, save it to memory.
Focus on non-obvious insights that will help future interactions.
Do NOT save trivial or already-known information.
"""

SKILL_REVIEW_PROMPT = """
Review the recent execution flow. Were there patterns worth saving?
Consider:
- Did you use 5+ tool calls to complete a task that could be a single skill?
- Did you go through trial-and-error before finding the right approach?
- Are there repeated tool call chains across this conversation?
- Is there a multi-step workflow the user will likely repeat?

If worth saving, create a new skill with:
- Specific steps and parameters
- Common pitfalls and how to avoid them
- Validation/verification steps
- When this skill should be triggered
"""

CORRECTION_REVIEW_PROMPT = """
Review the recent correction events. Analyze:
- What operations caused errors?
- What were the root causes?
- What correction strategies succeeded?
- How can similar errors be prevented?

If you find a reusable correction pattern, create a skill that:
- Describes the error scenario
- Lists the diagnostic steps
- Provides the fix procedure
- Includes prevention guidelines
"""


@dataclass
class ReviewResult:
    """Result from a background review."""

    review_type: str  # "memory", "skill", "correction"
    started_at: float
    completed_at: float = 0.0
    success: bool = False
    skills_created: list[str] = field(default_factory=list)
    memories_saved: list[str] = field(default_factory=list)
    error: str = ""


class BackgroundReviewSystem:
    """
    Counter-driven background review system.

    Spawns daemon threads to run Hermes agent for self-reflection.
    Reviews analyze recent conversation and create Skills/Memories.

    Thread safety: review runs in daemon thread with its own agent instance.
    Shared state (memory/skill files) is written via atomic file operations.
    """

    MEMORY_NUDGE_INTERVAL = 10
    SKILL_NUDGE_INTERVAL = 10
    MAX_CONCURRENT_REVIEWS = 1
    MAX_REVIEW_ITERATIONS = 8

    def __init__(
        self,
        hermes_runtime: HermesNativeRuntime | None = None,
        correction_tracker: CorrectionTracker | None = None,
        memory_available: bool = True,
        skill_manage_available: bool = True,
    ):
        self._hermes = hermes_runtime
        self._correction_tracker = correction_tracker

        self.memory_available = memory_available
        self.skill_manage_available = skill_manage_available

        self._turns_since_memory = 0
        self._iters_since_skill = 0
        self._active_reviews = 0
        self._lock = threading.Lock()
        self._review_history: list[ReviewResult] = []

    @property
    def available(self) -> bool:
        """Whether the review system can run."""
        return self._hermes is not None and self._hermes.available

    def increment_turn(self):
        """Called after each user turn in conversation."""
        self._turns_since_memory += 1

    def increment_iterations(self, count: int = 1):
        """Called after tool execution rounds."""
        self._iters_since_skill += count

    def reset_memory_counter(self):
        """Reset when memory tool is used."""
        self._turns_since_memory = 0

    def reset_skill_counter(self):
        """Reset when skill_manage tool is used."""
        self._iters_since_skill = 0

    async def check_and_spawn(
        self,
        session: Session,
        turn_result: Any = None,
    ):
        """
        Check counters and spawn background review if needed.

        Called at the end of each turn in ConversationRuntime.
        """
        if not self.available:
            return

        with self._lock:
            if self._active_reviews >= self.MAX_CONCURRENT_REVIEWS:
                return

        should_review_memory = (
            self._turns_since_memory >= self.MEMORY_NUDGE_INTERVAL
            and self.memory_available
        )
        should_review_skill = (
            self._iters_since_skill >= self.SKILL_NUDGE_INTERVAL
            and self.skill_manage_available
        )
        had_corrections = (
            self._correction_tracker is not None
            and self._correction_tracker.has_corrections
        )

        if not (should_review_memory or should_review_skill or had_corrections):
            return

        # Build review prompt
        prompt = self._build_review_prompt(
            should_review_memory,
            should_review_skill,
            had_corrections,
        )

        # Extract conversation context for the review agent
        context = self._extract_context(session)

        # Spawn review in daemon thread
        review_type = self._determine_review_type(
            should_review_memory, should_review_skill, had_corrections
        )

        thread = threading.Thread(
            target=self._run_review_sync,
            args=(context, prompt, review_type),
            daemon=True,
            name=f"bg-review-{review_type}-{int(time.time())}",
        )

        with self._lock:
            self._active_reviews += 1

        thread.start()
        logger.info(
            f"Background review spawned: type={review_type}, "
            f"turns={self._turns_since_memory}, iters={self._iters_since_skill}"
        )

        # Reset counters
        if should_review_memory:
            self._turns_since_memory = 0
        if should_review_skill:
            self._iters_since_skill = 0

    def _build_review_prompt(
        self,
        review_memory: bool,
        review_skill: bool,
        review_corrections: bool,
    ) -> str:
        parts = []

        if review_corrections:
            parts.append(CORRECTION_REVIEW_PROMPT)
            if self._correction_tracker:
                summary = self._correction_tracker.get_summary()
                parts.append(f"\nCorrection stats: {summary}")

        if review_memory:
            parts.append(MEMORY_REVIEW_PROMPT)

        if review_skill:
            parts.append(SKILL_REVIEW_PROMPT)

        return "\n\n---\n\n".join(parts)

    def _extract_context(self, session: Session) -> str:
        """Extract recent conversation context for review agent."""
        messages = session.messages[-20:]  # Last 20 messages
        context_parts = []

        for msg in messages:
            role = msg.role
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            # Truncate long content
            if len(content) > 500:
                content = content[:500] + "..."
            context_parts.append(f"[{role}]: {content}")

        return "\n".join(context_parts)

    def _determine_review_type(
        self, memory: bool, skill: bool, correction: bool
    ) -> str:
        if correction:
            return "correction"
        if memory and skill:
            return "memory_and_skill"
        if memory:
            return "memory"
        return "skill"

    def _run_review_sync(self, context: str, prompt: str, review_type: str):
        """Run review in daemon thread (sync wrapper for async Hermes call)."""
        result = ReviewResult(review_type=review_type, started_at=time.time())

        try:
            # Create a new event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            try:
                hermes_result = loop.run_until_complete(
                    self._hermes.run_background_review(
                        conversation_context=context,
                        review_prompt=prompt,
                        max_iterations=self.MAX_REVIEW_ITERATIONS,
                    )
                )

                result.success = hermes_result.success
                result.skills_created = hermes_result.skills_created
                result.memories_saved = hermes_result.memories_saved

                if hermes_result.errors:
                    result.error = "; ".join(hermes_result.errors)

                logger.info(
                    f"Background review completed: type={review_type}, "
                    f"success={result.success}, "
                    f"skills={len(result.skills_created)}, "
                    f"memories={len(result.memories_saved)}"
                )
            finally:
                loop.close()

        except Exception as e:
            result.error = str(e)
            logger.error(f"Background review failed: {e}")

        finally:
            result.completed_at = time.time()
            self._review_history.append(result)
            with self._lock:
                self._active_reviews -= 1

    @property
    def review_history(self) -> list[ReviewResult]:
        return list(self._review_history)

    @property
    def stats(self) -> dict:
        return {
            "turns_since_memory": self._turns_since_memory,
            "iters_since_skill": self._iters_since_skill,
            "active_reviews": self._active_reviews,
            "total_reviews": len(self._review_history),
            "successful_reviews": sum(1 for r in self._review_history if r.success),
            "memory_available": self.memory_available,
            "skill_manage_available": self.skill_manage_available,
        }

"""
Smart Model Routing — heuristic cheap/strong model selection.

Before every LLM call, evaluate the user message:
  - Short, simple, no code/URLs → cheap model (fast + low cost)
  - Long, complex, contains code/analysis keywords → strong model

Inspired by hermes smart_model_routing.py but adapted for rragent's
multi-provider chain (qwen3.5-plus / deepseek-chat / claude).

Usage:
    from rragent.runtime.providers.smart_routing import choose_model

    model = choose_model(user_message, cheap="qwen-turbo", strong="qwen3.5-plus")
"""

from __future__ import annotations

import re
import logging

logger = logging.getLogger("rragent.providers.smart_routing")

# Patterns that signal a complex request
_COMPLEX_PATTERNS = re.compile(
    r"```|```python|http[s]?://|www\."       # code blocks, URLs
    r"|分析|策略|回测|报告|生成代码|优化|架构"    # Chinese analysis keywords
    r"|analyze|implement|generate|refactor|optimize|architecture"
    r"|[0-9]{4,}",                            # large numbers (prices, dates w/ year)
    re.IGNORECASE,
)

# Max length/word thresholds for "cheap" route
_MAX_CHARS_CHEAP = 160
_MAX_WORDS_CHEAP = 28


def is_simple_request(user_message: str) -> bool:
    """
    Return True if the message is simple enough for a cheap model.

    Heuristics (all must pass):
    1. Length ≤ 160 chars
    2. Word count ≤ 28 words
    3. No code blocks, URLs, or complex keywords
    """
    if not user_message:
        return True

    text = user_message.strip()

    if len(text) > _MAX_CHARS_CHEAP:
        return False

    word_count = len(text.split())
    if word_count > _MAX_WORDS_CHEAP:
        return False

    if _COMPLEX_PATTERNS.search(text):
        return False

    return True


def choose_model(
    user_message: str,
    *,
    cheap: str = "qwen-turbo",
    strong: str = "qwen3.5-plus",
) -> str:
    """
    Choose between cheap and strong model based on message complexity.

    Args:
        user_message: The user's message text.
        cheap: Model name for simple requests (low cost, fast).
        strong: Model name for complex requests (high quality).

    Returns:
        Model name string (ready for provider.stream()).
    """
    if is_simple_request(user_message):
        logger.debug(f"Smart routing → cheap model ({cheap}): {user_message[:60]!r}")
        return cheap
    else:
        logger.debug(f"Smart routing → strong model ({strong}): {user_message[:60]!r}")
        return strong


class SmartModelRouter:
    """
    Stateful model router that tracks routing decisions for analytics.

    Attach to ConversationRuntime.context_provider or inject into
    ContextEngine.prepare() to override model selection.
    """

    def __init__(
        self,
        cheap_model: str = "qwen-turbo",
        strong_model: str = "qwen3.5-plus",
        enabled: bool = True,
    ):
        self.cheap_model = cheap_model
        self.strong_model = strong_model
        self.enabled = enabled
        self._cheap_count = 0
        self._strong_count = 0

    def route(self, user_message: str) -> str:
        """Return chosen model name."""
        if not self.enabled:
            return self.strong_model

        model = choose_model(
            user_message, cheap=self.cheap_model, strong=self.strong_model
        )
        if model == self.cheap_model:
            self._cheap_count += 1
        else:
            self._strong_count += 1
        return model

    def stats(self) -> dict:
        total = self._cheap_count + self._strong_count
        return {
            "total": total,
            "cheap": self._cheap_count,
            "strong": self._strong_count,
            "cheap_pct": round(100 * self._cheap_count / total, 1) if total else 0,
        }

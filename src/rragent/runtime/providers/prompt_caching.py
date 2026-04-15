"""
Prompt Caching — inject Anthropic cache_control breakpoints.

Strategy: system_and_3
  - Mark system message with cache_control (if passed separately)
  - Mark last 3 non-system messages with cache_control {"type": "ephemeral"}
  - Total breakpoints ≤ 4 (Anthropic API limit)

Effect: ~75% reduction in input token costs on repeated context.

Usage:
    from rragent.runtime.providers.prompt_caching import apply_anthropic_cache_control
    messages = apply_anthropic_cache_control(messages)
    # For system prompt as separate param, pass system_out list too:
    messages, system_blocks = apply_anthropic_cache_control(
        messages, include_system=True, system_text=system_prompt
    )
"""

from __future__ import annotations

import copy
import logging
from typing import Any

logger = logging.getLogger("rragent.providers.prompt_caching")

_CACHE_CONTROL = {"type": "ephemeral"}
_MAX_BREAKPOINTS = 4


def apply_anthropic_cache_control(
    api_messages: list[dict],
    *,
    include_system: bool = False,
    system_text: str = "",
    max_breakpoints: int = _MAX_BREAKPOINTS,
) -> tuple[list[dict], list[dict] | None]:
    """
    Inject cache_control breakpoints into messages for Anthropic prompt caching.

    Args:
        api_messages: List of message dicts (role/content format).
        include_system: If True, also return a system blocks list with cache_control.
        system_text: System prompt text (used when include_system=True).
        max_breakpoints: Max cache breakpoints (default 4, Anthropic limit).

    Returns:
        (messages_with_cache, system_blocks_or_None)
        - messages_with_cache: Deep copy with cache_control injected on last N messages.
        - system_blocks_or_None: [{"type":"text","text":...,"cache_control":{...}}] when
          include_system=True, otherwise None.
    """
    messages = copy.deepcopy(api_messages)

    # Budget: 1 slot for system (if requested), rest for messages
    msg_slots = max_breakpoints - (1 if include_system and system_text else 0)
    msg_slots = max(1, msg_slots)

    # Mark last msg_slots messages
    eligible_indices = [
        i for i, m in enumerate(messages)
        if m.get("role") in ("user", "assistant")
    ]

    to_mark = eligible_indices[-msg_slots:]
    marked = 0
    for idx in reversed(to_mark):
        if marked >= msg_slots:
            break
        msg = messages[idx]
        content = msg.get("content", "")

        if isinstance(content, str):
            # Convert string content to block list so cache_control can be attached
            messages[idx] = {
                **msg,
                "content": [
                    {"type": "text", "text": content, "cache_control": _CACHE_CONTROL}
                ],
            }
            marked += 1
        elif isinstance(content, list) and content:
            # Attach cache_control to the last block in the content list
            last_block = copy.deepcopy(content[-1])
            last_block["cache_control"] = _CACHE_CONTROL
            messages[idx] = {
                **msg,
                "content": content[:-1] + [last_block],
            }
            marked += 1

    # Build system blocks
    system_blocks = None
    if include_system and system_text:
        system_blocks = [
            {
                "type": "text",
                "text": system_text,
                "cache_control": _CACHE_CONTROL,
            }
        ]

    logger.debug(
        f"Cache control applied: {marked} message breakpoints"
        + (", 1 system breakpoint" if system_blocks else "")
    )
    return messages, system_blocks

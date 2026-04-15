"""
Anthropic LLM Provider — primary provider for RRAgent.

Supports prompt caching (cache_control breakpoints) for ~75% input cost reduction
on repeated context. Enable via prompt_caching=True in constructor.
"""

from __future__ import annotations

import logging
import os
from typing import Any, AsyncGenerator

from rragent.runtime.providers.base import BaseLLMProvider

logger = logging.getLogger("rragent.providers.anthropic")


class AnthropicProvider(BaseLLMProvider):
    """Anthropic Claude API provider."""

    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        api_key: str = "",
        prompt_caching: bool = True,
    ):
        # Strip provider prefix
        if "/" in model:
            model = model.split("/", 1)[1]
        self.model = model
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self.prompt_caching = prompt_caching
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
                self._client = anthropic.AsyncAnthropic(api_key=self.api_key)
            except ImportError:
                raise ImportError("Install anthropic: pip install anthropic")
        return self._client

    async def stream(
        self,
        messages: list[dict],
        system: str,
        tools: list[dict],
        model: str,
    ) -> AsyncGenerator[dict, None]:
        client = self._get_client()
        actual_model = model.split("/")[-1] if "/" in model else (model or self.model)

        kwargs: dict[str, Any] = {
            "model": actual_model,
            "max_tokens": 8192,
            "messages": messages,
        }

        # Prompt caching: inject cache_control breakpoints
        if self.prompt_caching:
            try:
                from rragent.runtime.providers.prompt_caching import (
                    apply_anthropic_cache_control,
                )
                cached_messages, system_blocks = apply_anthropic_cache_control(
                    messages,
                    include_system=bool(system),
                    system_text=system,
                )
                kwargs["messages"] = cached_messages
                if system_blocks:
                    kwargs["system"] = system_blocks
                elif system:
                    kwargs["system"] = system
            except Exception as e:
                logger.debug(f"Prompt caching setup failed, using plain messages: {e}")
                if system:
                    kwargs["system"] = system
        else:
            if system:
                kwargs["system"] = system

        if tools:
            kwargs["tools"] = tools

        async with client.messages.stream(**kwargs) as stream:
            current_tool_use = None

            async for event in stream:
                if event.type == "content_block_start":
                    block = event.content_block
                    if block.type == "tool_use":
                        current_tool_use = {
                            "id": block.id,
                            "name": block.name,
                            "input_json": "",
                        }

                elif event.type == "content_block_delta":
                    delta = event.delta
                    if delta.type == "text_delta":
                        yield {"type": "text_delta", "text": delta.text}
                    elif delta.type == "input_json_delta":
                        if current_tool_use is not None:
                            current_tool_use["input_json"] += delta.partial_json

                elif event.type == "content_block_stop":
                    if current_tool_use is not None:
                        import json
                        try:
                            tool_input = json.loads(current_tool_use["input_json"])
                        except json.JSONDecodeError:
                            tool_input = {}
                        yield {
                            "type": "tool_use",
                            "id": current_tool_use["id"],
                            "name": current_tool_use["name"],
                            "input": tool_input,
                        }
                        current_tool_use = None

            # Usage info
            response = await stream.get_final_message()
            yield {
                "type": "usage",
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "cache_creation_input_tokens": getattr(
                    response.usage, "cache_creation_input_tokens", 0
                ),
                "cache_read_input_tokens": getattr(
                    response.usage, "cache_read_input_tokens", 0
                ),
            }

    async def complete(
        self,
        messages: list[dict],
        system: str,
        tools: list[dict],
        model: str,
    ) -> dict:
        client = self._get_client()
        actual_model = model.split("/")[-1] if "/" in model else (model or self.model)

        kwargs: dict[str, Any] = {
            "model": actual_model,
            "max_tokens": 8192,
            "messages": messages,
        }

        if self.prompt_caching:
            try:
                from rragent.runtime.providers.prompt_caching import (
                    apply_anthropic_cache_control,
                )
                cached_messages, system_blocks = apply_anthropic_cache_control(
                    messages,
                    include_system=bool(system),
                    system_text=system,
                )
                kwargs["messages"] = cached_messages
                if system_blocks:
                    kwargs["system"] = system_blocks
                elif system:
                    kwargs["system"] = system
            except Exception as e:
                logger.debug(f"Prompt caching setup failed: {e}")
                if system:
                    kwargs["system"] = system
        else:
            if system:
                kwargs["system"] = system

        if tools:
            kwargs["tools"] = tools

        response = await client.messages.create(**kwargs)
        return {
            "content": response.content,
            "usage": {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
            "stop_reason": response.stop_reason,
        }

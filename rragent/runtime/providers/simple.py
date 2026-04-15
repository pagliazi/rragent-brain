"""
SimpleLLMProvider — minimal provider for DashScope/Bailian via OpenAI-compatible SDK.

Designed as the P0 harness provider: just works, no bells and whistles.
Reads OPENAI_API_KEY and OPENAI_BASE_URL from environment with sensible defaults.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, AsyncGenerator

logger = logging.getLogger("rragent.providers.simple")

_DEFAULT_API_KEY = "sk-sp-0dd17ca1a5ed4a108b13d7942216e107"
_DEFAULT_BASE_URL = "https://coding.dashscope.aliyuncs.com/v1"
_DEFAULT_MODEL = "qwen3.5-plus"


class SimpleLLMProvider:
    """
    Minimal LLM provider that speaks the ConversationRuntime LLMProvider protocol.

    Yields:
        {"type": "text_delta", "text": "..."}
        {"type": "tool_use", "id": "...", "name": "...", "input": {...}}
        {"type": "usage", "input_tokens": N, "output_tokens": N}
        {"type": "error", "message": "..."}
    """

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "",
        model: str = "",
    ):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", _DEFAULT_API_KEY)
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL", _DEFAULT_BASE_URL)
        self.default_model = model or _DEFAULT_MODEL
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
            )
        return self._client

    async def stream(
        self,
        messages: list[dict],
        system: str,
        tools: list[dict],
        model: str,
    ) -> AsyncGenerator[dict, None]:
        client = self._get_client()
        actual_model = model or self.default_model

        # Build OpenAI-format messages
        oai_messages: list[dict] = []
        if system:
            oai_messages.append({"role": "system", "content": system})

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if isinstance(content, str):
                oai_messages.append({"role": role, "content": content})
            elif isinstance(content, list):
                # Flatten content blocks to text
                text_parts = []
                tool_calls_out = []
                tool_results_out = []

                for block in content:
                    btype = block.get("type", "")
                    if btype == "text":
                        text_parts.append(block["text"])
                    elif btype == "tool_use":
                        tool_calls_out.append(block)
                    elif btype == "tool_result":
                        tool_results_out.append(block)

                # Assistant message with tool calls
                if role == "assistant" and tool_calls_out:
                    assistant_msg: dict[str, Any] = {"role": "assistant"}
                    if text_parts:
                        assistant_msg["content"] = "\n".join(text_parts)
                    else:
                        assistant_msg["content"] = None
                    assistant_msg["tool_calls"] = [
                        {
                            "id": tc.get("id", ""),
                            "type": "function",
                            "function": {
                                "name": tc.get("name", ""),
                                "arguments": json.dumps(tc.get("input", {}), ensure_ascii=False),
                            },
                        }
                        for tc in tool_calls_out
                    ]
                    oai_messages.append(assistant_msg)
                elif tool_results_out:
                    # Tool results become role=tool messages
                    for tr in tool_results_out:
                        tr_content = tr.get("content", "")
                        if isinstance(tr_content, list):
                            tr_content = "\n".join(
                                b.get("text", "") for b in tr_content if b.get("type") == "text"
                            )
                        oai_messages.append({
                            "role": "tool",
                            "tool_call_id": tr.get("tool_use_id", ""),
                            "content": str(tr_content),
                        })
                else:
                    if text_parts:
                        oai_messages.append({"role": role, "content": "\n".join(text_parts)})

        # Build kwargs
        kwargs: dict[str, Any] = {
            "model": actual_model,
            "messages": oai_messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }

        # Convert tools to OpenAI function format
        if tools:
            oai_tools = []
            for tool in tools:
                oai_tools.append({
                    "type": "function",
                    "function": {
                        "name": tool.get("name", ""),
                        "description": tool.get("description", ""),
                        "parameters": tool.get("input_schema", tool.get("parameters", {})),
                    },
                })
            kwargs["tools"] = oai_tools

        try:
            response = await client.chat.completions.create(**kwargs)
        except Exception as e:
            yield {"type": "error", "message": str(e)}
            return

        current_tool_call: dict[str, str] | None = None
        input_tokens = 0
        output_tokens = 0

        async for chunk in response:
            # Usage from the final chunk
            if chunk.usage:
                input_tokens = chunk.usage.prompt_tokens or 0
                output_tokens = chunk.usage.completion_tokens or 0

            if not chunk.choices:
                continue

            delta = chunk.choices[0].delta

            # Text content
            if delta.content:
                yield {"type": "text_delta", "text": delta.content}

            # Tool calls (streamed incrementally)
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    if tc.id:
                        # New tool call starting — flush previous
                        if current_tool_call and current_tool_call.get("args"):
                            try:
                                tool_input = json.loads(current_tool_call["args"])
                            except json.JSONDecodeError:
                                tool_input = {}
                            yield {
                                "type": "tool_use",
                                "id": current_tool_call["id"],
                                "name": current_tool_call["name"],
                                "input": tool_input,
                            }
                        current_tool_call = {
                            "id": tc.id,
                            "name": tc.function.name if tc.function else "",
                            "args": "",
                        }
                    if tc.function and tc.function.arguments:
                        if current_tool_call:
                            current_tool_call["args"] += tc.function.arguments

        # Flush last tool call
        if current_tool_call and current_tool_call.get("args"):
            try:
                tool_input = json.loads(current_tool_call["args"])
            except json.JSONDecodeError:
                tool_input = {}
            yield {
                "type": "tool_use",
                "id": current_tool_call["id"],
                "name": current_tool_call["name"],
                "input": tool_input,
            }

        yield {"type": "usage", "input_tokens": input_tokens, "output_tokens": output_tokens}

    def rotate_credential(self) -> None:
        """No credential rotation in simple provider."""
        pass

    def switch_to_fallback(self) -> bool:
        """No fallback in simple provider."""
        return False

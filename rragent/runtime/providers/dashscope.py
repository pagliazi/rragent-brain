"""
DashScope (Alibaba Cloud) Provider — fallback for when Anthropic is overloaded.
OpenAI-compatible API with DashScope specifics.
"""

from __future__ import annotations

import logging
import os
from typing import Any, AsyncGenerator

from rragent.runtime.providers.base import BaseLLMProvider

logger = logging.getLogger("rragent.providers.dashscope")


class DashScopeProvider(BaseLLMProvider):
    """DashScope/Tongyi Qianwen provider (OpenAI-compatible)."""

    def __init__(
        self,
        model: str = "qwen3.5-plus",
        api_key: str = "",
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ):
        if "/" in model:
            model = model.split("/", 1)[1]
        self.model = model
        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY", "")
        self.base_url = base_url
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from openai import AsyncOpenAI
                self._client = AsyncOpenAI(
                    api_key=self.api_key,
                    base_url=self.base_url,
                )
            except ImportError:
                raise ImportError("Install openai: pip install openai")
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

        # Convert Anthropic format to OpenAI format
        oai_messages = self._convert_messages(messages, system)
        oai_tools = self._convert_tools(tools) if tools else None

        kwargs: dict[str, Any] = {
            "model": actual_model,
            "messages": oai_messages,
            "stream": True,
        }
        if oai_tools:
            kwargs["tools"] = oai_tools

        response = await client.chat.completions.create(**kwargs)

        current_tool_call = None
        async for chunk in response:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            if delta.content:
                yield {"type": "text_delta", "text": delta.content}

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    if tc.id:
                        if current_tool_call and current_tool_call.get("args"):
                            import json
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
            import json
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

        # Approximate usage
        yield {"type": "usage", "input_tokens": 0, "output_tokens": 0}

    async def complete(self, messages, system, tools, model) -> dict:
        client = self._get_client()
        actual_model = model.split("/")[-1] if "/" in model else (model or self.model)
        oai_messages = self._convert_messages(messages, system)
        oai_tools = self._convert_tools(tools) if tools else None

        kwargs: dict[str, Any] = {"model": actual_model, "messages": oai_messages}
        if oai_tools:
            kwargs["tools"] = oai_tools

        response = await client.chat.completions.create(**kwargs)
        return {"content": response.choices[0].message.content}

    def _convert_messages(self, messages: list[dict], system: str) -> list[dict]:
        """Convert Anthropic message format to OpenAI format."""
        result = []
        if system:
            result.append({"role": "system", "content": system})

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if isinstance(content, str):
                result.append({"role": role, "content": content})
            elif isinstance(content, list):
                # Convert content blocks
                text_parts = []
                for block in content:
                    if block.get("type") == "text":
                        text_parts.append(block["text"])
                    elif block.get("type") == "tool_result":
                        text_parts.append(
                            f"[Tool Result ({block.get('tool_use_id', '')})]: "
                            f"{block.get('content', '')}"
                        )
                    elif block.get("type") == "tool_use":
                        import json
                        text_parts.append(
                            f"[Tool Call: {block.get('name', '')}]: "
                            f"{json.dumps(block.get('input', {}))}"
                        )
                if text_parts:
                    result.append({"role": role, "content": "\n".join(text_parts)})

        return result

    def _convert_tools(self, tools: list[dict]) -> list[dict]:
        """Convert Anthropic tool format to OpenAI function calling format."""
        result = []
        for tool in tools:
            result.append({
                "type": "function",
                "function": {
                    "name": tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {}),
                },
            })
        return result

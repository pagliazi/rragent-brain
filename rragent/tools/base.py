"""
Tool base class — inspired by claude-code Tool.ts.

Every tool has:
  - name, description, input_schema
  - is_tier0: always loaded in prompt
  - should_defer: schema loaded only via tool_search
  - is_concurrent_safe: can run in parallel with other tools
  - timeout: max execution time
  - max_result_size: large results get persisted to disk
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolSpec:
    """Tool metadata for registration and routing."""
    name: str
    description: str
    input_schema: dict[str, Any]
    is_tier0: bool = False
    should_defer: bool = True
    is_concurrent_safe: bool = True
    timeout: float = 30.0
    max_result_size: int = 50000
    category: str = "general"
    agent: str = ""
    keywords: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)


@dataclass
class ToolResult:
    """Result of a tool execution."""
    content: str = ""
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def error(cls, message: str) -> "ToolResult":
        return cls(content=message, is_error=True)

    @classmethod
    def success(cls, content: str, **metadata: Any) -> "ToolResult":
        return cls(content=content, metadata=metadata)


@dataclass
class ToolUse:
    """A tool invocation request from the LLM."""
    id: str
    name: str
    input: dict[str, Any]


class Tool(ABC):
    """Base class for all RRAgent tools."""

    spec: ToolSpec

    @abstractmethod
    async def call(self, input: dict[str, Any]) -> ToolResult:
        """Execute the tool with the given input."""
        ...

    def validate_input(self, input: dict[str, Any]) -> ToolResult | None:
        """Validate input against schema. Returns error ToolResult or None if valid."""
        schema = self.spec.input_schema
        required = schema.get("required", [])
        properties = schema.get("properties", {})

        for field_name in required:
            if field_name not in input:
                return ToolResult.error(
                    f"Missing required field: {field_name}\n"
                    f"Schema: {properties}"
                )
        return None

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def schema_dict(self) -> dict[str, Any]:
        """Return the tool schema for LLM API calls."""
        return {
            "name": self.spec.name,
            "description": self.spec.description,
            "input_schema": self.spec.input_schema,
        }

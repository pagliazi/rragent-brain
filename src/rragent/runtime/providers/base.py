"""Base LLM provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator


class BaseLLMProvider(ABC):
    """Base class for LLM providers."""

    @abstractmethod
    async def stream(
        self,
        messages: list[dict],
        system: str,
        tools: list[dict],
        model: str,
    ) -> AsyncGenerator[dict, None]:
        """Stream LLM response chunks."""
        ...

    @abstractmethod
    async def complete(
        self,
        messages: list[dict],
        system: str,
        tools: list[dict],
        model: str,
    ) -> dict:
        """Non-streaming completion."""
        ...

    def rotate_credential(self) -> None:
        """Rotate to next credential (override in subclass)."""
        pass

    def switch_to_fallback(self) -> bool:
        """Switch to fallback model. Returns True if switched."""
        return False

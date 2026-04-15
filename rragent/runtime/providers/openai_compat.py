"""
OpenAI-compatible Provider — for Ollama and other local models.
"""

from __future__ import annotations

import os
from typing import Any, AsyncGenerator

from rragent.runtime.providers.dashscope import DashScopeProvider


class OpenAICompatProvider(DashScopeProvider):
    """Generic OpenAI-compatible provider (Ollama, vLLM, etc.)."""

    def __init__(
        self,
        model: str = "qwen2.5-coder:14b",
        api_key: str = "ollama",
        base_url: str = "http://127.0.0.1:11434/v1",
    ):
        if "/" in model:
            parts = model.split("/", 1)
            if parts[0] == "ollama":
                model = parts[1]
                base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")

        super().__init__(model=model, api_key=api_key, base_url=base_url)

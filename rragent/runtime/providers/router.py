"""
Provider Router — wraps multiple SimpleLLMProvider instances with retry + fallback.

Routes requests through a fallback chain:
- Primary: qwen3.5-plus via DashScope
- Fallback: deepseek-chat via SiliconFlow or DeepSeek direct

On 429/529 → retry with exponential backoff
On 401/403 → try next provider
On context overflow → signal compression
"""

from __future__ import annotations

import logging
import time
from typing import Any, AsyncGenerator

from rragent.runtime.providers.simple import SimpleLLMProvider
from rragent.runtime.resilience.api_retry import ApiRetryPolicy
from rragent.runtime.resilience.circuit_breaker import CircuitBreaker
from rragent.runtime.resilience.error_classifier import RRClawErrorClassifier, ClassifiedError

logger = logging.getLogger("rragent.providers.router")


class ProviderConfig:
    """Configuration for a single provider in the fallback chain."""

    def __init__(
        self,
        name: str,
        api_key: str,
        base_url: str,
        model: str,
    ):
        self.name = name
        self.api_key = api_key
        self.base_url = base_url
        self.model = model


class ProviderRouter:
    """
    Multi-provider router implementing the LLMProvider protocol.

    Creates multiple SimpleLLMProvider instances and switches between them
    on failures. Each provider has its own circuit breaker.
    """

    def __init__(self, provider_configs: list[ProviderConfig]):
        if not provider_configs:
            raise ValueError("At least one provider config required")

        self._configs = provider_configs
        self._providers: list[SimpleLLMProvider] = []
        self._breakers: list[CircuitBreaker] = []
        self._classifier = RRClawErrorClassifier()
        self._retry = ApiRetryPolicy()
        self._current_index = 0

        # Initialize providers and circuit breakers
        for cfg in provider_configs:
            provider = SimpleLLMProvider(
                api_key=cfg.api_key,
                base_url=cfg.base_url,
                model=cfg.model,
            )
            self._providers.append(provider)
            self._breakers.append(
                CircuitBreaker(
                    name=f"provider:{cfg.name}",
                    max_failures=3,
                    cooldown=300,  # 5 min cooldown before half-open retry
                )
            )

        logger.info(
            f"ProviderRouter initialized with {len(self._providers)} providers: "
            + ", ".join(c.name for c in self._configs)
        )

    @property
    def current_provider_name(self) -> str:
        return self._configs[self._current_index].name

    @property
    def current_model(self) -> str:
        return self._configs[self._current_index].model

    async def stream(
        self,
        messages: list[dict],
        system: str,
        tools: list[dict],
        model: str,
    ) -> AsyncGenerator[dict, None]:
        """Stream with retry, circuit breaker, and provider fallback."""
        last_error = None

        # Try each provider in chain, starting from current
        for attempt_idx in range(len(self._providers)):
            idx = (self._current_index + attempt_idx) % len(self._providers)
            provider = self._providers[idx]
            breaker = self._breakers[idx]
            cfg = self._configs[idx]

            # Skip if circuit is open (unless it's our last option)
            if breaker.is_open() and attempt_idx < len(self._providers) - 1:
                logger.info(
                    f"Skipping provider {cfg.name} — circuit breaker open"
                )
                continue

            use_model = model or cfg.model

            try:
                logger.info(f"Trying provider: {cfg.name} (model: {use_model})")
                chunks = []
                provider_error = None
                async for chunk in provider.stream(messages, system, tools, use_model):
                    # Check for error chunks from SimpleLLMProvider
                    if chunk.get("type") == "error":
                        error_msg = chunk.get("message", "Unknown error")
                        exc = _ApiError(error_msg)
                        classified = self._classifier.classify(exc)

                        breaker.record_failure()
                        logger.warning(
                            f"Provider {cfg.name} returned error: {error_msg} "
                            f"(category: {classified.reason.value})"
                        )

                        if classified.should_fallback or classified.should_rotate_credential:
                            provider_error = exc
                            last_error = exc
                            break  # try next provider
                        else:
                            # Non-recoverable: yield error and stop
                            yield chunk
                            return
                    else:
                        chunks.append(chunk)

                # If we broke out due to error, skip to next provider
                if provider_error is not None:
                    continue

                # Success — record and yield all chunks
                breaker.record_success()
                if attempt_idx > 0:
                    self._current_index = idx
                    logger.info(f"Provider switch successful: now using {cfg.name}")

                for chunk in chunks:
                    yield chunk
                return

            except Exception as e:
                last_error = e
                breaker.record_failure()
                classified = self._classifier.classify(e)

                logger.warning(
                    f"Provider {cfg.name} failed: {e} "
                    f"(category: {classified.reason.value}, "
                    f"retry={classified.retryable}, "
                    f"fallback={classified.should_fallback})"
                )

                # Context overflow — don't try other providers, signal compression
                if classified.should_compress:
                    raise

                # Continue to next provider
                continue

        # All providers exhausted
        if last_error:
            raise last_error
        raise RuntimeError("All providers exhausted")

    def rotate_credential(self) -> None:
        """Rotate to next provider in the chain."""
        old = self.current_provider_name
        self.switch_to_fallback()
        logger.info(f"Credential rotation: {old} -> {self.current_provider_name}")

    def switch_to_fallback(self) -> bool:
        """Switch to next provider in fallback chain."""
        if len(self._providers) <= 1:
            return False

        old_idx = self._current_index
        self._current_index = (self._current_index + 1) % len(self._providers)
        logger.warning(
            f"Switched provider: {self._configs[old_idx].name} -> "
            f"{self._configs[self._current_index].name}"
        )
        return True

    def reset_to_primary(self):
        """Reset to primary provider."""
        self._current_index = 0
        logger.info(f"Reset to primary provider: {self._configs[0].name}")

    def status(self) -> dict:
        """Return status of all providers."""
        return {
            "current": self.current_provider_name,
            "providers": [
                {
                    "name": cfg.name,
                    "model": cfg.model,
                    "circuit_breaker": breaker.status(),
                }
                for cfg, breaker in zip(self._configs, self._breakers)
            ],
        }


class _ApiError(Exception):
    """Synthetic API error for classifying error messages from SimpleLLMProvider."""

    def __init__(self, message: str):
        super().__init__(message)
        # Try to extract status code from message
        self.status_code = 0
        msg_lower = message.lower()
        if "401" in message or "unauthorized" in msg_lower or "authentication" in msg_lower:
            self.status_code = 401
        elif "403" in message or "forbidden" in msg_lower:
            self.status_code = 403
        elif "429" in message or "rate" in msg_lower:
            self.status_code = 429
        elif "529" in message or "overloaded" in msg_lower:
            self.status_code = 529
        elif "400" in message and ("context" in msg_lower or "token" in msg_lower):
            self.status_code = 400
        elif "500" in message or "502" in message or "503" in message:
            self.status_code = 500

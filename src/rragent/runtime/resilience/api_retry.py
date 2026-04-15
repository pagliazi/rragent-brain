"""
API Retry with exponential backoff + model fallback.

Inspired by claude-code withRetry:
- 500ms base, 2x growth, 32s cap, 25% jitter
- Max 10 retries
- 429/529: respect retry-after header
- 529 × 3: trigger model fallback
- 401/403: auto refresh credentials
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, Callable, Awaitable

logger = logging.getLogger("rragent.resilience.api_retry")


class ApiRetryPolicy:
    MAX_RETRIES = 10
    BASE_DELAY_MS = 500
    MAX_BACKOFF_MS = 32_000
    JITTER_FACTOR = 0.25
    MAX_529_BEFORE_FALLBACK = 3

    def __init__(self):
        self._consecutive_529 = 0

    async def call_with_retry(
        self,
        fn: Callable[..., Awaitable],
        *,
        on_credential_rotate: Callable | None = None,
        on_model_fallback: Callable | None = None,
        on_reconnect: Callable | None = None,
    ) -> Any:
        """Call fn with retry, backoff, and fallback."""
        last_error = None

        for attempt in range(self.MAX_RETRIES):
            try:
                result = await fn()
                self._consecutive_529 = 0
                return result

            except Exception as e:
                last_error = e
                status = getattr(e, "status_code", getattr(e, "status", 0))
                retry_after = getattr(e, "retry_after", None)

                # Rate limit (429) or overloaded (529)
                if status in (429, 529):
                    if status == 529:
                        self._consecutive_529 += 1
                        if (
                            self._consecutive_529 >= self.MAX_529_BEFORE_FALLBACK
                            and on_model_fallback
                        ):
                            logger.warning(
                                f"529 × {self._consecutive_529}, switching to fallback model"
                            )
                            on_model_fallback()
                            self._consecutive_529 = 0

                    delay = self._backoff(attempt, retry_after)
                    logger.info(f"Rate limited ({status}), retry in {delay:.1f}s (attempt {attempt + 1})")
                    await asyncio.sleep(delay)
                    continue

                # Auth error
                if status in (401, 403):
                    if on_credential_rotate:
                        logger.info("Auth error, rotating credentials")
                        on_credential_rotate()
                        continue
                    raise

                # Connection error
                if isinstance(e, (ConnectionError, OSError)):
                    if on_reconnect:
                        logger.info("Connection error, reconnecting")
                        await on_reconnect()

                    delay = self._backoff(attempt)
                    await asyncio.sleep(delay)
                    continue

                # Context overflow
                if status == 400 and "context" in str(e).lower():
                    raise  # Let caller handle compression

                # Other errors: backoff and retry
                delay = self._backoff(attempt)
                logger.warning(
                    f"API error (attempt {attempt + 1}/{self.MAX_RETRIES}): {e}"
                )
                await asyncio.sleep(delay)

        raise last_error or RuntimeError("Max retries exceeded")

    def _backoff(self, attempt: int, retry_after: float | None = None) -> float:
        """Exponential backoff with jitter."""
        if retry_after:
            return float(retry_after)

        delay_ms = self.BASE_DELAY_MS * (2 ** attempt)
        delay_ms = min(delay_ms, self.MAX_BACKOFF_MS)

        # Add jitter
        jitter = delay_ms * self.JITTER_FACTOR * random.random()
        return (delay_ms + jitter) / 1000.0

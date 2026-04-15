"""
Error Classifier — categorizes API errors for recovery decisions.

Inspired by hermes-agent error_classifier.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class FailoverReason(str, Enum):
    AUTH = "auth"
    BILLING = "billing"
    RATE_LIMIT = "rate_limit"
    OVERLOADED = "overloaded"
    SERVER_ERROR = "server_error"
    TIMEOUT = "timeout"
    CONTEXT_OVERFLOW = "context_overflow"
    MODEL_NOT_FOUND = "model_not_found"
    FORMAT_ERROR = "format_error"
    CONNECTION = "connection"
    UNKNOWN = "unknown"


@dataclass
class ClassifiedError:
    reason: FailoverReason
    retryable: bool
    should_compress: bool = False
    should_rotate_credential: bool = False
    should_fallback: bool = False
    cooldown_seconds: int = 0
    message: str = ""

    @property
    def should_retry(self) -> bool:
        return self.retryable

    @property
    def retry_after_ms(self) -> int:
        return self.cooldown_seconds * 1000

    @property
    def category(self) -> str:
        return self.reason.value


class RRClawErrorClassifier:
    """Classify errors and return recovery hints."""

    def classify(self, error: Exception) -> ClassifiedError:
        return self._classify_error(error)

    def _classify_error(self, error: Exception) -> ClassifiedError:
        status = getattr(error, "status_code", getattr(error, "status", 0))
        msg = str(error).lower()

        # Auth errors
        if status in (401, 403) or "authentication" in msg or "unauthorized" in msg:
            return ClassifiedError(
                reason=FailoverReason.AUTH,
                retryable=True,
                should_rotate_credential=True,
                message="Authentication failed",
            )

        # Billing
        if status == 402 or "billing" in msg or "insufficient" in msg:
            return ClassifiedError(
                reason=FailoverReason.BILLING,
                retryable=True,
                should_rotate_credential=True,
                cooldown_seconds=3600,
                message="Billing error",
            )

        # Rate limit
        if status == 429:
            return ClassifiedError(
                reason=FailoverReason.RATE_LIMIT,
                retryable=True,
                should_rotate_credential=True,
                cooldown_seconds=60,
                message="Rate limited",
            )

        # Overloaded
        if status == 529 or "overloaded" in msg:
            return ClassifiedError(
                reason=FailoverReason.OVERLOADED,
                retryable=True,
                should_fallback=True,
                message="Service overloaded",
            )

        # Context overflow
        if (
            status == 400
            and ("context" in msg or "token" in msg or "too long" in msg)
        ):
            return ClassifiedError(
                reason=FailoverReason.CONTEXT_OVERFLOW,
                retryable=True,
                should_compress=True,
                message="Context window exceeded",
            )

        # Model not found
        if status == 404 and "model" in msg:
            return ClassifiedError(
                reason=FailoverReason.MODEL_NOT_FOUND,
                retryable=True,
                should_fallback=True,
                message="Model not found",
            )

        # Server error
        if status >= 500:
            return ClassifiedError(
                reason=FailoverReason.SERVER_ERROR,
                retryable=True,
                should_fallback=True,
                message=f"Server error ({status})",
            )

        # Connection
        if isinstance(error, (ConnectionError, OSError, TimeoutError)):
            return ClassifiedError(
                reason=FailoverReason.CONNECTION,
                retryable=True,
                message="Connection error",
            )

        # Timeout
        if "timeout" in msg:
            return ClassifiedError(
                reason=FailoverReason.TIMEOUT,
                retryable=True,
                message="Request timeout",
            )

        return ClassifiedError(
            reason=FailoverReason.UNKNOWN,
            retryable=False,
            message=str(error),
        )

"""
Credential Pool — 4 strategies for API key rotation.

Inspired by hermes-agent credential_pool.py:
- fill_first: exhaust one key before moving to next
- round_robin: rotate evenly
- random: prevent thundering herd
- least_used: minimize per-key usage

+ 1 hour cooldown on 429/402 errors
"""

from __future__ import annotations

import random as rng
import time
from dataclasses import dataclass, field
from enum import Enum


class RotationStrategy(str, Enum):
    FILL_FIRST = "fill_first"
    ROUND_ROBIN = "round_robin"
    RANDOM = "random"
    LEAST_USED = "least_used"


@dataclass
class Credential:
    key: str
    provider: str = ""
    usage_count: int = 0
    cooldown_until: float = 0
    last_used: float = 0
    is_active: bool = True


class CredentialPool:
    """Manage multiple API keys with rotation strategies."""

    COOLDOWN_DURATION = 3600  # 1 hour

    def __init__(
        self,
        strategy: RotationStrategy = RotationStrategy.ROUND_ROBIN,
    ):
        self.strategy = strategy
        self._credentials: list[Credential] = []
        self._current_index = 0

    def add(self, key: str, provider: str = ""):
        self._credentials.append(Credential(key=key, provider=provider))

    def get(self) -> str | None:
        """Get next credential based on strategy."""
        available = [
            c for c in self._credentials
            if c.is_active and time.time() > c.cooldown_until
        ]
        if not available:
            # Try all credentials including cooled down
            available = [c for c in self._credentials if c.is_active]
            if not available:
                return None

        if self.strategy == RotationStrategy.FILL_FIRST:
            cred = available[0]
        elif self.strategy == RotationStrategy.ROUND_ROBIN:
            self._current_index = self._current_index % len(available)
            cred = available[self._current_index]
            self._current_index += 1
        elif self.strategy == RotationStrategy.RANDOM:
            cred = rng.choice(available)
        elif self.strategy == RotationStrategy.LEAST_USED:
            cred = min(available, key=lambda c: c.usage_count)
        else:
            cred = available[0]

        cred.usage_count += 1
        cred.last_used = time.time()
        return cred.key

    def mark_rate_limited(self, key: str):
        """Put a credential on cooldown."""
        for c in self._credentials:
            if c.key == key:
                c.cooldown_until = time.time() + self.COOLDOWN_DURATION
                break

    def mark_disabled(self, key: str):
        """Permanently disable a credential."""
        for c in self._credentials:
            if c.key == key:
                c.is_active = False
                break

    @property
    def available_count(self) -> int:
        now = time.time()
        return sum(
            1 for c in self._credentials
            if c.is_active and now > c.cooldown_until
        )

    def stats(self) -> list[dict]:
        now = time.time()
        return [
            {
                "provider": c.provider,
                "key_prefix": c.key[:8] + "...",
                "usage": c.usage_count,
                "active": c.is_active,
                "cooled_down": now < c.cooldown_until,
            }
            for c in self._credentials
        ]

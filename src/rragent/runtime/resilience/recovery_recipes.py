"""
Recovery Recipes — structured failure recovery for 7 scenarios.

Inspired by claw-code recovery_recipes.rs.
One attempt per recipe before escalation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Awaitable

logger = logging.getLogger("rragent.resilience.recovery")


class FailureScenario(str, Enum):
    REDIS_CONNECTION_LOST = "redis_lost"
    GATEWAY_DISCONNECTED = "gateway_dc"
    PYAGENT_TIMEOUT = "pyagent_timeout"
    HERMES_CRASH = "hermes_crash"
    MODEL_OVERLOADED = "model_overloaded"
    TOOL_DEGRADED = "tool_degraded"
    MEMORY_CORRUPTION = "memory_corrupt"


class EscalationPolicy(str, Enum):
    DEGRADE_TO_LOCAL = "degrade_local"
    QUEUE_AND_RETRY = "queue_retry"
    FALLBACK_TO_HERMES = "fallback_hermes"
    LOG_AND_CONTINUE = "log_continue"
    ALERT_USER = "alert_user"
    DISABLE_TOOL = "disable_tool"
    FRESH_SESSION = "fresh_session"


@dataclass
class RecoveryStep:
    name: str
    action: Callable[..., Awaitable[bool]]
    description: str = ""


@dataclass
class RecoveryRecipe:
    scenario: FailureScenario
    steps: list[RecoveryStep]
    escalation: EscalationPolicy
    max_attempts: int = 1
    description: str = ""


class RecoveryEngine:
    """Execute recovery recipes for failure scenarios."""

    def __init__(self):
        self._recipes: dict[FailureScenario, RecoveryRecipe] = {}
        self._escalation_handlers: dict[EscalationPolicy, Callable] = {}

    def register_recipe(self, recipe: RecoveryRecipe):
        self._recipes[recipe.scenario] = recipe

    def register_escalation(self, policy: EscalationPolicy, handler: Callable):
        self._escalation_handlers[policy] = handler

    async def recover(self, scenario: FailureScenario, context: dict[str, Any] = None) -> bool:
        """Attempt recovery for a failure scenario."""
        recipe = self._recipes.get(scenario)
        if not recipe:
            logger.warning(f"No recovery recipe for {scenario}")
            return False

        logger.info(f"Executing recovery for {scenario.value}...")

        # Try recovery steps
        for attempt in range(recipe.max_attempts):
            all_ok = True
            for step in recipe.steps:
                try:
                    success = await step.action(**(context or {}))
                    if not success:
                        all_ok = False
                        break
                except Exception as e:
                    logger.error(f"Recovery step '{step.name}' failed: {e}")
                    all_ok = False
                    break

            if all_ok:
                logger.info(f"Recovery for {scenario.value} succeeded")
                return True

        # Escalate
        logger.warning(
            f"Recovery for {scenario.value} failed, "
            f"escalating to {recipe.escalation.value}"
        )
        handler = self._escalation_handlers.get(recipe.escalation)
        if handler:
            try:
                await handler(scenario, context)
            except Exception as e:
                logger.error(f"Escalation handler failed: {e}")

        return False

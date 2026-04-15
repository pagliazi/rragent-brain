"""
Webhook Handler — receive and send webhooks for lifecycle events.

Integrates with RRAgent hook system:
- Receives evolution notifications
- Sends alerts to external systems
- Gateway hook mappings support
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Coroutine

logger = logging.getLogger("rragent.channels.webhook")


class WebhookHandler:
    """
    Handle incoming and outgoing webhooks.

    Incoming: POST /webhook/<event_type>
    - evolution_update: skill created, memory updated
    - health_alert: component degraded/down
    - task_complete: background task finished

    Outgoing: POST to configured URLs
    - Notify Gateway of evolution updates
    - Alert Slack/Feishu on critical events
    """

    def __init__(
        self,
        outgoing_urls: dict[str, str] | None = None,
    ):
        self._outgoing = outgoing_urls or {}
        self._handlers: dict[str, list[Callable]] = {}
        self._history: list[dict] = []

    def register_handler(
        self,
        event_type: str,
        handler: Callable[[dict], Coroutine],
    ):
        """Register a handler for incoming webhook events."""
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)

    async def receive(self, event_type: str, payload: dict):
        """Process an incoming webhook event."""
        self._history.append({
            "direction": "incoming",
            "type": event_type,
            "payload": payload,
        })

        handlers = self._handlers.get(event_type, [])
        for handler in handlers:
            try:
                await handler(payload)
            except Exception as e:
                logger.error(f"Webhook handler error for {event_type}: {e}")

    async def send(
        self,
        event_type: str,
        payload: dict,
        target: str = "",
    ):
        """Send an outgoing webhook."""
        url = target or self._outgoing.get(event_type, "")
        if not url:
            logger.debug(f"No webhook URL for event: {event_type}")
            return

        self._history.append({
            "direction": "outgoing",
            "type": event_type,
            "url": url,
            "payload": payload,
        })

        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json={"type": event_type, "body": payload},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status >= 400:
                        logger.warning(
                            f"Webhook to {url} returned {resp.status}"
                        )
        except ImportError:
            logger.debug("aiohttp not available for outgoing webhooks")
        except Exception as e:
            logger.warning(f"Webhook send failed to {url}: {e}")

    async def notify_evolution(self, summary: str, skills: list[str] | None = None):
        """Send evolution update notification."""
        await self.send("evolution_update", {
            "summary": summary,
            "skills_created": skills or [],
        })

    async def notify_health_alert(self, component: str, status: str, details: str = ""):
        """Send health alert notification."""
        await self.send("health_alert", {
            "component": component,
            "status": status,
            "details": details,
        })

    @property
    def history(self) -> list[dict]:
        return list(self._history[-100:])

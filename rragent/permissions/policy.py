"""
Permission Policy — 4-tier permission model.

Tiers:
- SAFE: auto-allow (read files, market queries)
- AWARE: allow with logging (web search, general queries)
- CONSENT: require user confirmation (shell commands, file writes)
- CRITICAL: deny by default (destructive ops, deploy)
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class PermissionTier(str, Enum):
    SAFE = "safe"
    AWARE = "aware"
    CONSENT = "consent"
    CRITICAL = "critical"


class PermissionDecision(str, Enum):
    ALLOW = "allow"
    ALLOW_WITH_LOG = "allow_with_log"
    ASK_USER = "ask_user"
    DENY = "deny"


# Tool → Permission tier mapping
TOOL_PERMISSIONS: dict[str, PermissionTier] = {
    # SAFE — auto-allow
    "tool_search": PermissionTier.SAFE,
    "read_file": PermissionTier.SAFE,
    "market_query": PermissionTier.SAFE,
    "memory": PermissionTier.SAFE,
    # PyAgent read-only
    "pyagent_zt": PermissionTier.SAFE,
    "pyagent_lb": PermissionTier.SAFE,
    "pyagent_bk": PermissionTier.SAFE,
    "pyagent_hot": PermissionTier.SAFE,
    "pyagent_summary": PermissionTier.SAFE,
    "pyagent_bt_cache": PermissionTier.SAFE,
    "pyagent_ledger": PermissionTier.SAFE,
    "pyagent_strategy_list": PermissionTier.SAFE,
    "pyagent_calendar": PermissionTier.SAFE,
    "pyagent_sysinfo": PermissionTier.SAFE,
    "pyagent_alerts": PermissionTier.SAFE,
    "pyagent_news": PermissionTier.SAFE,
    "pyagent_git_status": PermissionTier.SAFE,
    "pyagent_git_log": PermissionTier.SAFE,

    # AWARE — allow with logging
    "pyagent_web_search": PermissionTier.AWARE,
    "pyagent_research": PermissionTier.AWARE,
    "pyagent_ask": PermissionTier.AWARE,
    "pyagent_q": PermissionTier.AWARE,
    "pyagent_translate": PermissionTier.AWARE,
    "pyagent_summarize": PermissionTier.AWARE,
    "pyagent_explain": PermissionTier.AWARE,

    # CONSENT — require confirmation
    "bash": PermissionTier.CONSENT,
    "write_file": PermissionTier.CONSENT,
    "edit_file": PermissionTier.CONSENT,
    "pyagent_ssh": PermissionTier.CONSENT,
    "pyagent_local": PermissionTier.CONSENT,
    "pyagent_backtest": PermissionTier.CONSENT,
    "pyagent_qv": PermissionTier.CONSENT,
    "pyagent_claude": PermissionTier.CONSENT,
    "pyagent_mail": PermissionTier.CONSENT,

    # CRITICAL — deny by default
    "pyagent_deploy": PermissionTier.CRITICAL,
    "pyagent_git_sync": PermissionTier.CRITICAL,
}


class PermissionPolicy:
    """Evaluate tool permissions."""

    def __init__(self, auto_approve_consent: bool = True):
        """
        auto_approve_consent: If True, CONSENT tools run without asking.
        Set to False for interactive mode.
        """
        self.auto_approve_consent = auto_approve_consent
        self._overrides: dict[str, PermissionDecision] = {}

    def check(self, tool_name: str) -> PermissionDecision:
        """Check permission for a tool."""
        # Check overrides first
        if tool_name in self._overrides:
            return self._overrides[tool_name]

        tier = TOOL_PERMISSIONS.get(tool_name, PermissionTier.AWARE)

        if tier == PermissionTier.SAFE:
            return PermissionDecision.ALLOW
        elif tier == PermissionTier.AWARE:
            return PermissionDecision.ALLOW_WITH_LOG
        elif tier == PermissionTier.CONSENT:
            if self.auto_approve_consent:
                return PermissionDecision.ALLOW_WITH_LOG
            return PermissionDecision.ASK_USER
        elif tier == PermissionTier.CRITICAL:
            return PermissionDecision.ASK_USER

        return PermissionDecision.ALLOW_WITH_LOG

    def override(self, tool_name: str, decision: PermissionDecision):
        """Set a permission override for a tool."""
        self._overrides[tool_name] = decision

    def get_tier(self, tool_name: str) -> PermissionTier:
        return TOOL_PERMISSIONS.get(tool_name, PermissionTier.AWARE)

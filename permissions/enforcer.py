"""
Permission Enforcer — workspace boundary + command safety checks.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .policy import PermissionPolicy, PermissionDecision


class PermissionEnforcer:
    """Enforce workspace boundaries and command safety."""

    # Dangerous command patterns
    DANGEROUS_PATTERNS = [
        r'\brm\s+-rf\b',
        r'\bgit\s+push\s+--force\b',
        r'\bgit\s+reset\s+--hard\b',
        r'\bdrop\s+table\b',
        r'\btruncate\s+table\b',
        r'\bkill\s+-9\b',
        r'\bmkfs\b',
        r'\bdd\s+if=',
        r'\b:()\s*\{\s*:\|:\s*&\s*\}',  # fork bomb
    ]

    def __init__(
        self,
        policy: PermissionPolicy,
        workspace_root: str = "",
        allowed_dirs: list[str] | None = None,
    ):
        self.policy = policy
        self.workspace_root = workspace_root
        self.allowed_dirs = allowed_dirs or []

    def check_tool(self, tool_name: str, input_data: dict) -> PermissionDecision:
        """Check tool permission with input-aware enforcement."""
        base_decision = self.policy.check(tool_name)

        # Additional checks for specific tools
        if tool_name == "bash":
            command = input_data.get("command", "")
            if self._is_dangerous_command(command):
                return PermissionDecision.DENY
            if self._is_read_only_command(command):
                return PermissionDecision.ALLOW

        elif tool_name in ("write_file", "edit_file"):
            file_path = input_data.get("file_path", "")
            if not self._is_within_workspace(file_path):
                return PermissionDecision.DENY

        return base_decision

    def _is_dangerous_command(self, command: str) -> bool:
        """Check if a shell command matches dangerous patterns."""
        for pattern in self.DANGEROUS_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                return True
        return False

    def _is_read_only_command(self, command: str) -> bool:
        """Check if a command is read-only (safe)."""
        read_only_prefixes = [
            "ls", "cat", "head", "tail", "wc", "du", "df",
            "pwd", "whoami", "date", "echo", "printf",
            "git status", "git log", "git diff", "git show",
            "pip list", "pip show", "python --version",
            "which", "type", "file", "stat",
        ]
        cmd_stripped = command.strip()
        for prefix in read_only_prefixes:
            if cmd_stripped.startswith(prefix):
                return True
        return False

    def _is_within_workspace(self, file_path: str) -> bool:
        """Check if a file path is within allowed workspace."""
        if not self.workspace_root and not self.allowed_dirs:
            return True  # No restrictions configured

        try:
            resolved = Path(file_path).resolve()
            if self.workspace_root:
                workspace = Path(self.workspace_root).resolve()
                if resolved.is_relative_to(workspace):
                    return True
            for allowed in self.allowed_dirs:
                if resolved.is_relative_to(Path(allowed).resolve()):
                    return True
        except (ValueError, OSError):
            pass

        return False

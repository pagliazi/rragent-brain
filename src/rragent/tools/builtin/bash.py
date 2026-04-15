"""Bash tool — execute shell commands."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from rragent.tools.base import Tool, ToolSpec, ToolResult


class BashTool(Tool):
    spec = ToolSpec(
        name="bash",
        description="Execute a shell command and return stdout/stderr.",
        input_schema={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute",
                },
                "timeout": {
                    "type": "number",
                    "description": "Timeout in seconds (default 120)",
                    "default": 120,
                },
                "working_dir": {
                    "type": "string",
                    "description": "Working directory (default: current)",
                },
            },
            "required": ["command"],
        },
        is_tier0=True,
        should_defer=False,
        is_concurrent_safe=False,
        timeout=120,
        category="system",
    )

    async def call(self, input: dict[str, Any]) -> ToolResult:
        command = input["command"]
        timeout = input.get("timeout", 120)
        cwd = input.get("working_dir") or None

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env={**os.environ},
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )

            output_parts = []
            if stdout:
                output_parts.append(stdout.decode("utf-8", errors="replace"))
            if stderr:
                output_parts.append(f"STDERR:\n{stderr.decode('utf-8', errors='replace')}")

            output = "\n".join(output_parts) or "(no output)"

            if proc.returncode != 0:
                return ToolResult(
                    content=f"Exit code: {proc.returncode}\n{output}",
                    is_error=True,
                )

            return ToolResult.success(output)

        except asyncio.TimeoutError:
            return ToolResult.error(f"Command timed out after {timeout}s: {command[:100]}")
        except Exception as e:
            return ToolResult.error(f"Failed to execute: {e}")

"""File operation tools — read, write, edit."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from rragent.tools.base import Tool, ToolSpec, ToolResult


class ReadFileTool(Tool):
    spec = ToolSpec(
        name="read_file",
        description="Read a file's contents. Supports text files and returns line-numbered output.",
        input_schema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute path to the file"},
                "offset": {"type": "integer", "description": "Line number to start from (1-based)"},
                "limit": {"type": "integer", "description": "Number of lines to read"},
            },
            "required": ["file_path"],
        },
        is_tier0=True,
        should_defer=False,
        is_concurrent_safe=True,
        timeout=10,
        category="file",
    )

    async def call(self, input: dict[str, Any]) -> ToolResult:
        path = Path(input["file_path"])
        offset = input.get("offset", 1)
        limit = input.get("limit", 2000)

        if not path.exists():
            return ToolResult.error(f"File not found: {path}")
        if not path.is_file():
            return ToolResult.error(f"Not a file: {path}")

        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            start = max(0, offset - 1)
            end = start + limit
            selected = lines[start:end]

            numbered = []
            for i, line in enumerate(selected, start=start + 1):
                numbered.append(f"{i:>6}\t{line}")

            return ToolResult.success("\n".join(numbered))
        except Exception as e:
            return ToolResult.error(f"Error reading {path}: {e}")


class WriteFileTool(Tool):
    spec = ToolSpec(
        name="write_file",
        description="Write content to a file, creating directories if needed.",
        input_schema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute path to write to"},
                "content": {"type": "string", "description": "Content to write"},
            },
            "required": ["file_path", "content"],
        },
        is_tier0=True,
        should_defer=False,
        is_concurrent_safe=False,
        timeout=10,
        category="file",
    )

    async def call(self, input: dict[str, Any]) -> ToolResult:
        path = Path(input["file_path"])
        content = input["content"]

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return ToolResult.success(f"Written {len(content)} chars to {path}")
        except Exception as e:
            return ToolResult.error(f"Error writing {path}: {e}")


class EditFileTool(Tool):
    spec = ToolSpec(
        name="edit_file",
        description="Replace a specific string in a file with new content.",
        input_schema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute path to the file"},
                "old_string": {"type": "string", "description": "Exact string to find and replace"},
                "new_string": {"type": "string", "description": "Replacement string"},
                "replace_all": {"type": "boolean", "description": "Replace all occurrences", "default": False},
            },
            "required": ["file_path", "old_string", "new_string"],
        },
        is_tier0=True,
        should_defer=False,
        is_concurrent_safe=False,
        timeout=10,
        category="file",
    )

    async def call(self, input: dict[str, Any]) -> ToolResult:
        path = Path(input["file_path"])
        old = input["old_string"]
        new = input["new_string"]
        replace_all = input.get("replace_all", False)

        if not path.exists():
            return ToolResult.error(f"File not found: {path}")

        try:
            content = path.read_text(encoding="utf-8")
            count = content.count(old)

            if count == 0:
                return ToolResult.error(
                    f"String not found in {path}. "
                    f"Make sure old_string matches exactly."
                )
            if count > 1 and not replace_all:
                return ToolResult.error(
                    f"Found {count} occurrences of old_string in {path}. "
                    f"Use replace_all=true or provide more context for uniqueness."
                )

            if replace_all:
                new_content = content.replace(old, new)
            else:
                new_content = content.replace(old, new, 1)

            path.write_text(new_content, encoding="utf-8")
            return ToolResult.success(
                f"Replaced {'all ' + str(count) if replace_all else '1'} "
                f"occurrence(s) in {path}"
            )
        except Exception as e:
            return ToolResult.error(f"Error editing {path}: {e}")

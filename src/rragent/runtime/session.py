"""
Session — JSONL persistence with rotation and crash recovery.

Inspired by claw-code session JSONL (256KB rotation).
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger("rragent.session")


@dataclass
class Message:
    """A single message in the conversation."""
    role: Literal["user", "assistant", "tool", "system"]
    content: Any = ""
    tool_use_id: str = ""
    tool_uses: list[dict] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        # Remove empty fields to save space
        return {k: v for k, v in d.items() if v}

    @classmethod
    def from_dict(cls, data: dict) -> "Message":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class UsageRecord:
    """Token usage for a single LLM call."""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    model: str = ""
    timestamp: float = field(default_factory=time.time)


class Session:
    """
    Conversation session with JSONL persistence.

    Features:
    - Append-only writes (crash-safe)
    - 256KB auto-rotation with gzip archival
    - Crash recovery from JSONL
    - Usage tracking
    """

    def __init__(
        self,
        session_id: str | None = None,
        session_dir: str = "~/.rragent/sessions",
        rotation_size: int = 262144,
    ):
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self.session_dir = Path(os.path.expanduser(session_dir))
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.rotation_size = rotation_size

        self.messages: list[Message] = []
        self.usage_records: list[UsageRecord] = []
        self.discovered_tool_schemas: dict[str, dict] = {}
        self.user_preferences: str = ""

        self._file = open(self._path(), "a", encoding="utf-8")

    def _path(self) -> Path:
        return self.session_dir / f"{self.session_id}.jsonl"

    def append_user(self, text: str):
        msg = Message(role="user", content=text)
        self.messages.append(msg)
        self._write_jsonl(msg)

    def append_assistant(self, content: str = "", tool_uses: list[dict] | None = None):
        msg = Message(role="assistant", content=content, tool_uses=tool_uses or [])
        self.messages.append(msg)
        self._write_jsonl(msg)

    def append_tool_result(self, tool_use_id: str, content: str, is_error: bool = False):
        msg = Message(
            role="tool",
            tool_use_id=tool_use_id,
            content=content,
            metadata={"is_error": is_error} if is_error else {},
        )
        self.messages.append(msg)
        self._write_jsonl(msg)

    def append_system(self, text: str):
        msg = Message(role="system", content=text)
        self.messages.append(msg)
        self._write_jsonl(msg)

    def record_usage(self, usage: UsageRecord):
        self.usage_records.append(usage)

    def total_usage(self) -> dict[str, int]:
        return {
            "input_tokens": sum(u.input_tokens for u in self.usage_records),
            "output_tokens": sum(u.output_tokens for u in self.usage_records),
            "total_calls": len(self.usage_records),
        }

    def persist(self):
        """Flush to disk and check rotation."""
        self._file.flush()
        try:
            if os.path.getsize(self._path()) > self.rotation_size:
                self._rotate()
        except OSError:
            pass

    def _write_jsonl(self, msg: Message):
        line = json.dumps(msg.to_dict(), ensure_ascii=False)
        self._file.write(line + "\n")

    def _rotate(self):
        """Close, gzip, and create new file."""
        self._file.close()
        src = self._path()
        dst = src.with_suffix(f".{int(time.time())}.jsonl.gz")
        with open(src, "rb") as f_in, gzip.open(dst, "wb") as f_out:
            f_out.writelines(f_in)
        src.write_text("")  # truncate
        self._file = open(self._path(), "a", encoding="utf-8")
        logger.info(f"Session rotated: {dst}")

    @classmethod
    def restore(cls, session_id: str, session_dir: str = "~/.rragent/sessions") -> "Session":
        """Restore session from JSONL (crash recovery)."""
        session = cls(session_id=session_id, session_dir=session_dir)
        path = session._path()
        if path.exists():
            for line in open(path, encoding="utf-8"):
                line = line.strip()
                if line:
                    try:
                        msg = Message.from_dict(json.loads(line))
                        session.messages.append(msg)
                    except (json.JSONDecodeError, Exception) as e:
                        logger.warning(f"Skipping corrupt JSONL line: {e}")
        logger.info(f"Session restored: {session_id} ({len(session.messages)} messages)")
        return session

    def to_api_messages(self) -> list[dict]:
        """Convert session messages to API format for LLM calls."""
        api_msgs = []
        for msg in self.messages:
            if msg.role == "user":
                api_msgs.append({"role": "user", "content": msg.content})
            elif msg.role == "assistant":
                content = []
                if msg.content:
                    content.append({"type": "text", "text": msg.content})
                for tu in msg.tool_uses:
                    content.append({
                        "type": "tool_use",
                        "id": tu.get("id", ""),
                        "name": tu.get("name", ""),
                        "input": tu.get("input", {}),
                    })
                api_msgs.append({"role": "assistant", "content": content})
            elif msg.role == "tool":
                api_msgs.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": msg.tool_use_id,
                        "content": msg.content,
                        "is_error": msg.metadata.get("is_error", False),
                    }],
                })
            elif msg.role == "system":
                # System messages injected as user messages with system tag
                api_msgs.append({
                    "role": "user",
                    "content": f"<system>{msg.content}</system>",
                })
        return api_msgs

    def close(self):
        if self._file and not self._file.closed:
            self._file.flush()
            self._file.close()

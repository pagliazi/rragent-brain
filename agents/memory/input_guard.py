"""
输入安全防护 (Input Guard)
检测并阻止 prompt injection、过长输入、恶意指令等。
在 Agent 处理用户输入前调用 guard_input()。

防护链:
  1. ClawDefender (社区 skill, 90+ 模式) — 覆盖 prompt injection / command injection /
     SSRF / 凭证泄露 / 路径遍历, shell pipe 集成
  2. 内置正则 (24 模式) — 兜底，当 ClawDefender 不可用时启用
  3. security-auditor (OpenClaw workspace skill) — OWASP Top 10 级别审计
"""

import logging
import os
import re
import subprocess
from typing import Optional

from agents.memory.config import INPUT_GUARD_CONFIG

logger = logging.getLogger("memory.input_guard")

# ClawDefender 脚本路径 — 两种安装位置都检查
_CLAWDEFENDER_PATHS = [
    os.path.expanduser("~/skills/clawdefender/scripts/sanitize.sh"),
    os.path.expanduser("~/openclaw/skills/clawdefender/scripts/sanitize.sh"),
]
_CLAWDEFENDER_AUDIT_PATHS = [
    os.path.expanduser("~/skills/clawdefender/scripts/clawdefender.sh"),
    os.path.expanduser("~/openclaw/skills/clawdefender/scripts/clawdefender.sh"),
]

_clawdefender_sanitize: Optional[str] = None
_clawdefender_audit: Optional[str] = None


def _find_script(paths: list[str]) -> Optional[str]:
    for p in paths:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def _ensure_scripts():
    global _clawdefender_sanitize, _clawdefender_audit
    if _clawdefender_sanitize is None:
        _clawdefender_sanitize = _find_script(_CLAWDEFENDER_PATHS) or ""
        _clawdefender_audit = _find_script(_CLAWDEFENDER_AUDIT_PATHS) or ""
        if _clawdefender_sanitize:
            logger.info(f"ClawDefender sanitize: {_clawdefender_sanitize}")
        else:
            logger.warning("ClawDefender not found, falling back to built-in patterns")


class InputGuardResult:
    __slots__ = ("safe", "reason", "sanitized", "source")

    def __init__(self, safe: bool, reason: str = "", sanitized: str = "", source: str = "builtin"):
        self.safe = safe
        self.reason = reason
        self.sanitized = sanitized
        self.source = source  # "clawdefender" | "builtin"


_COMPILED_PATTERNS: Optional[list[re.Pattern]] = None


def _get_patterns() -> list[re.Pattern]:
    global _COMPILED_PATTERNS
    if _COMPILED_PATTERNS is None:
        raw = INPUT_GUARD_CONFIG.get("blocked_patterns", [])
        _COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in raw]
    return _COMPILED_PATTERNS


def _clawdefender_check(text: str) -> Optional[InputGuardResult]:
    """通过 ClawDefender sanitize.sh 检查输入 (90+ 检测模式)"""
    _ensure_scripts()
    if not _clawdefender_sanitize:
        return None
    try:
        proc = subprocess.run(
            [_clawdefender_sanitize, "--strict"],
            input=text, capture_output=True, text=True, timeout=5,
        )
        if proc.returncode != 0:
            # --strict 模式下检测到注入会 exit 1
            reason = proc.stderr.strip() or proc.stdout.strip() or "ClawDefender 检测到安全威胁"
            # 提取关键信息
            for line in (proc.stderr + proc.stdout).splitlines():
                if "FLAGGED" in line or "injection" in line.lower() or "detected" in line.lower():
                    reason = line.strip()
                    break
            logger.warning(f"ClawDefender blocked input: {reason[:200]}")
            return InputGuardResult(safe=False, reason=f"🛡️ {reason}", source="clawdefender")
        # 安全通过
        sanitized = proc.stdout or text
        return InputGuardResult(safe=True, sanitized=sanitized, source="clawdefender")
    except subprocess.TimeoutExpired:
        logger.warning("ClawDefender timeout, falling back to builtin")
        return None
    except Exception as e:
        logger.warning(f"ClawDefender error: {e}, falling back to builtin")
        return None


def guard_input(text: str) -> InputGuardResult:
    """
    输入安全检查 — 双层防护链:
      1. ClawDefender (90+ 模式, 覆盖 SSRF/凭证泄露/路径遍历)
      2. 内置正则兜底 (24 模式)
    """
    if not INPUT_GUARD_CONFIG.get("enabled"):
        return InputGuardResult(safe=True, sanitized=text)

    max_len = INPUT_GUARD_CONFIG.get("max_input_length", 32000)
    truncated = False
    if len(text) > max_len:
        logger.warning(f"Input truncated: {len(text)} -> {max_len} chars")
        text = text[:max_len]
        truncated = True

    # ── Layer 1: ClawDefender (社区 skill, 90+ patterns) ──
    cd_result = _clawdefender_check(text)
    if cd_result is not None:
        if not cd_result.safe:
            return cd_result
        # ClawDefender 通过，使用其 sanitized 输出
        sanitized = cd_result.sanitized
        if truncated:
            sanitized = sanitized + f"\n\n[系统提示: 输入已截断至 {max_len} 字符]"
        cd_result.sanitized = sanitized
        return cd_result

    # ── Layer 2: 内置正则兜底 ──
    for pattern in _get_patterns():
        match = pattern.search(text)
        if match:
            logger.warning(f"Builtin pattern matched: '{match.group()}'")
            return InputGuardResult(
                safe=False,
                reason=f"检测到可疑指令注入: '{match.group()}'",
                source="builtin",
            )

    sanitized = _strip_control_chars(text)

    if truncated:
        sanitized = sanitized + f"\n\n[系统提示: 输入已截断至 {max_len} 字符]"

    return InputGuardResult(safe=True, sanitized=sanitized, source="builtin")


def audit_skills() -> Optional[str]:
    """运行 ClawDefender 全面安全审计 (扫描所有已安装 skills)"""
    _ensure_scripts()
    if not _clawdefender_audit:
        return None
    try:
        proc = subprocess.run(
            [_clawdefender_audit, "--audit"],
            capture_output=True, text=True, timeout=30,
        )
        return proc.stdout or proc.stderr or "审计完成(无输出)"
    except Exception as e:
        logger.error(f"ClawDefender audit failed: {e}")
        return f"审计失败: {e}"


def check_url(url: str) -> Optional[InputGuardResult]:
    """通过 ClawDefender 检查 URL (SSRF 防护)"""
    _ensure_scripts()
    if not _clawdefender_audit:
        return None
    try:
        proc = subprocess.run(
            [_clawdefender_audit, "--check-url", url],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode != 0:
            return InputGuardResult(safe=False, reason=f"URL 安全检查未通过: {proc.stdout.strip()}", source="clawdefender")
        return InputGuardResult(safe=True, sanitized=url, source="clawdefender")
    except Exception as e:
        logger.warning(f"URL check error: {e}")
        return None


def _strip_control_chars(text: str) -> str:
    """移除不可见控制字符（保留换行和制表符）"""
    return "".join(
        c for c in text
        if c in ("\n", "\t", "\r") or (ord(c) >= 32)
    )

"""
Soul Guardian — SOUL.md 身份漂移检测 + 基线完整性守护
灵感来源: awesome-openclaw-skills 中的 soul-guardian / agent-identity-kit

功能:
1. 启动时计算所有 SOUL.md 和 skills YAML 的哈希基线
2. 定期检查文件是否被篡改或意外修改
3. 分析 Agent 输出是否偏离 SOUL 身份定义（语义漂移检测）
4. 异常时通过 Redis 发送告警
"""

import hashlib
import json
import logging
import os
import time
from typing import Optional

logger = logging.getLogger("memory.soul_guardian")

SOULS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "souls")
SKILLS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills")
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
BASELINE_KEY = "soul:baseline"
ALERT_KEY = "soul:alerts"


def _file_hash(path: str) -> str:
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:16]
    except Exception:
        return ""


def _scan_directory(directory: str, ext: str) -> dict[str, str]:
    results = {}
    if not os.path.isdir(directory):
        return results
    for name in os.listdir(directory):
        if name.endswith(ext):
            path = os.path.join(directory, name)
            results[name] = _file_hash(path)
    return results


class SoulGuardian:
    """SOUL 身份完整性守护器"""

    def __init__(self):
        self._baseline: dict[str, dict[str, str]] = {}
        self._redis = None

    async def _get_redis(self):
        if self._redis is None:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(REDIS_URL, decode_responses=True)
        return self._redis

    def compute_baseline(self) -> dict:
        """计算当前所有 SOUL.md 和 skills YAML 的哈希基线"""
        self._baseline = {
            "souls": _scan_directory(SOULS_DIR, ".md"),
            "skills": _scan_directory(SKILLS_DIR, ".yaml"),
            "timestamp": time.time(),
        }
        return self._baseline

    async def save_baseline(self):
        """将基线保存到 Redis"""
        if not self._baseline:
            self.compute_baseline()
        try:
            r = await self._get_redis()
            await r.set(BASELINE_KEY, json.dumps(self._baseline))
            logger.info(f"Soul baseline saved: {len(self._baseline.get('souls', {}))} souls, "
                        f"{len(self._baseline.get('skills', {}))} skills")
        except Exception as e:
            logger.warning(f"Save baseline failed: {e}")

    async def load_baseline(self) -> Optional[dict]:
        """从 Redis 加载基线"""
        try:
            r = await self._get_redis()
            raw = await r.get(BASELINE_KEY)
            if raw:
                self._baseline = json.loads(raw)
                return self._baseline
        except Exception as e:
            logger.warning(f"Load baseline failed: {e}")
        return None

    async def check_integrity(self) -> dict:
        """
        检查 SOUL 和 Skills 文件完整性
        返回: {"status": "ok"|"tampered"|"missing", "changes": [...]}
        """
        stored = await self.load_baseline()
        if not stored:
            self.compute_baseline()
            await self.save_baseline()
            return {"status": "ok", "changes": [], "note": "baseline initialized"}

        current_souls = _scan_directory(SOULS_DIR, ".md")
        current_skills = _scan_directory(SKILLS_DIR, ".yaml")

        changes = []

        for name, old_hash in stored.get("souls", {}).items():
            new_hash = current_souls.get(name, "")
            if not new_hash:
                changes.append({"type": "deleted", "category": "soul", "file": name})
            elif new_hash != old_hash:
                changes.append({"type": "modified", "category": "soul", "file": name,
                                "old": old_hash, "new": new_hash})

        for name in current_souls:
            if name not in stored.get("souls", {}):
                changes.append({"type": "added", "category": "soul", "file": name})

        for name, old_hash in stored.get("skills", {}).items():
            new_hash = current_skills.get(name, "")
            if not new_hash:
                changes.append({"type": "deleted", "category": "skill", "file": name})
            elif new_hash != old_hash:
                changes.append({"type": "modified", "category": "skill", "file": name,
                                "old": old_hash, "new": new_hash})

        for name in current_skills:
            if name not in stored.get("skills", {}):
                changes.append({"type": "added", "category": "skill", "file": name})

        status = "ok" if not changes else "tampered"

        if changes:
            await self._alert(changes)

        return {"status": status, "changes": changes}

    async def _alert(self, changes: list[dict]):
        """通过 Redis 推送身份篡改告警"""
        try:
            r = await self._get_redis()
            alert = {
                "ts": time.time(),
                "changes": changes,
                "severity": "critical" if any(c["type"] == "deleted" for c in changes) else "warning",
            }
            await r.lpush(ALERT_KEY, json.dumps(alert, ensure_ascii=False))
            await r.ltrim(ALERT_KEY, 0, 49)

            tampered_files = ", ".join(f"{c['file']}({c['type']})" for c in changes[:5])
            await r.publish("rragent:notify:telegram", json.dumps({
                "text": f"🛡️ SOUL Guardian 告警: 检测到身份文件变更\n{tampered_files}",
                "from": "soul_guardian",
                "timestamp": time.time(),
            }))

            logger.warning(f"Soul integrity alert: {len(changes)} changes detected")
        except Exception as e:
            logger.warning(f"Alert push failed: {e}")

    async def accept_changes(self):
        """接受当前文件状态为新基线（人工确认后调用）"""
        self.compute_baseline()
        await self.save_baseline()
        logger.info("Soul baseline updated (changes accepted)")

    async def get_alerts(self, count: int = 10) -> list[dict]:
        """获取最近的告警记录"""
        try:
            r = await self._get_redis()
            raw = await r.lrange(ALERT_KEY, 0, count - 1)
            return [json.loads(x) for x in raw]
        except Exception:
            return []

    def get_soul_summary(self) -> dict:
        """获取当前 SOUL 和 Skills 文件概要"""
        souls = _scan_directory(SOULS_DIR, ".md")
        skills = _scan_directory(SKILLS_DIR, ".yaml")
        return {
            "souls_count": len(souls),
            "skills_count": len(skills),
            "souls": list(souls.keys()),
            "skills": list(skills.keys()),
        }

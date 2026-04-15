"""
反思引擎 (ReflectionEngine) — 持久化学习与路由优化

功能:
1. 持久化 plan 结果到 JSON（不受 Redis 24h TTL 限制）
2. 跟踪每个 agent.action 的成功率和延迟
3. 检测重复查询（用户未获满足的信号）
4. 生成路由提示，注入 L1 分诊 prompt
5. 生成周期性自我改进报告
6. 结构化教训系统 (集成 actual-self-improvement skill)
   - 自动从失败/纠正中提取可复用教训
   - .learnings/ 持久化: LEARNINGS.md / ERRORS.md / FEATURE_REQUESTS.md
   - 教训去重 + 晋升为永久记忆

存储布局:
  memory/reflection/
    route_stats.json      — 路由成功率统计
    daily_summary.json    — 每日查询摘要
    conversation_log.json — 近期对话质量记录（最近 500 条）
  .learnings/
    LEARNINGS.md          — 结构化教训库
    ERRORS.md             — 非显而易见的错误记录
    FEATURE_REQUESTS.md   — 功能需求追踪
"""

import json
import logging
import os
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger("memory.reflection_engine")

_BASE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "memory", "reflection",
)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_LEARNINGS_DIR = os.path.join(_PROJECT_ROOT, ".learnings")

ROUTE_STATS_FILE = os.path.join(_BASE, "route_stats.json")
DAILY_SUMMARY_FILE = os.path.join(_BASE, "daily_summary.json")
CONV_LOG_FILE = os.path.join(_BASE, "conversation_log.json")
CONV_LOG_MAX = 500


def _load_json(path: str, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.debug("load %s failed: %s", path, e)
    return default


def _save_json(path: str, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        logger.debug("save %s failed: %s", path, e)


class ReflectionEngine:
    """持久化反思引擎 — 从历史交互中学习，优化未来路由决策，结构化教训管理"""

    def __init__(self):
        Path(_BASE).mkdir(parents=True, exist_ok=True)
        Path(_LEARNINGS_DIR).mkdir(parents=True, exist_ok=True)
        self._route_stats: dict = _load_json(ROUTE_STATS_FILE, {})
        self._daily_summary: dict = _load_json(DAILY_SUMMARY_FILE, {})
        self._conv_log: list = _load_json(CONV_LOG_FILE, [])
        self._pending = 0
        self._last_flush = time.time()
        self._init_learnings()

    # ── 核心记录接口 ─────────────────────────────────

    def record_plan_outcome(self, plan_record: dict):
        """从 orchestrator 的 plan_record 提取结果并记录"""
        today = date.today().isoformat()
        steps = plan_record.get("steps", [])
        route_level = plan_record.get("route_level", "L1")
        user_input = plan_record.get("input", "")[:120]
        uid = plan_record.get("uid", "")
        total_ok = all(sr.get("ok", False) for sr in steps) if steps else False
        total_latency = plan_record.get("latency_ms", {}).get("total", 0)

        # 1. 路由统计
        for sr in steps:
            step = sr.get("step", {})
            agent = step.get("agent", "unknown")
            action = step.get("action", "unknown")
            ok = sr.get("ok", False)
            lat = sr.get("latency_ms", 0)
            key = f"{agent}.{action}"
            if key not in self._route_stats:
                self._route_stats[key] = {
                    "success": 0, "failure": 0,
                    "total_latency_ms": 0, "count": 0,
                    "last_seen": today,
                }
            s = self._route_stats[key]
            s["success" if ok else "failure"] += 1
            s["count"] += 1
            s["total_latency_ms"] += lat
            s["last_seen"] = today

        # 2. 每日摘要
        if today not in self._daily_summary:
            self._daily_summary[today] = {
                "total": 0, "l0": 0, "l1": 0, "l2": 0,
                "failures": 0, "agents": {},
            }
        ds = self._daily_summary[today]
        ds["total"] += 1
        level_key = route_level.lower()
        ds[level_key] = ds.get(level_key, 0) + 1
        if not total_ok:
            ds["failures"] += 1
        for sr in steps:
            ag = sr.get("step", {}).get("agent", "?")
            ds["agents"][ag] = ds["agents"].get(ag, 0) + 1

        # 3. 对话日志（滚动 CONV_LOG_MAX 条）
        self._conv_log.append({
            "ts": time.time(),
            "date": today,
            "uid": uid,
            "input_head": user_input,
            "route": route_level,
            "ok": total_ok,
            "latency_ms": total_latency,
            "agents": [sr.get("step", {}).get("agent", "?") for sr in steps],
        })
        if len(self._conv_log) > CONV_LOG_MAX:
            self._conv_log = self._conv_log[-CONV_LOG_MAX:]

        # 自动从失败中提取教训 (actual-self-improvement 集成)
        if not total_ok:
            try:
                self.auto_capture_from_failure(plan_record)
            except Exception as e:
                logger.debug("auto_capture_from_failure failed: %s", e)

        self._maybe_flush()

    def detect_repeated_query(self, user_input: str, recent_inputs: list[str]) -> bool:
        """
        检测用户是否在短时间内重复提交高度相似的查询。
        使用轻量 Jaccard 相似度（中英文 token 级别）。
        """
        if not recent_inputs or not user_input:
            return False
        a_tokens = set(user_input.lower().split())
        for prev in recent_inputs[-5:]:
            if not prev:
                continue
            b_tokens = set(prev.lower().split())
            union = a_tokens | b_tokens
            if not union:
                continue
            jaccard = len(a_tokens & b_tokens) / len(union)
            if jaccard > 0.58:
                return True
        return False

    # ── 路由提示生成 ──────────────────────────────────

    def get_routing_hints(self, top_k: int = 8) -> str:
        """
        生成给 L1 分诊的路由提示字符串。
        基于历史成功率 + 平均延迟，推荐/警告特定 agent.action。
        只返回样本量 ≥ 3 的路由。
        """
        if not self._route_stats:
            return ""
        rows = []
        for key, s in self._route_stats.items():
            n = s.get("count", 0)
            if n < 3:
                continue
            sr = s["success"] / n
            lat = s["total_latency_ms"] / n
            rows.append((key, sr, lat, n))
        if not rows:
            return ""
        rows.sort(key=lambda x: (-x[1], x[2]))
        lines = ["[历史路由参考（基于真实成功率）]"]
        for key, sr, lat, n in rows[:top_k]:
            tag = "✅" if sr >= 0.85 else ("⚠️" if sr < 0.5 else "")
            lines.append(f"  {tag}{key}: 成功率{sr:.0%} 均延迟{lat:.0f}ms (n={n})")
        # 单独列出表现差的（n≥5 且成功率 < 50%）
        poor = [(k, s, n) for k, s, _, n in rows if s < 0.5 and n >= 5]
        if poor:
            lines.append("[近期失败率偏高，路由时应谨慎]")
            for k, s, n in poor[:4]:
                lines.append(f"  ❌{k}: 成功率仅{s:.0%} (n={n})")
        return "\n".join(lines)

    def get_failure_prone_agents(self) -> set[str]:
        """返回成功率 < 50% 且样本量 ≥ 5 的 agent 集合，供路由规避参考"""
        agents: set[str] = set()
        for key, s in self._route_stats.items():
            n = s.get("count", 0)
            if n >= 5 and s.get("success", 0) / n < 0.5:
                agents.add(key.split(".")[0])
        return agents

    # ── 报告生成 ──────────────────────────────────────

    def generate_daily_insight(self) -> str:
        """生成今日系统洞察（适合 Telegram 推送）"""
        today = date.today().isoformat()
        ds = self._daily_summary.get(today, {})
        total = ds.get("total", 0)
        if total == 0:
            return "📊 今日暂无查询数据"
        failures = ds.get("failures", 0)
        fail_rate = failures / total
        top_agents = sorted(ds.get("agents", {}).items(), key=lambda x: -x[1])[:3]
        lines = [
            f"📊 **今日系统洞察** ({today})",
            f"  总查询: {total}  失败率: {fail_rate:.1%}",
            f"  路由分布: L0={ds.get('l0',0)} L1={ds.get('l1',0)} L2={ds.get('l2',0)}",
        ]
        if top_agents:
            lines.append("  最活跃 Agent: " + " | ".join(f"{a}({n})" for a, n in top_agents))
        # 近期对话质量趋势（最近 50 条）
        recent = [c for c in self._conv_log if c.get("date") == today]
        if len(recent) >= 10:
            recent_ok = sum(1 for c in recent if c.get("ok")) / len(recent)
            lines.append(f"  今日回复质量: {recent_ok:.0%} 成功")
        # 最常见失败路由
        failing = [
            (k, s["success"] / s["count"])
            for k, s in self._route_stats.items()
            if s.get("count", 0) >= 3 and s.get("last_seen") == today
            and s["success"] / s["count"] < 0.7
        ]
        if failing:
            lines.append("  ⚠️ 今日不稳定路由: " + ", ".join(f"{k}({r:.0%})" for k, r in failing[:3]))
        return "\n".join(lines)

    def generate_weekly_report(self) -> str:
        """生成近7天系统周报"""
        today = date.today()
        week_ago = (today - timedelta(days=7)).isoformat()
        today_str = today.isoformat()
        total_q = fail_q = 0
        dist = {"l0": 0, "l1": 0, "l2": 0}
        agent_usage: dict[str, int] = {}
        for day_str, ds in self._daily_summary.items():
            if not (week_ago <= day_str <= today_str):
                continue
            total_q += ds.get("total", 0)
            fail_q += ds.get("failures", 0)
            for lv in ("l0", "l1", "l2"):
                dist[lv] += ds.get(lv, 0)
            for ag, cnt in ds.get("agents", {}).items():
                agent_usage[ag] = agent_usage.get(ag, 0) + cnt
        if total_q == 0:
            return "📊 近7天暂无足够数据生成周报"
        fail_rate = fail_q / total_q
        lines = [
            f"📊 **系统周报** ({week_ago} → {today_str})",
            f"  总查询: {total_q}  整体失败率: {fail_rate:.1%}",
            f"  路由分布: L0={dist['l0']} L1={dist['l1']} L2={dist['l2']}",
        ]
        if agent_usage:
            top = sorted(agent_usage.items(), key=lambda x: -x[1])[:4]
            lines.append("  最活跃 Agent: " + " | ".join(f"{a}({n})" for a, n in top))
        # 表现最好路由
        good = sorted(
            [(k, s["success"] / s["count"], s["count"])
             for k, s in self._route_stats.items() if s.get("count", 0) >= 5],
            key=lambda x: -x[1],
        )
        if good:
            lines.append("  🏆 最稳定路由: " + ", ".join(f"{k}({r:.0%})" for k, r, _ in good[:3]))
        # 表现差的路由
        bad = [(k, s["success"] / s["count"], s["count"])
               for k, s in self._route_stats.items()
               if s.get("count", 0) >= 5 and s["success"] / s["count"] < 0.75]
        bad.sort(key=lambda x: x[1])
        if bad:
            lines.append("  ⚠️ 需优化: " + ", ".join(f"{k}({r:.0%})" for k, r, _ in bad[:3]))
        # 改进建议
        suggestions = self._generate_suggestions(dist, total_q, fail_rate, bad)
        if suggestions:
            lines.append("\n💡 **改进建议**")
            lines.extend(f"  {i+1}. {s}" for i, s in enumerate(suggestions))
        return "\n".join(lines)

    def _generate_suggestions(
        self,
        dist: dict,
        total_q: int,
        fail_rate: float,
        bad_routes: list,
    ) -> list[str]:
        """基于统计数据生成具体改进建议"""
        suggestions = []
        l2_rate = dist["l2"] / total_q if total_q > 0 else 0
        if l2_rate > 0.3:
            suggestions.append(
                f"L2深度规划占比 {l2_rate:.0%} 偏高，建议在 L0 关键词路由中增补常见模式，减少 LLM 分诊开销"
            )
        if fail_rate > 0.15:
            suggestions.append(
                f"整体失败率 {fail_rate:.1%} 偏高，建议检查 Agent 可用性和 LLM 路由配置"
            )
        for k, r, n in bad_routes[:2]:
            agent = k.split(".")[0]
            suggestions.append(
                f"{k} 成功率仅 {r:.0%}（n={n}），建议检查该 Agent 的超时配置或数据源稳定性"
            )
        return suggestions[:4]

    def get_recent_query_inputs(self, uid: str, limit: int = 10) -> list[str]:
        """获取指定用户最近的查询输入列表（用于重复查询检测）"""
        result = []
        for entry in reversed(self._conv_log):
            if entry.get("uid") == uid:
                inp = entry.get("input_head", "")
                if inp:
                    result.append(inp)
            if len(result) >= limit:
                break
        return result

    # ── 结构化教训系统 (actual-self-improvement 集成) ──

    def _init_learnings(self):
        """初始化 .learnings/ 目录和文件"""
        for fname in ("LEARNINGS.md", "ERRORS.md", "FEATURE_REQUESTS.md"):
            fpath = os.path.join(_LEARNINGS_DIR, fname)
            if not os.path.exists(fpath):
                header = {
                    "LEARNINGS.md": "# Learnings\n\n持久化教训库 — 从错误、纠正、惯例中提取的可复用知识\n\n",
                    "ERRORS.md": "# Errors\n\n非显而易见的错误记录 — 值得记住的故障模式\n\n",
                    "FEATURE_REQUESTS.md": "# Feature Requests\n\n功能需求追踪 — 用户请求的缺失能力\n\n",
                }
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(header[fname])

    def _next_learning_id(self, prefix: str = "LRN") -> str:
        """生成确定性的教训 ID"""
        today = date.today().strftime("%Y%m%d")
        fpath = os.path.join(_LEARNINGS_DIR, "LEARNINGS.md")
        content = ""
        if os.path.exists(fpath):
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read()
        existing = re.findall(rf"{prefix}-{today}-(\d+)", content)
        seq = max((int(x) for x in existing), default=0) + 1
        return f"{prefix}-{today}-{seq:03d}"

    def log_learning(
        self,
        summary: str,
        details: str = "",
        category: str = "correction",
        area: str = "system",
        priority: str = "medium",
        suggested_action: str = "",
        source: str = "auto",
        related_files: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> str:
        """
        记录一条结构化教训到 .learnings/LEARNINGS.md
        返回教训 ID。

        category: correction | convention | workaround | performance | security
        priority: low | medium | high | critical
        source: auto | user_correction | error | reflection
        """
        lid = self._next_learning_id("LRN")
        fpath = os.path.join(_LEARNINGS_DIR, "LEARNINGS.md")
        entry = (
            f"\n## {lid}\n"
            f"- **Summary**: {summary}\n"
            f"- **Category**: {category}\n"
            f"- **Area**: {area}\n"
            f"- **Priority**: {priority}\n"
            f"- **Source**: {source}\n"
            f"- **Date**: {date.today().isoformat()}\n"
            f"- **Status**: active\n"
        )
        if details:
            entry += f"- **Details**: {details}\n"
        if suggested_action:
            entry += f"- **Action**: {suggested_action}\n"
        if related_files:
            entry += f"- **Files**: {', '.join(related_files)}\n"
        if tags:
            entry += f"- **Tags**: {', '.join(tags)}\n"
        entry += "\n"

        try:
            with open(fpath, "a", encoding="utf-8") as f:
                f.write(entry)
            logger.info("Logged learning %s: %s", lid, summary[:80])
        except Exception as e:
            logger.warning("Failed to log learning: %s", e)
        return lid

    def log_error(
        self,
        name: str,
        summary: str,
        error_text: str = "",
        context: str = "",
        suggested_fix: str = "",
        area: str = "system",
        priority: str = "medium",
        reproducible: str = "unknown",
    ) -> str:
        """记录非显而易见的错误到 .learnings/ERRORS.md"""
        eid = self._next_learning_id("ERR")
        fpath = os.path.join(_LEARNINGS_DIR, "ERRORS.md")
        entry = (
            f"\n## {eid}: {name}\n"
            f"- **Summary**: {summary}\n"
            f"- **Area**: {area}\n"
            f"- **Priority**: {priority}\n"
            f"- **Reproducible**: {reproducible}\n"
            f"- **Date**: {date.today().isoformat()}\n"
            f"- **Status**: active\n"
        )
        if error_text:
            entry += f"- **Error**: `{error_text[:500]}`\n"
        if context:
            entry += f"- **Context**: {context}\n"
        if suggested_fix:
            entry += f"- **Fix**: {suggested_fix}\n"
        entry += "\n"

        try:
            with open(fpath, "a", encoding="utf-8") as f:
                f.write(entry)
            logger.info("Logged error %s: %s", eid, summary[:80])
        except Exception as e:
            logger.warning("Failed to log error: %s", e)
        return eid

    def log_feature_request(
        self,
        capability: str,
        summary: str,
        user_context: str = "",
        priority: str = "medium",
        area: str = "system",
    ) -> str:
        """记录功能需求到 .learnings/FEATURE_REQUESTS.md"""
        fid = self._next_learning_id("FR")
        fpath = os.path.join(_LEARNINGS_DIR, "FEATURE_REQUESTS.md")
        entry = (
            f"\n## {fid}: {capability}\n"
            f"- **Summary**: {summary}\n"
            f"- **Area**: {area}\n"
            f"- **Priority**: {priority}\n"
            f"- **Date**: {date.today().isoformat()}\n"
            f"- **Status**: open\n"
        )
        if user_context:
            entry += f"- **Context**: {user_context}\n"
        entry += "\n"

        try:
            with open(fpath, "a", encoding="utf-8") as f:
                f.write(entry)
        except Exception as e:
            logger.warning("Failed to log feature request: %s", e)
        return fid

    def search_learnings(self, query: str, limit: int = 10) -> list[dict]:
        """搜索 .learnings/ 中的教训（简单关键词匹配）"""
        results = []
        query_lower = query.lower()
        for fname in ("LEARNINGS.md", "ERRORS.md", "FEATURE_REQUESTS.md"):
            fpath = os.path.join(_LEARNINGS_DIR, fname)
            if not os.path.exists(fpath):
                continue
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read()
            # 按 ## 分割条目
            entries = re.split(r'\n(?=## )', content)
            for entry in entries:
                if not entry.strip() or entry.startswith("# "):
                    continue
                if query_lower in entry.lower():
                    # 提取 ID 和 summary
                    id_match = re.search(r'## ((?:LRN|ERR|FR)-\S+)', entry)
                    sum_match = re.search(r'\*\*Summary\*\*:\s*(.+)', entry)
                    results.append({
                        "id": id_match.group(1) if id_match else "?",
                        "summary": sum_match.group(1).strip() if sum_match else entry[:100],
                        "file": fname,
                        "full": entry.strip(),
                    })
                    if len(results) >= limit:
                        return results
        return results

    def get_learnings_stats(self) -> dict:
        """返回 .learnings/ 统计"""
        stats = {}
        for fname in ("LEARNINGS.md", "ERRORS.md", "FEATURE_REQUESTS.md"):
            fpath = os.path.join(_LEARNINGS_DIR, fname)
            if not os.path.exists(fpath):
                stats[fname] = 0
                continue
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read()
            stats[fname] = len(re.findall(r'^## (?:LRN|ERR|FR)-', content, re.MULTILINE))
        return stats

    def auto_capture_from_failure(self, plan_record: dict):
        """
        从失败的 plan 执行中自动提取教训。
        仅记录非显而易见的、可复用的失败模式。
        """
        steps = plan_record.get("steps", [])
        user_input = plan_record.get("input", "")[:200]

        for sr in steps:
            if sr.get("ok"):
                continue
            step = sr.get("step", {})
            agent = step.get("agent", "unknown")
            action = step.get("action", "unknown")
            result_text = str(sr.get("result", ""))[:500]
            key = f"{agent}.{action}"

            # 只有重复失败 (≥3次且成功率<50%) 的路由才记录
            stat = self._route_stats.get(key, {})
            count = stat.get("count", 0)
            if count < 3:
                continue
            success_rate = stat.get("success", 0) / count
            if success_rate >= 0.5:
                continue

            # 去重检查 — 避免重复记录同一路由的错误
            existing = self.search_learnings(key, limit=3)
            if any(key in e.get("summary", "") for e in existing):
                continue

            self.log_error(
                name=f"{key}-recurring-failure",
                summary=f"路由 {key} 持续失败 (成功率 {success_rate:.0%}, n={count})",
                error_text=result_text[:300],
                context=f"用户输入: {user_input}",
                suggested_fix=f"检查 {agent} 的 {action} 处理逻辑、超时配置或数据源稳定性",
                area=agent,
                priority="high" if success_rate < 0.3 else "medium",
                reproducible="yes",
            )

    # ── 持久化管理 ────────────────────────────────────

    def _maybe_flush(self, force: bool = False):
        self._pending += 1
        elapsed = time.time() - self._last_flush
        if force or self._pending >= 5 or elapsed > 180:
            self.flush()

    def flush(self):
        """强制写盘"""
        _save_json(ROUTE_STATS_FILE, self._route_stats)
        _save_json(DAILY_SUMMARY_FILE, self._daily_summary)
        _save_json(CONV_LOG_FILE, self._conv_log)
        self._pending = 0
        self._last_flush = time.time()

    def get_stats_summary(self) -> dict:
        """返回概览字典，供 /reflect 命令使用"""
        today = date.today().isoformat()
        ds = self._daily_summary.get(today, {})
        tracked_routes = len(self._route_stats)
        days_tracked = len(self._daily_summary)
        reliable_routes = [
            (k, s["success"] / s["count"])
            for k, s in self._route_stats.items()
            if s.get("count", 0) >= 5
        ]
        avg_success = (
            sum(r for _, r in reliable_routes) / len(reliable_routes)
            if reliable_routes else 0.0
        )
        return {
            "today_queries": ds.get("total", 0),
            "today_failures": ds.get("failures", 0),
            "tracked_routes": tracked_routes,
            "days_tracked": days_tracked,
            "avg_success_rate": round(avg_success, 3),
            "failure_prone_agents": list(self.get_failure_prone_agents()),
            "conv_log_size": len(self._conv_log),
            "learnings": self.get_learnings_stats(),
        }


# ── 全局单例 ─────────────────────────────────────────
_engine: Optional[ReflectionEngine] = None


def get_reflection_engine() -> ReflectionEngine:
    global _engine
    if _engine is None:
        _engine = ReflectionEngine()
    return _engine

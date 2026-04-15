"""
Orchestrator — 统筹 Agent
职责: 命令路由（精确命令 + LLM 意图识别柔性路由）、规则引擎、跨Agent编排、结果聚合
      Redis 行为画像、全局图谱拓扑健康监控、记忆降级告警
      跨Agent记忆提醒引擎、数据源健康监控
      SOUL 身份守护、LLM 智能路由、主动综合简报
"""

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path

import yaml

from agents.base import BaseAgent, AgentMessage, run_agent

logger = logging.getLogger("agent.orchestrator")

RULES_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rules.yaml")

ACTION_TIMEOUTS: dict[str, float] = {
    "claude_code": 360,
    "code_review": 300,
    "refactor": 300,
    "fix_bug": 300,
    "gen_test": 300,
    "explain": 180,
    "deploy_frontend": 300,
    "smart_task": 180,
    "smart_exec": 120,
    "task": 180,
    "ai_dev": 180,
    "ask": 180,
    "ask_strategy": 180,
    "run_backtest": 300,
    "get_news": 120,
    "get_summary": 120,
    "search": 120,
    "summarize": 180,
    "write": 180,
    "explain_code": 180,
    "ssh_exec": 120,
    "local_exec": 120,
    "git_sync": 120,
    "host_test": 60,
    "status": 120,
    "summary": 120,
    "check_alerts": 120,
    "check_targets": 120,
    "host_health": 120,
}

COMMAND_ROUTES = {
    "zt": ("market", "get_limitup"),
    "lb": ("market", "get_limitstep"),
    "bk": ("market", "get_concepts"),
    "hot": ("market", "get_hot"),
    "summary": ("market", "get_summary"),
    "ask": ("analysis", "ask"),
    "dev": ("dev", "ai_dev"),
    "claude": ("dev", "claude_code"),
    "cc": ("dev", "claude_code"),
    "claude_continue": ("dev", "claude_continue"),
    "ccr": ("dev", "claude_continue"),
    "deploy": ("dev", "deploy_frontend"),
    "ssh": ("dev", "ssh_exec"),
    "local": ("dev", "local_exec"),
    "code_review": ("dev", "code_review"),
    "cr": ("dev", "code_review"),
    "review": ("dev", "code_review"),
    "refactor": ("dev", "refactor"),
    "fix": ("dev", "fix_bug"),
    "test": ("dev", "gen_test"),
    "explain": ("dev", "explain"),
    "dev_status": ("dev", "status"),
    "git_status": ("dev", "git_status"),
    "git_pull": ("dev", "git_pull"),
    "git_log": ("dev", "git_log"),
    "git_diff": ("dev", "git_diff"),
    "git_sync": ("dev", "git_sync"),
    "host_list": ("dev", "host_list"),
    "host_test": ("dev", "host_test"),
    "browse": ("browser", "smart_task"),
    "task": ("browser", "smart_task"),
    "url": ("browser", "open_url"),
    "screen": ("desktop", "screenshot"),
    "do": ("desktop", "smart_exec"),
    "shell": ("desktop", "shell"),
    "app": ("desktop", "open_app"),
    "type": ("desktop", "type_text"),
    "key": ("desktop", "key_press"),
    "click": ("desktop", "click"),
    "windows": ("desktop", "list_windows"),
    "news": ("news", "get_news"),
    "strategy": ("strategist", "ask_strategy"),
    "backtest": ("backtest", "run_backtest"),
    "bt_cache": ("backtest", "list_cache"),
    "ledger": ("backtest", "list_ledger"),
    "strategy_list": ("backtest", "list_strategies"),
    "strategy_detail": ("backtest", "get_strategy"),
    # GeneralAgent — 通用助手
    "q": ("general", "ask"),
    "translate": ("general", "translate"),
    "summarize": ("general", "summarize"),
    "write": ("general", "write"),
    "code": ("general", "explain_code"),
    "calc": ("general", "calculate"),
    "websearch": ("general", "search"),
    # AppleAgent — Apple 生态
    "calendar": ("apple", "calendar_today"),
    "cal_add": ("apple", "calendar_create"),
    "cal_del": ("apple", "calendar_delete"),
    "remind": ("apple", "remind_create"),
    "remind_list": ("apple", "remind_list"),
    "remind_done": ("apple", "remind_complete"),
    "remind_edit": ("apple", "remind_edit"),
    "remind_del": ("apple", "remind_delete"),
    "remind_lists": ("apple", "remind_lists"),
    "note": ("apple", "note_create"),
    "note_search": ("apple", "note_search"),
    "contact": ("apple", "contact_search"),
    "mail": ("apple", "mail_send"),
    "notify": ("apple", "notify"),
    "search": ("apple", "spotlight"),
    "music": ("apple", "music_control"),
    "shortcut": ("apple", "shortcut_run"),
    "shortcut_list": ("apple", "shortcut_list"),
    "sysinfo": ("apple", "system_info"),
    "clip": ("apple", "clipboard_read"),
    "clip_set": ("apple", "clipboard_write"),
    "finder": ("apple", "finder_open"),
    "volume": ("apple", "volume_control"),
    "app_ctrl": ("apple", "app_control"),
    "brightness": ("apple", "screen_brightness"),
    "dnd": ("apple", "do_not_disturb"),
    "alarm": ("apple", "alarm_set"),
    "alarm_list": ("apple", "alarm_list"),
    "alarm_cancel": ("apple", "alarm_cancel"),
    "timer": ("apple", "timer_set"),
    # MonitorAgent — 基础设施监控
    "alerts": ("monitor", "check_alerts"),
    "targets": ("monitor", "check_targets"),
    "alert_history": ("monitor", "alert_history"),
    "grafana_alerts": ("monitor", "grafana_alerts"),
    "patrol": ("monitor", "summary"),
    "silence": ("monitor", "silence"),
    "cert": ("monitor", "check_cert"),
    "ssl": ("monitor", "check_cert"),
    "query": ("monitor", "query"),
    "promql": ("monitor", "query"),
    "metrics": ("monitor", "metrics"),
    "host": ("monitor", "host_health"),
    "host_health": ("monitor", "host_health"),
    "grafana_dash": ("monitor", "grafana_dash"),
    # NewsAgent — 搜索与研究
    "web_search": ("news", "web_search"),
    "ws": ("news", "web_search"),
    "research": ("news", "deep_research"),
    "deep": ("news", "deep_research"),
    # BrowserAgent — 新能力
    "snapshot": ("browser", "snapshot"),
    # 反思引擎
    "reflect": ("orchestrator", "reflect_insight"),
    "reflect_weekly": ("orchestrator", "reflect_weekly"),
    "reflect_stats": ("orchestrator", "reflect_stats"),
    # 安全审计
    "audit": ("orchestrator", "security_audit"),
    "security": ("orchestrator", "security_audit"),
    # 多 Agent 协作研究
    "multi_research": ("orchestrator", "multi_research"),
    "mr": ("orchestrator", "multi_research"),
    # 系统自省
    "skills": ("orchestrator", "list_skills"),
    "agents": ("orchestrator", "list_agents"),
    "factor_list": ("orchestrator", "factor_list"),
    "factor_detail": ("orchestrator", "factor_detail"),
    # 记忆分层 (memory-tiering)
    "tier": ("orchestrator", "memory_tier"),
    "memory_tier": ("orchestrator", "memory_tier"),
    "tier_status": ("orchestrator", "memory_tier_status"),
    # 结构化教训 (actual-self-improvement)
    "learnings": ("orchestrator", "learnings"),
    "learn": ("orchestrator", "log_learning"),
    # 量化验证 (quantitative-research)
    "qv": ("backtest", "quant_validate"),
    "quant_validate": ("backtest", "quant_validate"),
}

PARAM_MAP = {
    "zt": lambda args: {"page_size": int(args) if args else 20},
    "lb": lambda args: {"page_size": int(args) if args else 15},
    "bk": lambda args: {"page_size": int(args) if args else 15},
    "hot": lambda args: {"page_size": int(args) if args else 15},
    "ask": lambda args: {"question": args},
    "dev": lambda args: {"instruction": args},
    "claude": lambda args: _parse_claude_args(args),
    "cc": lambda args: _parse_claude_args(args),
    "claude_continue": lambda args: {"prompt": args or "继续"},
    "ccr": lambda args: {"prompt": args or "继续"},
    "deploy": lambda args: {"skip_build": args.strip().lower() == "skip_build"},
    "ssh": lambda args: _parse_json_or_simple(args, "command"),
    "git_status": lambda args: _parse_json_or_simple(args, "repo"),
    "git_pull": lambda args: _parse_json_or_simple(args, "repo"),
    "git_log": lambda args: _parse_json_or_simple(args, "repo"),
    "git_diff": lambda args: _parse_json_or_simple(args, "repo"),
    "git_sync": lambda args: _parse_json_or_simple(args, "repo"),
    "host_list": lambda args: {},
    "host_test": lambda args: _parse_json_or_simple(args, "host"),
    "local": lambda args: {"command": args},
    "code_review": lambda args: _parse_dev_path_args(args, "path", "focus"),
    "cr": lambda args: _parse_dev_path_args(args, "path", "focus"),
    "review": lambda args: _parse_dev_path_args(args, "path", "focus"),
    "refactor": lambda args: _parse_dev_path_args(args, "path", "description"),
    "fix": lambda args: {"description": args},
    "test": lambda args: _parse_dev_path_args(args, "path", "framework"),
    "explain": lambda args: {"path": args.strip()},
    "dev_status": lambda args: {},
    "browse": lambda args: {"task": args},
    "task": lambda args: {"task": args},
    "url": lambda args: {"url": args},
    "do": lambda args: {"instruction": args},
    "shell": lambda args: {"command": args},
    "app": lambda args: {"app": args},
    "type": lambda args: {"text": args},
    "news": lambda args: {"keyword": args},
    "strategy": lambda args: {"question": args},
    "strategy_detail": lambda args: {"id": args},
    # GeneralAgent
    "q": lambda args: {"question": args},
    "translate": lambda args: {"text": args},
    "summarize": lambda args: {"text": args},
    "write": lambda args: {"task": args},
    "code": lambda args: {"code": args},
    "calc": lambda args: {"expression": args},
    "websearch": lambda args: {"query": args},
    # AppleAgent
    "calendar": lambda args: {"date": args or "today"},
    "cal_add": lambda args: _parse_cal_add_args(args),
    "cal_del": lambda args: {"title": args},
    "remind": lambda args: _parse_remind_args(args),
    "remind_list": lambda args: _parse_remind_list_args(args),
    "remind_done": lambda args: {"title": args},
    "remind_edit": lambda args: _parse_json_or(args, {"id": args}),
    "remind_del": lambda args: {"id": args.strip()},
    "remind_lists": lambda args: {},
    "note": lambda args: _parse_note_args(args),
    "note_search": lambda args: {"keyword": args},
    "contact": lambda args: {"name": args},
    "mail": lambda args: _parse_mail_args(args),
    "notify": lambda args: {"title": "RRClaw", "message": args},
    "search": lambda args: {"query": args},
    "music": lambda args: _parse_music_args(args),
    "shortcut": lambda args: {"name": args},
    "shortcut_list": lambda args: {},
    "sysinfo": lambda args: {"category": args or "all"},
    "clip": lambda args: {},
    "clip_set": lambda args: {"text": args},
    "finder": lambda args: {"path": args},
    "volume": lambda args: _parse_json_or(args, {"action": "get"}),
    "app_ctrl": lambda args: _parse_json_or(args, {"action": "list"}),
    "brightness": lambda args: _parse_json_or(args, {}),
    "dnd": lambda args: _parse_json_or(args, {"action": args or "status"}),
    "alarm": lambda args: _parse_alarm_args(args),
    "alarm_list": lambda args: {},
    "alarm_cancel": lambda args: {"id": args.strip() if args else ""},
    "timer": lambda args: _parse_timer_args(args),
    # MonitorAgent
    "alert_history": lambda args: {"count": int(args) if args else 20},
    "silence": lambda args: _parse_silence_args(args),
    "cert": lambda args: {"domain": args.strip()},
    "ssl": lambda args: {"domain": args.strip()},
    "query": lambda args: {"promql": args.strip()},
    "promql": lambda args: {"promql": args.strip()},
    "metrics": lambda args: {"keyword": args.strip()},
    "host": lambda args: {"host": args.strip()},
    "host_health": lambda args: {"host": args.strip()},
    "grafana_dash": lambda args: {"uid": args.strip()},
    # 系统自省
    "factor_detail": lambda args: {"factor_id": args.strip()},
    # 记忆分层
    "tier": lambda args: {},
    "memory_tier": lambda args: {},
    "tier_status": lambda args: {},
    # 结构化教训
    "learnings": lambda args: {"query": args} if args else {},
    "learn": lambda args: {"summary": args},
    # 量化验证
    "qv": lambda args: _parse_json_or(args, {"metrics": {}}),
    "quant_validate": lambda args: _parse_json_or(args, {"metrics": {}}),
}


def _parse_json_or(args: str, default: dict) -> dict:
    """尝试 JSON 解析，失败则返回 default"""
    if not args:
        return default
    try:
        parsed = json.loads(args)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass
    return default


def _parse_json_or_simple(args: str, default_key: str = "command") -> dict:
    """尝试 JSON 解析，失败则用 default_key 包装原始字符串"""
    if not args:
        return {}
    try:
        parsed = json.loads(args)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass
    return {default_key: args}


def _parse_dev_path_args(args: str, path_key: str = "path", extra_key: str = "description") -> dict:
    """解析 /code_review /refactor /test 等命令: <path> [extra text]"""
    parts = args.strip().split(maxsplit=1)
    result = {path_key: parts[0] if parts else ""}
    if len(parts) > 1:
        result[extra_key] = parts[1]
    return result


def _parse_claude_args(args: str) -> dict:
    """解析 claude 命令参数。

    格式:
        JSON: {"prompt": "...", "work_dir": "...", "model": "...", "provider": "..."}
        CLI:  /claude --model opus --dir /path <prompt>
    """
    # 优先尝试 JSON（webchat API 传入的格式）
    stripped = args.strip()
    if stripped.startswith("{"):
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, dict) and parsed.get("prompt"):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass

    params: dict = {}
    tokens = args.split()
    prompt_parts = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t == "--model" and i + 1 < len(tokens):
            params["model"] = tokens[i + 1]
            i += 2
        elif t == "--dir" and i + 1 < len(tokens):
            params["work_dir"] = tokens[i + 1]
            i += 2
        elif t == "--budget" and i + 1 < len(tokens):
            params["max_budget"] = tokens[i + 1]
            i += 2
        elif t == "--tools" and i + 1 < len(tokens):
            params["allowed_tools"] = tokens[i + 1]
            i += 2
        elif t == "--timeout" and i + 1 < len(tokens):
            params["timeout"] = int(tokens[i + 1])
            i += 2
        else:
            prompt_parts.append(t)
            i += 1
    params["prompt"] = " ".join(prompt_parts)
    return params


def _parse_silence_args(args: str) -> dict:
    """解析静默参数: 'AlertName [duration_min]'"""
    parts = args.split(maxsplit=1) if args else []
    result = {"alertname": parts[0] if parts else ""}
    if len(parts) >= 2:
        try:
            result["duration"] = int(parts[1])
        except ValueError:
            result["duration"] = 60
    return result


def _parse_cal_add_args(args: str) -> dict:
    """解析日历创建参数: '标题 2026-03-01 14:00 [地点]'"""
    parts = args.split(maxsplit=3) if args else []
    result = {"title": parts[0] if parts else "新事件"}
    if len(parts) >= 3:
        result["start"] = f"{parts[1]} {parts[2]}"
    elif len(parts) >= 2:
        result["start"] = parts[1]
    if len(parts) >= 4:
        result["location"] = parts[3]
    return result


def _parse_alarm_args(args: str) -> dict:
    """解析闹钟参数: '08:30 [标签]' 或 '2026-03-23 08:30 [标签]'"""
    if not args:
        return {"time": ""}
    parts = args.strip().split(maxsplit=2)
    # check if first part is a date
    if len(parts) >= 2 and "-" in parts[0] and ":" in parts[1]:
        result = {"date": parts[0], "time": parts[1]}
        if len(parts) > 2:
            result["label"] = parts[2]
        return result
    result = {"time": parts[0]}
    if len(parts) > 1:
        result["label"] = " ".join(parts[1:])
    return result


def _parse_timer_args(args: str) -> dict:
    """解析定时器参数: '5 [标签]'（分钟数 + 可选标签）"""
    if not args:
        return {"minutes": ""}
    parts = args.strip().split(maxsplit=1)
    result = {"minutes": parts[0]}
    if len(parts) > 1:
        result["label"] = parts[1]
    return result


def _parse_remind_list_args(args: str) -> dict:
    """解析提醒列表查看参数: 'today' / 'overdue' / '工作' (列表名) / 'week 工作'"""
    if not args:
        return {"filter": "upcoming"}
    args = args.strip()
    filters = {"today", "tomorrow", "week", "overdue", "upcoming", "completed", "all"}
    parts = args.split(maxsplit=1)
    if parts[0].lower() in filters:
        result = {"filter": parts[0].lower()}
        if len(parts) > 1:
            result["list_name"] = parts[1]
        return result
    return {"list_name": args}


def _parse_remind_args(args: str) -> dict:
    """智能解析提醒参数 — 支持自然语言时间、优先级、列表、备注、URL

    示例:
      买牛奶 明天下午3点
      打电话给妈妈 high priority 工作列表
      开会 2026-03-25 14:00 notes=带文件 list=工作
      {"title":"...", "due":"...", "notes":"...", "priority":"high", "list_name":"...", "url":"..."}
    """
    if not args:
        return {"title": "新提醒"}

    # 如果是 JSON，直接解析
    args_stripped = args.strip()
    if args_stripped.startswith("{"):
        try:
            data = json.loads(args_stripped)
            return data
        except json.JSONDecodeError:
            pass

    result = {}

    # 提取 key=value 格式参数
    kv_pattern = re.compile(r'\b(notes|url|list|list_name|priority|due|remind_date|flagged)=(\S+(?:\s+\S+)*?)(?=\s+\w+=|\s*$)')
    for m in kv_pattern.finditer(args):
        key = m.group(1)
        val = m.group(2).strip()
        if key == "list":
            key = "list_name"
        result[key] = val
    # 去掉已匹配的 key=value 部分
    remaining = kv_pattern.sub("", args).strip()

    # 提取 URL
    url_match = re.search(r'https?://\S+', remaining)
    if url_match and "url" not in result:
        result["url"] = url_match.group(0)
        remaining = remaining[:url_match.start()] + remaining[url_match.end():]
        remaining = remaining.strip()

    # 提取优先级关键词
    if "priority" not in result:
        pri_patterns = [
            (r'\b(urgent|紧急)\b', "high"),
            (r'\b(high|高优先级|重要)\b', "high"),
            (r'\b(medium|中等优先级|一般)\b', "medium"),
            (r'\b(low|低优先级)\b', "low"),
        ]
        for pat, pri_val in pri_patterns:
            m = re.search(pat, remaining, re.IGNORECASE)
            if m:
                result["priority"] = pri_val
                remaining = remaining[:m.start()] + remaining[m.end():]
                remaining = remaining.strip()
                break

    # 提取列表名 (中文: XX列表 / 英文: list XX)
    if "list_name" not in result:
        list_match = re.search(r'(?:列表|清单|list)\s*[:：]?\s*(\S+)', remaining, re.IGNORECASE)
        if list_match:
            result["list_name"] = list_match.group(1)
            remaining = remaining[:list_match.start()] + remaining[list_match.end():]
            remaining = remaining.strip()

    # 提取时间 — 支持多种自然语言格式
    time_patterns = [
        # ISO 日期时间: 2026-03-25 14:00
        (r'(\d{4}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2})', None),
        # ISO 日期: 2026-03-25
        (r'(\d{4}-\d{1,2}-\d{1,2})', None),
        # 中文: 明天下午3点 / 明天15:00 / 后天上午10点半
        (r'((?:今天|明天|后天|大后天|下周[一二三四五六日天]|周[一二三四五六日天]|下个?月)\s*(?:上午|下午|晚上|早上)?\s*\d{1,2}(?::\d{2}|点半?|时半?)?\s*)', None),
        # 中文无时间: 明天 / 后天 / 下周一
        (r'(今天|明天|后天|大后天|下周[一二三四五六日天]|周[一二三四五六日天])', None),
        # 英文: tomorrow 3pm / next monday / in 2 hours
        (r'((?:today|tomorrow|next\s+\w+|in\s+\d+\s+(?:hours?|minutes?|days?|weeks?))\s*(?:at\s+)?\d{0,2}:?\d{0,2}\s*(?:am|pm)?)', None),
        # 纯时间: 14:00 / 3pm
        (r'(\d{1,2}:\d{2})', None),
        (r'(\d{1,2}\s*(?:am|pm))', None),
    ]

    if "due" not in result:
        for pat, _ in time_patterns:
            m = re.search(pat, remaining, re.IGNORECASE)
            if m:
                result["due"] = m.group(1).strip()
                remaining = remaining[:m.start()] + remaining[m.end():]
                remaining = remaining.strip()
                break

    # 剩余文本作为 title
    remaining = re.sub(r'\s+', ' ', remaining).strip()
    if remaining:
        result["title"] = remaining
    elif "title" not in result:
        result["title"] = args.strip()

    return result


def _parse_note_args(args: str) -> dict:
    """解析备忘录参数: '标题 | 内容'"""
    if not args:
        return {"title": "新备忘录"}
    if "|" in args:
        title, body = args.split("|", 1)
        return {"title": title.strip(), "body": body.strip()}
    return {"title": args}


def _parse_mail_args(args: str) -> dict:
    """解析邮件参数: '收件人 | 主题 | 内容'"""
    if not args:
        return {"to": "", "subject": "", "body": ""}
    parts = [p.strip() for p in args.split("|", 2)]
    return {
        "to": parts[0] if len(parts) > 0 else "",
        "subject": parts[1] if len(parts) > 1 else "",
        "body": parts[2] if len(parts) > 2 else "",
    }


def _parse_music_args(args: str) -> dict:
    """解析音乐参数: 'play/pause/next/prev/status/search 关键词'"""
    if not args:
        return {"action": "status"}
    parts = args.split(maxsplit=1)
    result = {"action": parts[0]}
    if len(parts) > 1:
        result["query"] = parts[1]
    return result

BEHAVIOR_CMD_FREQ_KEY = "user:{uid}:cmd_freq"
BEHAVIOR_TIME_KEY = "user:{uid}:time_pattern"
BEHAVIOR_RECENT_KEY = "user:{uid}:recent_queries"
DEGRADATION_LOG_KEY = "memory:degradation_log"
PLAN_LOG_PREFIX = "rrclaw:plan_log:"
PLAN_HISTORY_KEY = "rrclaw:plan_history"
PLAN_LOG_TTL_SECONDS = 24 * 3600

SESSION_HISTORY_KEY = "rragent:session:{uid}:history"
SESSION_HISTORY_MAX = 40
SESSION_HISTORY_TTL = 86400

UID_ALIAS_KEY = "rragent:uid_aliases"


async def resolve_canonical_uid(r, uid: str) -> str:
    """将任何渠道的 uid 解析为统一的 canonical uid，使跨渠道记忆共享。"""
    if not uid or not r:
        return uid
    try:
        canonical = await r.hget(UID_ALIAS_KEY, uid)
        return canonical.decode() if canonical and isinstance(canonical, bytes) else (canonical or uid)
    except Exception:
        return uid


def load_rules() -> list[dict]:
    try:
        with open(RULES_PATH) as f:
            data = yaml.safe_load(f)
        return [r for r in data.get("rules", []) if r.get("enabled", True)]
    except Exception as e:
        logger.error(f"Failed to load rules: {e}")
        return []


def parse_schedule(schedule_str: str) -> dict:
    parts = schedule_str.split()
    if len(parts) != 5:
        return {}
    return {
        "minute": parts[0],
        "hour": parts[1],
        "dom": parts[2],
        "month": parts[3],
        "dow": parts[4],
    }


def _match_field(spec: str, value: int) -> bool:
    """匹配 cron 字段: *, N, */N, N-M"""
    if spec == "*":
        return True
    if "/" in spec:
        interval = int(spec.split("/")[1])
        return value % interval == 0
    if "-" in spec:
        lo, hi = map(int, spec.split("-"))
        return lo <= value <= hi
    return value == int(spec)


def _match_hour_range(spec: str, now: datetime) -> bool:
    """匹配 hour 字段，支持 H:MM-H:MM 带分钟的范围（如 9:30-15:00）"""
    if ":" in spec:
        parts = spec.replace(":", ".").split("-")
        start_f, end_f = float(parts[0]), float(parts[1])
        current = now.hour + now.minute / 60.0
        return start_f <= current <= end_f
    return _match_field(spec, now.hour)


def match_schedule(schedule: dict, now: datetime) -> bool:
    if not _match_field(schedule.get("minute", "*"), now.minute):
        return False
    if not _match_hour_range(schedule.get("hour", "*"), now):
        return False
    if not _match_field(schedule.get("dow", "*"), now.isoweekday()):
        return False
    return True


class Orchestrator(BaseAgent):
    name = "orchestrator"

    def __init__(self):
        super().__init__()
        self.rules = load_rules()
        self._notify_callbacks: dict[str, list] = {}
        self._last_limitup_count: int | None = None
        self._notify_router = None
        self._task_manager = None
        # 反思引擎（惰性初始化，避免启动延迟）
        self._reflection_engine = None

    def _get_reflection_engine(self):
        if self._reflection_engine is None:
            try:
                from agents.memory.reflection_engine import get_reflection_engine
                self._reflection_engine = get_reflection_engine()
            except Exception as e:
                logger.debug("ReflectionEngine init failed (non-fatal): %s", e)
        return self._reflection_engine

    async def _get_task_manager(self):
        if self._task_manager is None:
            from agents.task_manager import TaskManager
            r = await self.get_redis()
            self._task_manager = TaskManager(r)
        return self._task_manager

    async def _get_notify_router(self):
        if self._notify_router is None:
            from agents.notify_router import get_notify_router
            r = await self.get_redis()
            self._notify_router = await get_notify_router(r)
        return self._notify_router

    async def _notify(self, text: str, topic: str = "market", priority: str = "normal"):
        """统一通知接口 — 通过 NotifyRouter 推送到所有活跃渠道"""
        from agents.notify_router import Priority
        router = await self._get_notify_router()
        prio = Priority(priority)
        await router.broadcast(text, topic=topic, priority=prio, source="orchestrator")

    async def handle(self, msg: AgentMessage):
        action = msg.action

        if action == "route":
            await self._route_command(msg)

        elif action == "notify":
            text = msg.params.get("text", "")
            topic = msg.params.get("topic", "market")
            priority = msg.params.get("priority", "normal")
            await self._notify(text, topic=topic, priority=priority)
            await self.reply(msg, result={"notified": "all_active"})

        elif action == "status":
            r = await self.get_redis()
            hb = await r.hgetall("rragent:heartbeats")
            agents_status = {}
            now = time.time()
            for name, val in hb.items():
                try:
                    info = json.loads(val)
                    alive = (now - info.get("ts", 0)) < 30
                    agents_status[name] = {"alive": alive, "pid": info.get("pid"), "last_seen": info.get("ts")}
                except Exception:
                    agents_status[name] = {"alive": False}
            await self.reply(msg, result=agents_status)

        elif action == "reload_rules":
            self.rules = load_rules()
            await self.reply(msg, result={"rules_count": len(self.rules)})

        elif action == "memory_health":
            health = await self._get_memory_health()
            await self.reply(msg, result=health)

        elif action == "memory_backup":
            try:
                from agents.memory.lifecycle import daily_backup
                daily_backup()
                await self.reply(msg, result={"backed_up": True})
            except Exception as e:
                logger.error(f"Memory backup failed: {e}")
                await self.reply(msg, error=str(e))

        elif action == "memory_compress":
            try:
                from agents.memory.lifecycle import compress_old_memories, fix_orphan_nodes
                results = {}
                for agent_name in ("analysis", "news", "dev"):
                    results[agent_name] = await compress_old_memories(agent_name)
                fix_result = fix_orphan_nodes()
                results["orphan_fix"] = fix_result
                await self.reply(msg, result=results)
            except Exception as e:
                logger.error(f"Memory compress failed: {e}")
                await self.reply(msg, error=str(e))

        elif action == "memory_remind":
            try:
                from agents.memory.reminder import MemoryReminder
                reminder = MemoryReminder()
                result = await reminder.scan_and_remind()
                await self.reply(msg, result=result)
            except Exception as e:
                logger.error(f"Memory remind failed: {e}")
                await self.reply(msg, error=str(e))

        elif action == "data_source_status":
            try:
                from agents.data_sources.source_router import get_router
                router = get_router()
                await self.reply(msg, result=router.get_status())
            except Exception as e:
                await self.reply(msg, error=str(e))

        elif action == "embed_status":
            try:
                from agents.memory.embedding import EmbeddingClient
                client = EmbeddingClient()
                await self.reply(msg, result=client.get_status())
            except Exception as e:
                await self.reply(msg, error=str(e))

        elif action == "embed_cache_prune":
            try:
                from agents.memory.embedding import EmbedCache
                from agents.memory.config import CLOUD_EMBEDDING_CONFIG
                cache = EmbedCache()
                cache.prune(max_entries=CLOUD_EMBEDDING_CONFIG.get("max_cache_entries", 10000))
                await self.reply(msg, result={"pruned": True})
            except Exception as e:
                await self.reply(msg, error=str(e))

        elif action == "soul_check":
            try:
                from agents.memory.soul_guardian import SoulGuardian
                guardian = SoulGuardian()
                result = await guardian.check_integrity()
                await self.reply(msg, result=result)
            except Exception as e:
                await self.reply(msg, error=str(e))

        elif action == "soul_accept":
            try:
                from agents.memory.soul_guardian import SoulGuardian
                guardian = SoulGuardian()
                await guardian.accept_changes()
                await self.reply(msg, result={"accepted": True})
            except Exception as e:
                await self.reply(msg, error=str(e))

        elif action == "llm_status":
            try:
                from agents.llm_router import get_llm_router
                router = get_llm_router()
                await self.reply(msg, result=router.get_status())
            except Exception as e:
                await self.reply(msg, error=str(e))

        elif action == "daily_briefing":
            try:
                result = await self._generate_daily_briefing()
                await self.reply(msg, result=result)
            except Exception as e:
                await self.reply(msg, error=str(e))

        elif action == "memory_hygiene":
            try:
                from agents.memory.lifecycle import memory_hygiene_report
                result = await memory_hygiene_report()
                await self.reply(msg, result=result)
            except Exception as e:
                await self.reply(msg, error=str(e))

        # ── 记忆分层 (memory-tiering 集成) ──
        elif action == "memory_tier":
            try:
                from agents.memory.lifecycle import tier_all_agents
                result = await tier_all_agents()
                total = sum(r.get("changes", 0) for r in result.values())
                lines = [f"🧠 记忆分层完成 — {total} 条变更"]
                for ag, r in result.items():
                    s = r.get("stats", {})
                    lines.append(f"  {ag}: 🔥HOT={s.get('hot',0)} 🌡️WARM={s.get('warm',0)} ❄️COLD={s.get('cold',0)}")
                await self.reply(msg, result={"text": "\n".join(lines), "details": result})
            except Exception as e:
                await self.reply(msg, error=str(e))

        elif action == "memory_tier_status":
            try:
                from agents.memory.lifecycle import get_tiering_summary
                result = get_tiering_summary()
                lines = [
                    f"🧠 记忆分层概况 (共{result.get('total', 0)}条)",
                    f"  🔥 HOT: {result.get('hot', 0)}  🌡️ WARM: {result.get('warm', 0)}  ❄️ COLD: {result.get('cold', 0)}  ❓ 未分层: {result.get('untiered', 0)}",
                ]
                for ag, s in result.get("by_agent", {}).items():
                    lines.append(f"  {ag}: H={s['hot']} W={s['warm']} C={s['cold']}")
                await self.reply(msg, result={"text": "\n".join(lines), "raw": result})
            except Exception as e:
                await self.reply(msg, error=str(e))

        # ── 结构化教训 (actual-self-improvement 集成) ──
        elif action == "learnings":
            try:
                engine = self._get_reflection_engine()
                if not engine:
                    await self.reply(msg, error="ReflectionEngine 不可用")
                    return
                query = msg.params.get("query", "")
                if query:
                    results = engine.search_learnings(query, limit=10)
                    if results:
                        lines = [f"📚 教训搜索 \"{query}\" — {len(results)} 条匹配"]
                        for r in results:
                            lines.append(f"  [{r['id']}] {r['summary']}")
                        await self.reply(msg, result={"text": "\n".join(lines), "entries": results})
                    else:
                        await self.reply(msg, result={"text": f"未找到与 \"{query}\" 相关的教训"})
                else:
                    stats = engine.get_learnings_stats()
                    lines = [
                        "📚 教训库统计",
                        f"  教训: {stats.get('LEARNINGS.md', 0)} 条",
                        f"  错误: {stats.get('ERRORS.md', 0)} 条",
                        f"  需求: {stats.get('FEATURE_REQUESTS.md', 0)} 条",
                    ]
                    await self.reply(msg, result={"text": "\n".join(lines), "stats": stats})
            except Exception as e:
                await self.reply(msg, error=str(e))

        elif action == "log_learning":
            try:
                engine = self._get_reflection_engine()
                if not engine:
                    await self.reply(msg, error="ReflectionEngine 不可用")
                    return
                summary = msg.params.get("summary", "")
                if not summary:
                    await self.reply(msg, error="缺少教训内容")
                    return
                category = msg.params.get("category", "correction")
                lid = engine.log_learning(
                    summary=summary,
                    category=category,
                    source="user_correction",
                )
                await self.reply(msg, result={"text": f"✅ 已记录教训 {lid}: {summary[:80]}", "id": lid})
            except Exception as e:
                await self.reply(msg, error=str(e))

        elif action == "task_create":
            await self._handle_task_create(msg)

        elif action == "task_status":
            await self._handle_task_status(msg)

        elif action == "task_list":
            await self._handle_task_list(msg)

        elif action == "task_cancel":
            await self._handle_task_cancel(msg)

        elif action == "channel_status":
            try:
                router = await self._get_notify_router()
                await router.refresh_channel_states()
                await self.reply(msg, result=router.get_status())
            except Exception as e:
                await self.reply(msg, error=str(e))

        elif action == "channel_flush":
            try:
                router = await self._get_notify_router()
                flushed = await router.flush_pending()
                await self.reply(msg, result={"flushed": flushed})
            except Exception as e:
                await self.reply(msg, error=str(e))

        elif action == "reflect_insight":
            try:
                engine = self._get_reflection_engine()
                if engine:
                    result = engine.generate_daily_insight()
                    await self.reply(msg, result={"text": result})
                else:
                    await self.reply(msg, result={"text": "反思引擎未就绪"})
            except Exception as e:
                await self.reply(msg, error=str(e))

        elif action == "reflect_weekly":
            try:
                engine = self._get_reflection_engine()
                if engine:
                    result = engine.generate_weekly_report()
                    engine.flush()
                    await self.reply(msg, result={"text": result})
                else:
                    await self.reply(msg, result={"text": "反思引擎未就绪"})
            except Exception as e:
                await self.reply(msg, error=str(e))

        elif action == "reflect_stats":
            try:
                engine = self._get_reflection_engine()
                if engine:
                    stats = engine.get_stats_summary()
                    lines = [
                        "🔍 **反思引擎状态**",
                        f"  今日查询: {stats['today_queries']}  失败: {stats['today_failures']}",
                        f"  跟踪路由数: {stats['tracked_routes']}  历史天数: {stats['days_tracked']}",
                        f"  平均成功率: {stats['avg_success_rate']:.1%}",
                        f"  对话日志: {stats['conv_log_size']} 条",
                    ]
                    if stats["failure_prone_agents"]:
                        lines.append(f"  ⚠️ 不稳定 Agent: {', '.join(stats['failure_prone_agents'])}")
                    hints = engine.get_routing_hints(top_k=5)
                    if hints:
                        lines.append("\n" + hints)
                    await self.reply(msg, result={"text": "\n".join(lines)})
                else:
                    await self.reply(msg, result={"text": "反思引擎未就绪"})
            except Exception as e:
                await self.reply(msg, error=str(e))

        elif action == "list_skills":
            try:
                manifest = self._build_skills_manifest()
                await self.reply(msg, result={"text": manifest})
            except Exception as e:
                await self.reply(msg, error=str(e))

        elif action == "list_agents":
            try:
                info = await self._build_agents_info()
                await self.reply(msg, result={"text": info})
            except Exception as e:
                await self.reply(msg, error=str(e))

        elif action == "factor_list":
            try:
                text = await self._build_factor_list()
                await self.reply(msg, result={"text": text})
            except Exception as e:
                await self.reply(msg, error=str(e))

        elif action == "factor_detail":
            try:
                factor_id = params.get("factor_id", "")
                text = await self._build_factor_detail(factor_id)
                await self.reply(msg, result={"text": text})
            except Exception as e:
                await self.reply(msg, error=str(e))

        elif action.endswith(":response"):
            pass

        else:
            await self.reply(msg, error=f"Unknown orchestrator action: {action}")

    async def _session_history_get(self, uid: str, max_rounds: int = 6) -> list[dict]:
        """从 Redis 读取最近 N 轮对话，返回 [{role, content}, ...]"""
        try:
            r = await self.get_redis()
            key = SESSION_HISTORY_KEY.replace("{uid}", uid)
            raw_list = await r.lrange(key, 0, max_rounds * 2 - 1)
            messages = []
            for raw in reversed(raw_list):
                entry = json.loads(raw)
                messages.append({"role": entry["role"], "content": entry["content"]})
            return messages
        except Exception as e:
            logger.debug(f"session history read error: {e}")
            return []

    async def _session_history_append(self, uid: str, role: str, content: str):
        """追加一条对话记录到 session history（带去重）"""
        try:
            r = await self.get_redis()
            key = SESSION_HISTORY_KEY.replace("{uid}", uid)
            # 去重: 如果最近一条是同角色同内容，跳过
            latest = await r.lindex(key, 0)
            if latest:
                try:
                    prev = json.loads(latest)
                    if prev.get("role") == role and prev.get("content") == content[:2000]:
                        return  # 跳过重复
                except Exception:
                    pass
            entry = json.dumps({"role": role, "content": content[:2000], "ts": time.time()}, ensure_ascii=False)
            await r.lpush(key, entry)
            await r.ltrim(key, 0, SESSION_HISTORY_MAX - 1)
            await r.expire(key, SESSION_HISTORY_TTL)
        except Exception as e:
            logger.debug(f"session history write error: {e}")

    async def _send_with_progress(self, target: str, action: str, params: dict,
                                   timeout: float = 0, reply_channel: str = "", msg_id: str = ""):
        """发送消息并将子 Agent 的进度实时转发到 reply_channel"""
        from agents.base import AgentMessage, MSG_TIMEOUT
        out_msg = AgentMessage.create(self.name, target, action, params=params or {})

        # 注册进度回调 — base.py _listen 会调用
        if reply_channel:
            async def _on_progress(text):
                await self._progress_to_channel(reply_channel, msg_id or out_msg.id, text, source=target)
            self._progress_callbacks[out_msg.id] = _on_progress

        try:
            resp = await self.request(out_msg, timeout=timeout or MSG_TIMEOUT)
        finally:
            self._progress_callbacks.pop(out_msg.id, None)
        return resp

    async def _progress_to_channel(self, reply_channel: str, msg_id: str, text: str, source: str = "manager"):
        """向 reply_channel 推送中间进度，TG Bot 会实时转发给用户"""
        if not reply_channel:
            return
        try:
            r = await self.get_redis()
            payload = {"type": "progress", "text": text, "in_reply_to": msg_id,
                       "timestamp": time.time(), "source": source}
            await r.publish(reply_channel, json.dumps(payload, ensure_ascii=False, default=str))
        except Exception:
            pass

    async def _reply_to_channel(self, reply_channel: str, msg_id: str, text: str, raw=None, source: str = "manager"):
        """统一回复到 reply_channel，确保 Telegram Bot 总能收到"""
        if not reply_channel:
            return
        r = await self.get_redis()
        payload = {"type": "done", "text": text, "in_reply_to": msg_id, "timestamp": time.time(), "source": source}
        if raw is not None:
            payload["raw"] = raw
        await r.publish(reply_channel, json.dumps(payload, ensure_ascii=False, default=str))

    async def _route_command(self, msg: AgentMessage):
        cmd = msg.params.get("command", "")
        args = msg.params.get("args", "")
        reply_channel = msg.params.get("reply_channel", "")
        uid = msg.params.get("uid", msg.sender)
        user_name = msg.params.get("user_name", "")

        if user_name and uid:
            try:
                r = await self.get_redis()
                await r.hset("rragent:user_profiles", uid, json.dumps({"name": user_name, "uid": uid}, ensure_ascii=False))
            except Exception:
                pass

        if args:
            from agents.memory.input_guard import guard_input
            guard = guard_input(args)
            if not guard.safe:
                err = f"输入安全检查未通过: {guard.reason}"
                await self._reply_to_channel(reply_channel, msg.id, f"❌ {err}")
                await self.reply(msg, error=err)
                return
            args = guard.sanitized

        await self._record_behavior(uid, cmd)

        if cmd == "chat":
            await self._progress_to_channel(reply_channel, msg.id, "💬 正在理解你的意图...")
            await self._intent_route(args, uid, reply_channel, msg.id, user_name=user_name)
            await self.reply(msg, result={"text": "intent_routed"})
            return

        if cmd == "ask":
            await self._progress_to_channel(reply_channel, msg.id, "🧠 正在分析问题...", source="analysis")
            result = await self._cross_agent_ask(args)
            await self._reply_to_channel(reply_channel, msg.id, result, source="analysis")
            await self.reply(msg, result={"text": result})
            return

        if cmd == "status":
            r = await self.get_redis()
            hb = await r.hgetall("rragent:heartbeats")
            lines = ["📊 Agent 状态:"]
            now = time.time()
            for name, val in sorted(hb.items()):
                try:
                    info = json.loads(val)
                    alive = (now - info.get("ts", 0)) < 30
                    icon = "✅" if alive else "❌"
                    lines.append(f"  {icon} {name} (pid:{info.get('pid','?')})")
                except Exception:
                    lines.append(f"  ❓ {name}")
            text = "\n".join(lines)
            await self._reply_to_channel(reply_channel, msg.id, text)
            await self.reply(msg, result={"text": text})
            return

        if cmd in ("jobs", "task_list"):
            await self._handle_task_status(msg)
            return

        if cmd == "task_new":
            preset = args.strip() if args else ""
            msg.params["preset"] = preset
            await self._handle_task_create(msg)
            return

        if cmd == "task_cancel":
            msg.params["task_id"] = args.strip()
            await self._handle_task_cancel(msg)
            return

        if cmd == "task_status":
            msg.params["task_id"] = args.strip()
            await self._handle_task_status(msg)
            return

        if cmd == "channel":
            try:
                router = await self._get_notify_router()
                await router.refresh_channel_states()
                status = router.get_status()
                lines = ["📡 渠道状态:"]
                now_ts = time.time()
                for ch_name, ch_info in sorted(status["channels"].items()):
                    age = ch_info.get("age") or 999
                    icon = "✅" if ch_info["online"] else "❌"
                    if ch_info.get("degraded"):
                        icon = "⚠️"
                    lines.append(
                        f"  {icon} {ch_name}: "
                        f"{'在线' if ch_info['online'] else '离线'} "
                        f"(心跳 {age:.0f}s前, 失败 {ch_info.get('consecutive_failures', 0)}次)"
                    )
                lines.append(f"\n活跃渠道: {status['active']}")
                stats = status.get("stats", {})
                lines.append(f"统计: 已发 {stats.get('total_sent', 0)} | "
                             f"转移 {stats.get('failovers', 0)} | "
                             f"积压 {stats.get('pending_queued', 0)}")
                text = "\n".join(lines)
            except Exception as e:
                text = f"❌ 渠道状态获取失败: {e}"
            await self._reply_to_channel(reply_channel, msg.id, text)
            await self.reply(msg, result={"text": text})
            return

        progress_channel = msg.params.get("progress_channel", "")
        system_result = await self._handle_system_cmd(cmd, args, progress_channel=progress_channel)
        if system_result is not None:
            text = self._result_to_text(system_result, source="system", action=cmd)
            raw = system_result if isinstance(system_result, dict) else None
            await self._reply_to_channel(reply_channel, msg.id, text, raw=raw)
            await self.reply(msg, result={"text": text} if raw is None else system_result)
            return

        if cmd not in COMMAND_ROUTES:
            combined_input = f"/{cmd} {args}".strip() if args else f"/{cmd}"
            await self._progress_to_channel(reply_channel, msg.id, f"🤔 正在分析意图...")
            await self._intent_route(combined_input, uid, reply_channel, msg.id, user_name=user_name)
            await self.reply(msg, result={"text": "intent_routed"})
            return

        target_agent, target_action = COMMAND_ROUTES[cmd]
        params = PARAM_MAP.get(cmd, lambda a: {"args": a})(args)
        timeout = ACTION_TIMEOUTS.get(target_action, 0)

        await self._progress_to_channel(reply_channel, msg.id,
                                        f"🔄 正在执行 /{cmd}...", source=target_agent)
        resp = await self._send_with_progress(
            target_agent, target_action, params, timeout=timeout,
            reply_channel=reply_channel, msg_id=msg.id,
        )
        if resp.error:
            await self._reply_to_channel(reply_channel, msg.id, f"❌ {resp.error}", source=target_agent)
            await self.reply(msg, error=resp.error)
        else:
            result = resp.result
            text = self._result_to_text(result, source=target_agent, action=target_action)
            await self._reply_to_channel(reply_channel, msg.id, text, raw=result, source=target_agent)
            await self.reply(msg, result=result)

    async def _handle_system_cmd(self, cmd: str, args: str, progress_channel: str = ""):
        """处理系统诊断命令，返回结果 dict/str 或 None（非系统命令）"""
        try:
            if cmd == "llm_status":
                from agents.llm_router import get_llm_router
                return get_llm_router().get_status()
            elif cmd == "embed_status":
                from agents.memory.embedding import EmbeddingClient
                return EmbeddingClient().get_status()
            elif cmd == "data_source_status":
                from agents.data_sources.source_router import get_router
                return get_router().get_status()
            elif cmd == "soul_check":
                from agents.memory.soul_guardian import SoulGuardian
                return await SoulGuardian().check_integrity()
            elif cmd == "memory_health":
                return await self._get_memory_health()
            elif cmd == "memory_hygiene":
                from agents.memory.lifecycle import memory_hygiene_report
                return await memory_hygiene_report()
            elif cmd == "status":
                r = await self.get_redis()
                hb = await r.hgetall("rragent:heartbeats")
                lines = ["📊 Agent 状态:"]
                now = time.time()
                for name, val in sorted(hb.items()):
                    try:
                        info = json.loads(val)
                        age = now - info.get("ts", 0)
                        mark = "🟢" if age < 30 else ("🟡" if age < 60 else "🔴")
                        lines.append(f"  {mark} {name}: {age:.0f}s ago (pid={info.get('pid',0)})")
                    except Exception:
                        lines.append(f"  ❓ {name}")
                return "\n".join(lines)
            elif cmd == "quant":
                from agents.quant_pipeline import run_quant_pipeline
                try:
                    q_params = json.loads(args) if args.strip().startswith("{") else {"topic": args}
                except (json.JSONDecodeError, AttributeError):
                    q_params = {"topic": args if args else ""}
                topic = q_params.get("topic", "").strip() or "今日市场热点板块策略"
                bt_mode = q_params.get("mode") or "vectorbt"
                max_rounds = int(q_params.get("max_rounds") or 5)
                base_strat = q_params.get("base_strategy")
                async def _notify_fn(text):
                    await self._notify(text, topic="strategy", priority="normal")
                result = await run_quant_pipeline(self, topic, notify_fn=_notify_fn, base_strategy=base_strat if isinstance(base_strat, dict) else None, progress_channel=progress_channel, backtest_mode=bt_mode, max_rounds=max_rounds)
                return {"text": result.get("summary", str(result)), "metrics": result.get("metrics", {}), "status": result.get("status", ""), "code": result.get("code", "")}
            elif cmd == "reflect":
                engine = self._get_reflection_engine()
                if engine:
                    return {"text": engine.generate_daily_insight()}
                return {"text": "反思引擎未就绪"}
            elif cmd == "reflect_weekly":
                engine = self._get_reflection_engine()
                if engine:
                    report = engine.generate_weekly_report()
                    engine.flush()
                    return {"text": report}
                return {"text": "反思引擎未就绪"}
            elif cmd == "reflect_stats":
                engine = self._get_reflection_engine()
                if engine:
                    stats = engine.get_stats_summary()
                    hints = engine.get_routing_hints(top_k=5)
                    lines = [
                        "🔍 反思引擎状态",
                        f"  今日查询: {stats['today_queries']}  失败: {stats['today_failures']}",
                        f"  跟踪路由: {stats['tracked_routes']}  历史天数: {stats['days_tracked']}",
                        f"  平均成功率: {stats['avg_success_rate']:.1%}",
                    ]
                    if stats["failure_prone_agents"]:
                        lines.append(f"  ⚠️ 不稳定: {', '.join(stats['failure_prone_agents'])}")
                    if hints:
                        lines.append("\n" + hints)
                    return {"text": "\n".join(lines)}
                return {"text": "反思引擎未就绪"}
            elif cmd == "skills":
                return self._build_skills_manifest()
            elif cmd == "agents":
                return await self._build_agents_info()
            elif cmd == "factor_list":
                return await self._build_factor_list()
            elif cmd == "factor_detail":
                return await self._build_factor_detail(args.strip() if args else "")
            elif cmd == "quant_optimize":
                from agents.quant_pipeline import run_quant_pipeline
                try:
                    params = json.loads(args) if args.strip().startswith("{") else {"topic": args}
                except json.JSONDecodeError:
                    params = {"topic": args}
                topic = params.get("topic", "策略优化")
                bt_mode = params.get("mode", "vectorbt")
                max_rounds = int(params.get("max_rounds") or 5)
                base_strategy = {"title": params.get("base_title", "")}

                if params.get("base_preset"):
                    base_strategy["preset"] = params["base_preset"]
                else:
                    base_strategy["code"] = params.get("base_code", "")
                    base_strategy["metrics"] = params.get("base_metrics", {})

                async def _notify_opt(text):
                    await self._notify(text, topic="strategy", priority="normal")
                result = await run_quant_pipeline(self, topic, notify_fn=_notify_opt, base_strategy=base_strategy, progress_channel=progress_channel, backtest_mode=bt_mode, max_rounds=max_rounds)
                return {"text": result.get("summary", str(result)), "metrics": result.get("metrics", {}), "status": result.get("status", ""), "code": result.get("code", "")}
            elif cmd == "digger":
                from agents.alpha_digger import run_alpha_digger
                try:
                    d_params = json.loads(args) if args and args.strip().startswith("{") else {}
                except (json.JSONDecodeError, AttributeError):
                    d_params = {}
                async def _notify_dig(text):
                    await self._notify(text, topic="strategy", priority="normal")
                    if progress_channel:
                        try:
                            r = await self.get_redis()
                            await r.publish(progress_channel, json.dumps({"type": "progress", "text": text}, ensure_ascii=False))
                        except Exception:
                            pass
                result = await run_alpha_digger(
                    orchestrator=self,
                    notify_fn=_notify_dig,
                    max_rounds=d_params.get("rounds", 10),
                    factors_per_round=d_params.get("factors", 5),
                    round_interval=d_params.get("interval", 60),
                )
                if progress_channel:
                    try:
                        r = await self.get_redis()
                        await r.publish(progress_channel, json.dumps({"type": "done", "text": result.get("summary", "")}, ensure_ascii=False))
                    except Exception:
                        pass
                return result.get("summary", str(result))
            elif cmd == "digger_status":
                from agents.alpha_digger import get_digger_status
                stats = await get_digger_status()
                lines = ["📊 因子库状态:"]
                lines.append(f"  活跃因子: {stats.get('active_count', 0)}")
                lines.append(f"  衰减因子: {stats.get('decayed_count', 0)}")
                lines.append(f"  总数: {stats.get('total_count', 0)}")
                if stats.get("best_sharpe"):
                    lines.append(f"  最佳 Sharpe: {stats['best_sharpe']:.3f}")
                if stats.get("best_ir"):
                    lines.append(f"  最佳 IR: {stats['best_ir']:.3f}")
                if stats.get("ready_to_combine"):
                    lines.append("  🔮 已达融合阈值!")
                if stats.get("theme_distribution"):
                    lines.append("  主题分布:")
                    for theme, cnt in stats["theme_distribution"].items():
                        lines.append(f"    {theme}: {cnt}")
                return "\n".join(lines)
            elif cmd == "security_audit":
                from agents.memory.input_guard import audit_skills, check_url
                parts = ["🛡️ 安全审计报告:\n"]
                # ClawDefender 全面扫描
                audit_result = audit_skills()
                if audit_result:
                    parts.append("── ClawDefender 审计 ──")
                    parts.append(audit_result[:3000])
                else:
                    parts.append("⚠️ ClawDefender 未就绪，使用内置检查")
                # 检查已安装 skills
                import glob
                skill_dirs = glob.glob(os.path.expanduser("~/skills/*/")) + glob.glob(os.path.expanduser("~/openclaw/skills/*/"))
                parts.append(f"\n── 已安装 Skills ({len(skill_dirs)}) ──")
                for sd in sorted(skill_dirs):
                    name = os.path.basename(sd.rstrip("/"))
                    parts.append(f"  • {name}")
                return {"text": "\n".join(parts)}
            elif cmd == "multi_research":
                # 多 Agent 协作研究: news(web_search) → analysis(ask) → strategist
                topic = args or "市场热点"
                results = []
                # Step 1: 网搜
                r1 = await self.send("news", "web_search", {"query": topic}, timeout=60)
                if not r1.error:
                    results.append(("news.web_search", self._result_to_text(r1.result)))
                # Step 2: 基于搜索结果让分析 agent 深度分析
                search_context = results[0][1][:2000] if results else topic
                r2 = await self.send("analysis", "ask", {"question": f"基于以下信息分析: {search_context}", "args": search_context}, timeout=120)
                if not r2.error:
                    results.append(("analysis.ask", self._result_to_text(r2.result)))
                # Step 3: 策略建议
                analysis_context = results[-1][1][:2000] if len(results) > 1 else search_context
                r3 = await self.send("strategist", "ask_strategy", {"args": f"基于分析给出策略建议: {analysis_context}"}, timeout=120)
                if not r3.error:
                    results.append(("strategist.ask_strategy", self._result_to_text(r3.result)))
                # 聚合
                summary_parts = [f"🔗 多 Agent 协作研究: {topic}\n"]
                for src, text in results:
                    summary_parts.append(f"── {src} ──\n{text[:1500]}\n")
                return {"text": "\n".join(summary_parts)}
            elif cmd == "combine_exhaustive":
                from agents.factor_library import FactorLibrary
                from agents.bridge_client import get_bridge_client
                from itertools import combinations
                from datetime import date, timedelta
                try:
                    ce_params = json.loads(args) if args and args.strip().startswith("{") else {}
                except (json.JSONDecodeError, AttributeError):
                    ce_params = {}
                group_size = min(max(ce_params.get("group_size", 2), 2), 5)
                max_combos = min(ce_params.get("max_combos", 30), 200)
                r = await self.get_redis()
                lib = FactorLibrary(redis_client=r)
                bridge = get_bridge_client()
                candidates = await lib.get_combine_candidates()
                if len(candidates) < group_size:
                    return f"可融合因子仅 {len(candidates)} 个，不足 {group_size}"
                history = await lib.get_combine_records(limit=500)
                tested = set(tuple(sorted(rec.get("input_factor_ids", []))) for rec in history)
                all_combos = list(combinations(range(len(candidates)), group_size))
                combos = [c for c in all_combos if tuple(sorted(candidates[i].id for i in c)) not in tested][:max_combos]
                start_d = (date.today() - timedelta(days=180)).isoformat()
                end_d = date.today().isoformat()
                accepted, tested_n = 0, 0
                for combo in combos:
                    factors = [candidates[i] for i in combo]
                    codes = [f.code.replace("def generate_factor(", f"def _factor_{j+1}(") for j, f in enumerate(factors)]
                    combiner = "\n\nimport numpy as np\nimport pandas as pd\n\ndef generate_factor(matrices):\n    factors = []\n"
                    for j in range(len(factors)):
                        combiner += f"    try:\n        factors.append(_factor_{j+1}(matrices))\n    except Exception:\n        pass\n"
                    combiner += "    if not factors:\n        return pd.DataFrame(0, index=matrices['close'].index, columns=matrices['close'].columns)\n    stacked = np.stack([f.values for f in factors], axis=0)\n    combined = np.nanmean(stacked, axis=0)\n    return pd.DataFrame(combined, index=matrices['close'].index, columns=matrices['close'].columns)\n"
                    combined_code = "\n\n".join(codes) + combiner
                    try:
                        resp = await bridge.run_factor_mining(factor_code=combined_code, start_date=start_d, end_date=end_d)
                        metrics = resp.get("metrics") or {} if resp.get("status") != "error" else {}
                    except Exception:
                        metrics = {}
                    input_info = [{"id": f.id, "theme": f.sub_theme or f.theme, "sharpe": f.sharpe, "ir": f.ir, "ic_mean": f.ic_mean} for f in factors]
                    evaluation = lib.evaluate_combine_quality(input_info, metrics)
                    record = {"input_factors": input_info, "input_factor_ids": [f.id for f in factors], "combined_metrics": metrics, "evaluation": evaluation, "verdict": evaluation["verdict"], "status": "accepted" if evaluation["verdict"] == "accept" else "rejected", "source": "rule_exhaustive"}
                    await lib.save_combine_record(record)
                    if evaluation["verdict"] == "accept":
                        accepted += 1
                    tested_n += 1
                    await asyncio.sleep(1)
                summary = f"穷举融合完成: 测试 {tested_n}/{len(combos)} 组合, {accepted} 个被采纳 (group_size={group_size})"
                await self._notify(summary, topic="strategy", priority="normal")
                return summary
            elif cmd == "intraday_select":
                from agents.intraday_pipeline import run_post_market_selection
                strategy = args.strip() if args else ""
                async def _notify_sel(text):
                    await self._notify(text, topic="strategy", priority="normal")
                result = await run_post_market_selection(self, strategy_name=strategy, notify_fn=_notify_sel, progress_channel=progress_channel)
                return self._result_to_text(result, source="intraday", action="select")
            elif cmd == "intraday_monitor":
                logic = args.strip() if args else ""
                resp = await self.send("intraday", "start_monitor", {"strategy_logic": logic})
                return self._result_to_text(resp.result, "intraday", "monitor") if resp.result else resp.error or "❌ intraday agent 无响应"
            elif cmd == "intraday_status":
                resp = await self.send("intraday", "get_status", {})
                return self._result_to_text(resp.result, "intraday", "status") if resp.result else resp.error or "❌ intraday agent 无响应"
            elif cmd == "intraday_scan":
                logic = args.strip() if args else ""
                resp = await self.send("intraday", "scan", {"strategy_logic": logic})
                return self._result_to_text(resp.result, "intraday", "scan") if resp.result else resp.error or "❌ intraday agent 无响应"
            elif cmd == "intraday_stop":
                resp = await self.send("intraday", "stop_monitor", {})
                return self._result_to_text(resp.result, "intraday", "stop") if resp.result else resp.error or "❌ intraday agent 无响应"
            elif cmd == "intraday_pool":
                resp = await self.send("intraday", "get_pool", {})
                return self._result_to_text(resp.result, "intraday", "pool") if resp.result else resp.error or "❌ intraday agent 无响应"
        except Exception as e:
            return f"❌ {cmd} 失败: {e}"
        return None

    async def _record_behavior(self, uid: str, cmd: str):
        """Redis 行为画像: 命令频率 + 时段偏好 + 最近查询"""
        try:
            r = await self.get_redis()
            freq_key = BEHAVIOR_CMD_FREQ_KEY.format(uid=uid)
            await r.zincrby(freq_key, 1, cmd)
            await r.expire(freq_key, 86400 * 30)

            time_key = BEHAVIOR_TIME_KEY.format(uid=uid)
            hour_slot = str(datetime.now().hour)
            await r.hincrby(time_key, hour_slot, 1)
            await r.expire(time_key, 86400 * 30)

            recent_key = BEHAVIOR_RECENT_KEY.format(uid=uid)
            await r.lpush(recent_key, json.dumps({
                "cmd": cmd, "ts": time.time(),
            }))
            await r.ltrim(recent_key, 0, 49)
        except Exception as e:
            logger.debug(f"Behavior record failed: {e}")

    async def _cross_agent_ask(self, question: str) -> str:
        market_resp = await self.send("market", "get_all_raw")
        if market_resp.error:
            return f"获取行情数据失败: {market_resp.error}"

        analysis_resp = await self.send("analysis", "ask", {
            "question": question,
            "market_data": market_resp.result,
        })
        if analysis_resp.error:
            return f"分析失败: {analysis_resp.error}"

        return self._result_to_text(analysis_resp.result, source="analysis", action="ask")

    def _load_capability_manifest(self) -> str:
        """从 skills YAML 构建能力清单，用于 LLM 意图识别"""
        skills_dir = Path(__file__).parent / "skills"
        manifest_lines = []
        if not skills_dir.exists():
            return ""
        for yf in sorted(skills_dir.glob("*.yaml")):
            try:
                data = yaml.safe_load(yf.read_text())
                agent = data.get("agent", yf.stem)
                desc = data.get("description", "")
                skills = data.get("skills", [])
                skill_list = ", ".join(
                    f"{s['name']}({s.get('description','')})"
                    for s in skills if isinstance(s, dict)
                )
                manifest_lines.append(f"- {agent}: {desc} | 技能: {skill_list}")
            except Exception:
                continue
        return "\n".join(manifest_lines)

    async def _build_memory_context(self, uid: str, user_input: str) -> str:
        """用 embedding 模型做语义检索，从长期记忆中提取相关片段"""
        try:
            async def _do_memory_lookup():
                from agents.memory.embedding import EmbeddingClient
                from agents.memory.vector_store import VectorStore
                embedder = EmbeddingClient()
                vec = await embedder.embed(user_input)
                if vec is None:
                    return ""
                # 搜索 orchestrator + general 两个记忆库
                all_hits = []
                for collection in ("orchestrator_memory", "general_memory"):
                    try:
                        store = VectorStore(collection)
                        hits = store.query(vec, n=3)
                        all_hits.extend(hits)
                    except Exception:
                        pass
                if not all_hits:
                    return ""
                # 按相似度排序取 top 5
                all_hits.sort(key=lambda h: h.cosine_sim, reverse=True)
                lines = ["[长期记忆 — 语义相关历史片段]"]
                for i, h in enumerate(all_hits[:5], 1):
                    date_str = h.metadata.get("date", "")
                    snippet = h.content[:500].replace("\n", " ")
                    lines.append(f"  {i}. [{date_str}] (sim={h.cosine_sim:.2f}): {snippet}")
                return "\n".join(lines) + "\n"
            return await asyncio.wait_for(_do_memory_lookup(), timeout=8)
        except asyncio.TimeoutError:
            logger.warning("Memory context lookup timed out (8s)")
            return ""
        except Exception as e:
            logger.debug(f"Memory context build failed (non-fatal): {e}")
            return ""


    # ── 响应人性化 ─────────────────────────────────────

    def _result_to_text(self, result, source: str = "", action: str = "") -> str:
        if isinstance(result, str):
            return result
        if not isinstance(result, dict):
            return str(result)
        if "text" in result and isinstance(result["text"], str) and result["text"].strip():
            return result["text"]
        return self._dict_to_readable(result, source, action)

    def _dict_to_readable(self, d: dict, source: str = "", action: str = "") -> str:
        if "error" in d:
            return f"❌ {d['error']}"
        SECTION_ICONS = {
            "status": "📊", "agents": "🤖", "channels": "📡", "stats": "📈",
            "graph": "🕸️", "memory": "🧠", "cpu": "⚡", "disk": "💾",
            "network": "🌐", "version": "📦", "config": "⚙️", "current": "🎯",
            "providers": "☁️", "task_preferences": "📋", "info": "ℹ️",
            "degradation_recent": "⚠️", "health": "🏥", "result": "📋",
            "pruned": "🧹", "accepted": "✅", "flushed": "📤",
        }
        lines = []
        for key, val in d.items():
            icon = SECTION_ICONS.get(key, "▸")
            label = key.replace("_", " ").title()
            if isinstance(val, bool):
                lines.append(f"{icon} {label}: {'✅ 是' if val else '❌ 否'}")
            elif isinstance(val, (int, float)):
                lines.append(f"{icon} {label}: {val}")
            elif isinstance(val, str):
                if len(val) > 200:
                    lines.append(f"{icon} {label}:\n  {val[:200]}...")
                else:
                    lines.append(f"{icon} {label}: {val}")
            elif isinstance(val, list):
                if not val:
                    lines.append(f"{icon} {label}: (空)")
                elif len(val) <= 5 and all(isinstance(v, str) for v in val):
                    lines.append(f"{icon} {label}: {', '.join(val)}")
                else:
                    lines.append(f"{icon} {label}: ({len(val)} 项)")
                    for i, item in enumerate(val[:10]):
                        if isinstance(item, dict):
                            summary = " | ".join(f"{k}={v}" for k, v in list(item.items())[:4])
                            lines.append(f"  {i+1}. {summary}")
                        else:
                            lines.append(f"  {i+1}. {item}")
                    if len(val) > 10:
                        lines.append(f"  ... 还有 {len(val)-10} 项")
            elif isinstance(val, dict):
                lines.append(f"{icon} {label}:")
                for k2, v2 in val.items():
                    if isinstance(v2, dict):
                        sub = " | ".join(f"{sk}={sv}" for sk, sv in list(v2.items())[:4])
                        lines.append(f"  {k2}: {sub}")
                    elif isinstance(v2, (list, tuple)):
                        lines.append(f"  {k2}: {len(v2)} 项")
                    else:
                        lines.append(f"  {k2}: {v2}")
        return "\n".join(lines) if lines else json.dumps(d, ensure_ascii=False, indent=2)

    async def _humanize(self, text: str, source: str = "", action: str = "") -> str:
        if not text or len(text) < 20 or not self._needs_polish(text):
            return text
        try:
            from agents.llm_router import get_llm_router
            router = get_llm_router()
            truncated = text[:4000]
            ctx = f"来源: {source}.{action}" if source else ""
            result = await router.chat([
                {"role": "system", "content": (
                    "你是信息格式化助手。将以下原始数据/输出转为清晰简洁的中文摘要。\n"
                    "规则: 用 emoji 标注类目，保留关键数值，去冗余，结构化分行。\n"
                    "不加前缀(如'以下是')，不猜测，不加额外解释，只输出格式化结果。\n"
                    f"{ctx}"
                )},
                {"role": "user", "content": truncated},
            ], task_type="brief", temperature=0.1)
            return result or text
        except Exception:
            return text

    @staticmethod
    def _needs_polish(text: str) -> bool:
        stripped = text.strip()
        if stripped.startswith(("{", "[")):
            try:
                json.loads(stripped)
                return len(stripped) > 100
            except json.JSONDecodeError:
                pass
        CLEAN_MARKERS = "📊📈🔍✅❌⚡💻🧠📋🌐🔧⏱️🖥️💾🎵📅⏰📝👤📧🔔📂🤖📡▸🏥🕸️☁️🎯📦⚙️🧹📤"
        if any(c in stripped[:5] for c in CLEAN_MARKERS):
            return False
        if stripped.startswith(("##", "**", "# ")):
            return False
        return False

    def _get_agent_summary(self) -> str:
        skills_dir = Path(__file__).parent / "skills"
        lines = []
        if not skills_dir.exists():
            return "general: 通用问答与任务处理"
        for yf in sorted(skills_dir.glob("*_skills.yaml")):
            try:
                data = yaml.safe_load(yf.read_text(encoding="utf-8")) or {}
                agent = data.get("agent", yf.stem.replace("_skills", ""))
                desc = str(data.get("description", "")).strip()
                brief = desc.split("。")[0].split("—")[0].strip()
                if not brief:
                    brief = "通用能力"
                lines.append(f"{agent}: {brief}")
            except Exception:
                continue
        return "\n".join(lines[:16]) if lines else "general: 通用问答与任务处理"

    def _build_skills_manifest(self) -> str:
        """列出所有 Agent 的 skills 清单，供 /skills 命令使用"""
        skills_dir = Path(__file__).parent / "skills"
        if not skills_dir.exists():
            return "未找到 skills 目录"
        sections = []
        for yf in sorted(skills_dir.glob("*_skills.yaml")):
            try:
                data = yaml.safe_load(yf.read_text(encoding="utf-8")) or {}
                agent = data.get("agent", yf.stem.replace("_skills", ""))
                desc = data.get("description", "")
                skills = data.get("skills", [])
                lines = [f"🤖 **{agent}** — {desc}"]
                for s in skills:
                    if not isinstance(s, dict):
                        continue
                    name = s.get("name", "?")
                    sdesc = s.get("description", "")
                    trigger = s.get("trigger", "")
                    line = f"  ▸ {name}: {sdesc}"
                    if trigger:
                        line += f" [{trigger}]"
                    lines.append(line)
                sections.append("\n".join(lines))
            except Exception:
                continue
        return "\n\n".join(sections) if sections else "暂无 skills 数据"

    async def _build_agents_info(self) -> str:
        """列出所有 Agent 运行状态 + 目标 + 技能概要"""
        r = await self.get_redis()
        hb_raw = await r.hgetall("rragent:heartbeats")
        skills_dir = Path(__file__).parent / "skills"
        agent_meta = {}
        if skills_dir.exists():
            for yf in sorted(skills_dir.glob("*_skills.yaml")):
                try:
                    data = yaml.safe_load(yf.read_text(encoding="utf-8")) or {}
                    name = data.get("agent", yf.stem.replace("_skills", ""))
                    agent_meta[name] = {
                        "description": data.get("description", ""),
                        "skills": [s.get("name", "?") for s in data.get("skills", []) if isinstance(s, dict)],
                    }
                except Exception:
                    continue
        lines = ["📋 **Agent 全景**\n"]
        now = time.time()
        for name in sorted(set(list(hb_raw.keys()) + list(agent_meta.keys()))):
            hb = {}
            status = "offline"
            if name in hb_raw:
                try:
                    hb = json.loads(hb_raw[name])
                    age = now - hb.get("ts", 0)
                    status = "online" if age < 30 else ("slow" if age < 60 else "offline")
                except Exception:
                    pass
            icon = "🟢" if status == "online" else ("🟡" if status == "slow" else "⚪")
            meta = agent_meta.get(name, {})
            desc = meta.get("description", "")
            skills = meta.get("skills", hb.get("skills", []))
            lines.append(f"{icon} **{name}** [{status}]")
            if desc:
                lines.append(f"  目标: {desc}")
            if skills:
                lines.append(f"  技能: {', '.join(skills[:10])}")
            lines.append("")
        return "\n".join(lines)

    async def _build_factor_list(self) -> str:
        """列出因子库中所有因子摘要"""
        try:
            from agents.factor_library import get_factor_library
            lib = get_factor_library()
            factors = await lib.get_all_factors(status="")
            stats = await lib.get_stats()
        except Exception as e:
            return f"因子库加载失败: {e}"
        if not factors:
            return "📊 因子库为空，尚无入库因子"
        lines = [
            f"📊 **因子库** (活跃: {stats.get('active_count', 0)} / 衰减: {stats.get('decayed_count', 0)} / 总数: {stats.get('total_count', 0)})",
        ]
        if stats.get("best_sharpe"):
            lines.append(f"  最佳 Sharpe: {stats['best_sharpe']:.3f}  最佳 IR: {stats.get('best_ir', 0):.3f}  平均 Sharpe: {stats.get('avg_sharpe', 0):.3f}")
        if stats.get("ready_to_combine"):
            lines.append("  🔮 已达融合阈值!")
        lines.append("")
        for i, f in enumerate(sorted(factors, key=lambda x: x.sharpe, reverse=True), 1):
            s = "🟢" if f.status == "active" else "🔴"
            lines.append(f"{s} {i}. [{f.id}] {f.theme}/{f.sub_theme} — Sharpe {f.sharpe:.3f}  IR {f.ir:.3f}  WR {f.win_rate:.1%}  DD {f.max_drawdown:.1%}")
        lines.append(f"\n查看详情: /factor_detail <factor_id>")
        return "\n".join(lines)

    async def _build_factor_detail(self, factor_id: str) -> str:
        """展示单个因子的完整详情"""
        if not factor_id:
            return "用法: /factor_detail <factor_id>"
        try:
            from agents.factor_library import get_factor_library
            lib = get_factor_library()
            factors = await lib.get_all_factors(status="")
        except Exception as e:
            return f"因子库加载失败: {e}"
        target = None
        for f in factors:
            if f.id == factor_id:
                target = f
                break
        if not target:
            return f"未找到因子: {factor_id}"
        lines = [
            f"📊 **因子详情: {target.id}**",
            f"  状态: {target.status}",
            f"  主题: {target.theme} / {target.sub_theme}",
            f"  Sharpe: {target.sharpe:.4f}",
            f"  Win Rate: {target.win_rate:.2%}",
            f"  IC Mean: {target.ic_mean:.6f}",
            f"  IR: {target.ir:.4f}",
            f"  单调性: {target.monotonicity:.4f}",
            f"  换手率: {target.turnover:.4f}",
            f"  最大回撤: {target.max_drawdown:.2%}",
            f"  交易次数: {target.trades}",
            f"  分位价差: {target.quantile_spread:.4f}",
        ]
        if target.decay_halflife is not None:
            lines.append(f"  衰减半衰期: {target.decay_halflife}")
        from datetime import datetime as _dt
        if target.created_at:
            lines.append(f"  创建时间: {_dt.fromtimestamp(target.created_at).strftime('%Y-%m-%d %H:%M')}")
        if target.last_validated:
            lines.append(f"  最后验证: {_dt.fromtimestamp(target.last_validated).strftime('%Y-%m-%d %H:%M')}")
        lines.append(f"  验证次数: {target.validation_count}")
        if target.code:
            code_preview = target.code[:500]
            if len(target.code) > 500:
                code_preview += "\n... (截断)"
            lines.append(f"\n📝 **因子代码:**\n```python\n{code_preview}\n```")
        return "\n".join(lines)

    @staticmethod
    def _strip_json_fence(text: str) -> str:
        raw = (text or "").strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        return raw

    @staticmethod
    def _extract_domain(text: str) -> str:
        m = re.search(r'([a-zA-Z0-9][-a-zA-Z0-9]*\.)+[a-zA-Z]{2,}', text)
        return m.group(0) if m else ""

    @staticmethod
    def _extract_host(text: str) -> str:
        m = re.search(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})', text)
        if m:
            return m.group(1)
        m = re.search(r'(\d{2,3})(?:服务器|号|机)', text)
        if m:
            return f"192.168.1.{m.group(1)}"
        m = re.search(r'(?:服务器|主机|host)\s*(\d{2,3})', text)
        if m:
            return f"192.168.1.{m.group(1)}"
        return ""

    @staticmethod
    def _looks_irrelevant(user_input: str, output_text: str) -> bool:
        ui = re.findall(r"[A-Za-z0-9\u4e00-\u9fff]{2,}", user_input or "")
        out = (output_text or "").lower()
        if not ui or len(out) < 8:
            return False
        hits = sum(1 for token in ui[:8] if token.lower() in out)
        return hits == 0

    def _l0_rule_route(self, user_input: str) -> tuple[dict | None, str]:
        text = (user_input or "").strip()
        if not text:
            return None, "none"
        if text.startswith("/"):
            parts = text[1:].split(None, 1)
            cmd = parts[0].lower() if parts else ""
            args = parts[1] if len(parts) > 1 else ""
            if cmd in COMMAND_ROUTES:
                target_agent, target_action = COMMAND_ROUTES[cmd]
                params = PARAM_MAP.get(cmd, lambda a: {"args": a})(args)
                return {"agent": target_agent, "action": target_action, "params": params}, "command"
        host_ip = self._extract_host(user_input)
        keyword_routes = [
            (re.compile(r"(证书|ssl|cert).{0,10}(到期|过期|剩余|还有|expire|expir)", re.IGNORECASE), {"agent": "monitor", "action": "check_cert", "params": {"domain": self._extract_domain(user_input)}}),
            (re.compile(r"(服务器|主机|host).{0,6}(状态|健康|cpu|内存|磁盘|负载|memory|disk|load)", re.IGNORECASE), {"agent": "monitor", "action": "host_health", "params": {"host": host_ip}}),
            (re.compile(r"(cpu|内存|磁盘|负载).{0,6}(使用率|占用|用了多少|怎么样|状态)", re.IGNORECASE), {"agent": "monitor", "action": "host_health", "params": {"host": host_ip}}),
            (re.compile(r"\b1[39]\d服务器|服务器1[39]\d\b", re.IGNORECASE), {"agent": "monitor", "action": "host_health", "params": {"host": host_ip}}),
            (re.compile(r"(巡检|patrol|系统.{0,4}(状态|健康|怎么样))", re.IGNORECASE), {"agent": "monitor", "action": "summary", "params": {}}),
            (re.compile(r"(告警|alerts?).{0,6}(历史|记录)", re.IGNORECASE), {"agent": "monitor", "action": "alert_history", "params": {"count": 20}}),
            (re.compile(r"(告警|alerts?|有没有.{0,4}(告警|异常))", re.IGNORECASE), {"agent": "monitor", "action": "check_alerts", "params": {}}),
            (re.compile(r"(监控目标|targets?|采集.{0,4}(目标|状态))", re.IGNORECASE), {"agent": "monitor", "action": "check_targets", "params": {}}),
            (re.compile(r"(grafana|仪表盘|dashboard)", re.IGNORECASE), {"agent": "monitor", "action": "grafana_dash", "params": {}}),
            (re.compile(r"(有哪些|还有哪些|搜索|查找|列出|有什么|还有什么).{0,6}(指标|metric|监控项|监控指标|监控)", re.IGNORECASE), {"agent": "monitor", "action": "metrics", "params": {"keyword": ""}}),
            (re.compile(r"^.{0,4}监控(情况|状况|概况|报告|怎么样).{0,4}$", re.IGNORECASE), {"agent": "monitor", "action": "summary", "params": {}}),
            (re.compile(r"(帮我|请|给我|你来).{0,8}(写|开发|实现|创建|生成|改|加|添加|增加|修改|调整|优化|重写).{0,8}(代码|功能|接口|页面|组件|脚本|文件|模块|方法|函数|类|配置)", re.IGNORECASE), {"agent": "dev", "action": "claude_code", "params": {"prompt": user_input}}),
            (re.compile(r"(改一下|改改|修改|调整|优化).{0,10}(\.py|\.js|\.jsx|\.ts|\.yaml|\.json|\.sh|\.css|\.html)", re.IGNORECASE), {"agent": "dev", "action": "claude_code", "params": {"prompt": user_input}}),
            (re.compile(r"(review|审查|检查).{0,6}(代码|code)", re.IGNORECASE), {"agent": "dev", "action": "code_review", "params": {"path": "", "focus": user_input}}),
            (re.compile(r"(重构|refactor)", re.IGNORECASE), {"agent": "dev", "action": "refactor", "params": {"description": user_input}}),
            (re.compile(r"(修复|fix|修|解决).{0,6}(bug|错误|报错|问题|异常|崩溃)", re.IGNORECASE), {"agent": "dev", "action": "fix_bug", "params": {"description": user_input}}),
            (re.compile(r"(生成|写).{0,4}(测试|test|单元测试)", re.IGNORECASE), {"agent": "dev", "action": "gen_test", "params": {"path": ""}}),
            (re.compile(r"(解释|explain).{0,6}(代码|code|文件|函数)", re.IGNORECASE), {"agent": "dev", "action": "explain", "params": {"path": ""}}),
            (re.compile(r"(部署|deploy).{0,6}(前端|frontend|项目)", re.IGNORECASE), {"agent": "dev", "action": "deploy_frontend", "params": {}}),
            (re.compile(r"(开发|dev).{0,4}(状态|status|健康)", re.IGNORECASE), {"agent": "dev", "action": "status", "params": {}}),
            (re.compile(r"(继续|接着|resume|continue).{0,6}(开发|编码|coding|写代码|上次)", re.IGNORECASE), {"agent": "dev", "action": "claude_continue", "params": {"prompt": user_input}}),
            (re.compile(r"(翻译|translate|translation)", re.IGNORECASE), {"agent": "general", "action": "translate", "params": {"text": user_input}}),
            (re.compile(r"(日历|行程|calendar|日程)", re.IGNORECASE), {"agent": "apple", "action": "calendar_today", "params": {"date": "today"}}),
            (re.compile(r"(提醒|remind|提醒事项)", re.IGNORECASE), {"agent": "apple", "action": "remind_create", "params": {"title": user_input}}),
            (re.compile(r"(备忘|note|备忘录)", re.IGNORECASE), {"agent": "apple", "action": "note_create", "params": {"title": user_input}}),
            (re.compile(r"(音乐|music|播放|暂停|下一首|上一首)", re.IGNORECASE), {"agent": "apple", "action": "music_control", "params": {"action": "status"}}),
            (re.compile(r"(快捷指令|shortcut)", re.IGNORECASE), {"agent": "apple", "action": "shortcut_list", "params": {}}),
            (re.compile(r"(系统信息|sysinfo|电池|磁盘|网络状态)", re.IGNORECASE), {"agent": "apple", "action": "system_info", "params": {"category": "all"}}),
            (re.compile(r"(剪贴板|clipboard|粘贴板)", re.IGNORECASE), {"agent": "apple", "action": "clipboard_read", "params": {}}),
            (re.compile(r"(联系人|通讯录|contact)", re.IGNORECASE), {"agent": "apple", "action": "contact_search", "params": {"name": user_input}}),
            (re.compile(r"(音量|volume|静音|mute)", re.IGNORECASE), {"agent": "apple", "action": "volume_control", "params": {"action": "get"}}),
            (re.compile(r"(闹钟|闹铃|alarm).{0,6}(列表|list|有哪些|查看)", re.IGNORECASE), {"agent": "apple", "action": "alarm_list", "params": {}}),
            (re.compile(r"(设|定|set).{0,4}(闹钟|闹铃|alarm)", re.IGNORECASE), {"agent": "apple", "action": "alarm_set", "params": {"time": ""}}),
            (re.compile(r"(取消|关闭|cancel|delete).{0,4}(闹钟|闹铃|alarm)", re.IGNORECASE), {"agent": "apple", "action": "alarm_cancel", "params": {"id": "all"}}),
            (re.compile(r"(定时器|timer|倒计时|计时)", re.IGNORECASE), {"agent": "apple", "action": "timer_set", "params": {"minutes": ""}}),
            (re.compile(r"^(你好|你是谁|我是谁|帮助|help|who are you|who am i|嗨|hi|hello|早上好|晚上好|早安|晚安|谢谢|在吗|在不在).{0,20}$", re.IGNORECASE), {"agent": "_chat", "action": "reply", "params": {"message": user_input}}),
            # 搜索与研究 (新增)
            (re.compile(r"^(搜索|搜一下|查一下|search|google|查找|网上搜).{0,30}$", re.IGNORECASE), {"agent": "news", "action": "web_search", "params": {"query": user_input}}),
            (re.compile(r"(深度|深入|详细|全面).{0,6}(研究|分析|调研|报告|research)", re.IGNORECASE), {"agent": "news", "action": "deep_research", "params": {"topic": user_input}}),
            (re.compile(r"^(帮我|请|给我).{0,4}(研究|调研|research).{0,20}$", re.IGNORECASE), {"agent": "news", "action": "deep_research", "params": {"topic": user_input}}),
            # 安全审计 (新增)
            (re.compile(r"(安全|security).{0,6}(审计|审查|检查|扫描|audit|scan)", re.IGNORECASE), {"agent": "orchestrator", "action": "security_audit", "params": {}}),
        ]
        for pattern, route in keyword_routes:
            if pattern.search(text):
                return route, "keyword"
        return None, "none"

    def _assess_complexity(self, user_input: str) -> dict:
        """
        自适应推理评估 (adaptive-reasoning 集成)
        根据任务特征快速判断复杂度，指导分诊和模型选择。
        """
        text = user_input.lower()
        score = 0
        signals = []

        # 多步骤信号
        multi_step_patterns = ["然后", "接着", "之后", "首先", "最后", "分析.*并.*",
                               "对比", "比较", "综合", "and then", "step by step"]
        for p in multi_step_patterns:
            if p in text:
                score += 2
                signals.append("multi_step")
                break

        # 量化/技术深度信号
        quant_patterns = ["回测", "因子", "策略", "alpha", "sharpe", "backtest",
                          "优化", "回归", "统计", "模型", "组合"]
        quant_hits = sum(1 for p in quant_patterns if p in text)
        if quant_hits >= 2:
            score += 3
            signals.append("quant_depth")
        elif quant_hits == 1:
            score += 1

        # 多 Agent 信号
        agent_keywords = {"新闻": "news", "行情": "market", "代码": "dev",
                          "浏览": "browser", "监控": "monitor", "策略": "strategist"}
        agents_mentioned = [v for k, v in agent_keywords.items() if k in text]
        if len(agents_mentioned) >= 2:
            score += 2
            signals.append("multi_agent")

        # 长度信号
        if len(user_input) > 200:
            score += 1
            signals.append("long_input")

        # 研究/深度信号
        if any(w in text for w in ["深度", "研究", "详细", "全面", "deep", "research"]):
            score += 2
            signals.append("deep_analysis")

        level = "complex" if score >= 4 else "simple"
        return {
            "score": score,
            "level": level,
            "signals": signals,
            "reasoning_depth": "extended" if score >= 6 else ("standard" if score >= 3 else "light"),
        }

    async def _l1_triage(self, user_input: str, uid: str) -> dict:
        from agents.llm_router import get_llm_router
        router = get_llm_router()
        agent_summary = self._get_agent_summary()

        # 自适应复杂度评估 (adaptive-reasoning 集成)
        complexity = self._assess_complexity(user_input)
        complexity_hint = ""
        if complexity["signals"]:
            complexity_hint = (
                f"\n[复杂度预评估: {complexity['level']} "
                f"(score={complexity['score']}, signals={','.join(complexity['signals'])})"
                f" → 推荐推理深度: {complexity['reasoning_depth']}]\n"
            )

        # 注入历史路由成功率提示（来自反思引擎）
        routing_hints = ""
        try:
            engine = self._get_reflection_engine()
            if engine:
                routing_hints = engine.get_routing_hints(top_k=6)
        except Exception:
            pass

        # 获取对话历史帮助分诊
        history = await self._session_history_get(uid, max_rounds=3)
        history_text = ""
        if history and len(history) > 1:
            h_lines = []
            for h in history[-4:]:
                role_label = "user" if h["role"] == "user" else "assistant"
                h_lines.append(f"{role_label}: {h['content'][:200]}")
            history_text = f"\n最近对话:\n" + "\n".join(h_lines) + "\n"

        prompt = (
            "你是请求分诊器。仅输出 JSON，不要解释。\n"
            f"可用Agent:\n{agent_summary}\n\n"
        )
        if complexity_hint:
            prompt += complexity_hint + "\n"
        if routing_hints:
            prompt += f"{routing_hints}\n\n"
        if history_text:
            prompt += history_text + "\n"
        prompt += (
            "输出格式:\n"
            '{"level":"simple|complex","agent":"name","action":"name","params":{}}\n'
            "规则: simple=单Agent可解决；complex=需要多步骤/多Agent协作。\n"
            "优先选择历史成功率高的 agent.action 组合。\n"
            "参考复杂度预评估结果做出判断。\n"
            "注意: 如果用户的问题是对之前对话的追问或继续，应路由到同类型Agent。\n"
            "日常问答、知识问题路由到 general.ask。闲聊问候路由到 _chat。"
        )
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_input},
        ]
        result = await router.chat(messages, task_type="triage", temperature=0.0)
        if not result:
            return {"level": "simple", "agent": "general", "action": "ask", "params": {"question": user_input}}
        try:
            payload = json.loads(self._strip_json_fence(result))
            if not isinstance(payload, dict):
                raise ValueError("bad")
            payload.setdefault("level", "simple")
            payload.setdefault("agent", "general")
            payload.setdefault("action", "ask")
            if not isinstance(payload.get("params"), dict):
                payload["params"] = {}
            if payload["agent"] == "general" and payload["action"] == "ask":
                payload["params"].setdefault("question", user_input)
            return payload
        except Exception:
            return {"level": "simple", "agent": "general", "action": "ask", "params": {"question": user_input}}

    async def _l2_deep_plan(self, user_input: str, uid: str) -> list[dict]:
        from agents.llm_router import get_llm_router
        router = get_llm_router()
        history = await self._session_history_get(uid, max_rounds=5)
        memory_context = await self._build_memory_context(uid, user_input)
        manifest = self._load_capability_manifest()
        time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        history_text = "\n".join(f"{m['role']}: {m['content']}" for m in history[-8:])
        prompt = (
            "你是 RRClaw 多Agent系统的任务规划器。仅输出 JSON 数组。\n"
            f"当前时间: {time_str}\n"
            f"{memory_context}"
            f"会话历史:\n{history_text}\n\n"
            f"可用能力:\n{manifest}\n\n"
            "将用户请求拆解为顺序步骤。每步结构:\n"
            '{"step":1,"desc":"...","agent":"...","action":"...","params":{}}\n'
            "如果可单步完成，也输出一个元素数组。"
        )
        result = await router.chat(
            [{"role": "system", "content": prompt}, {"role": "user", "content": user_input}],
            task_type="reasoning", temperature=0.2,
        )
        if not result:
            return [{"step": 1, "desc": "通用问答兜底", "agent": "general", "action": "ask", "params": {"question": user_input}}]
        try:
            plan = json.loads(self._strip_json_fence(result))
            if isinstance(plan, dict):
                plan = [plan]
            normalized = []
            for index, item in enumerate(plan, start=1):
                if not isinstance(item, dict):
                    continue
                normalized.append({
                    "step": item.get("step", index),
                    "desc": item.get("desc", f"步骤{index}"),
                    "agent": item.get("agent", "general"),
                    "action": item.get("action", "ask"),
                    "params": item.get("params") if isinstance(item.get("params"), dict) else {},
                })
            return normalized or [{"step": 1, "desc": "通用问答兜底", "agent": "general", "action": "ask", "params": {"question": user_input}}]
        except Exception:
            return [{"step": 1, "desc": "通用问答兜底", "agent": "general", "action": "ask", "params": {"question": user_input}}]

    @staticmethod
    def _build_parallel_batches(plan: list[dict]) -> list[list[int]]:
        """将计划步骤分成并行批次。同一批次内步骤互不依赖可并行，批次间串行。
        规则: 一个步骤若显式标记 depends_on 则必须在依赖完成后执行；
              同 agent 的连续步骤串行（避免竞态）；
              否则尽量并行。"""
        n = len(plan)
        if n <= 1:
            return [[i] for i in range(n)]

        batches: list[list[int]] = []
        assigned = set()

        while len(assigned) < n:
            batch = []
            agents_in_batch = set()
            for i in range(n):
                if i in assigned:
                    continue
                step = plan[i]
                deps = step.get("depends_on", [])
                if isinstance(deps, int):
                    deps = [deps]
                if deps and not all(d - 1 in assigned for d in deps):
                    continue
                agent = step.get("agent", "")
                if agent in agents_in_batch:
                    continue
                batch.append(i)
                agents_in_batch.add(agent)
            if not batch:
                remaining = [i for i in range(n) if i not in assigned]
                batch = [remaining[0]]
            for i in batch:
                assigned.add(i)
            batches.append(batch)
        return batches

    async def _execute_plan(self, plan: list[dict], uid: str, reply_channel: str, msg_id: str, user_name: str = "") -> list[dict]:
        accumulated_context = ""
        step_results = [None] * len(plan)
        total = len(plan)
        batches = self._build_parallel_batches(plan)

        async def _run_step(index: int, ctx_snapshot: str):
            step = plan[index]
            step_params = step.get("params") if isinstance(step.get("params"), dict) else {}
            step_params["prior_context"] = ctx_snapshot
            base_context = step_params.get("context", "")
            step_params["context"] = (base_context + "\n" + ctx_snapshot).strip()
            step["params"] = step_params
            start_ms = int(time.time() * 1000)
            text = await self._execute_intent_step(step, uid=uid, user_name=user_name,
                                                    reply_channel=reply_channel, msg_id=msg_id)
            end_ms = int(time.time() * 1000)
            ok = "❌" not in text
            return {"index": index + 1, "step": step, "result": text, "ok": ok, "latency_ms": end_ms - start_ms}

        for batch in batches:
            descs = [plan[i].get("desc", f"步骤{i+1}") for i in batch]
            if len(batch) == 1:
                idx = batch[0]
                await self._progress_to_channel(
                    reply_channel, msg_id,
                    f"🔄 执行步骤 {idx+1}/{total}: {descs[0]}...",
                    source=plan[idx].get("agent", "manager"))
                result = await _run_step(idx, accumulated_context)
                step_results[idx] = result
            else:
                agents_str = " + ".join(f"{plan[i].get('agent','?')}" for i in batch)
                step_nums = "+".join(str(i+1) for i in batch)
                await self._progress_to_channel(
                    reply_channel, msg_id,
                    f"⚡ 并行执行步骤 [{step_nums}]/{total}: {', '.join(descs)} ({agents_str})")
                ctx_snap = accumulated_context
                tasks = [_run_step(i, ctx_snap) for i in batch]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for i, r in zip(batch, results):
                    if isinstance(r, Exception):
                        step_results[i] = {"index": i + 1, "step": plan[i], "result": f"❌ 并行执行异常: {r}", "ok": False, "latency_ms": 0}
                    else:
                        step_results[i] = r

            for i in batch:
                r = step_results[i]
                if r:
                    snippet = r["result"][:500] if isinstance(r["result"], str) else str(r["result"])[:500]
                    accumulated_context += f"\n[{plan[i].get('desc', f'步骤{i+1}')}]: {snippet}"

        return [r for r in step_results if r is not None]

    async def _reflect_and_synthesize(self, user_input: str, route_level: str, step_results: list[dict]) -> tuple[str, dict]:
        reflection = {"triggered": False, "retry_attempted": False, "retry_result": "", "reason": ""}
        if not step_results:
            return "❌ 未生成可执行结果", reflection
        failures = [s for s in step_results if not s.get("ok", False)]
        if len(step_results) == 1 and not failures:
            return step_results[0]["result"], reflection
        if len(step_results) == 1 and failures:
            failed_agent = step_results[0].get("step", {}).get("agent", "")
            if failed_agent not in ("general", "_chat"):
                reflection["triggered"] = True
                reflection["reason"] = "specialized_agent_failed"
                return step_results[0]["result"], reflection
            reflection["triggered"] = True
            reflection["reason"] = "single_step_failed"
            try:
                fallback_text = await self._handle_casual_chat(user_input, uid="", user_name="")
                reflection["retry_attempted"] = True
                reflection["retry_result"] = fallback_text
                return fallback_text, reflection
            except Exception:
                return step_results[0]["result"], reflection
        reflection["triggered"] = True
        compiled = "\n\n".join(
            f"步骤{idx+1} ({sr['step'].get('agent','?')}.{sr['step'].get('action','?')}):\n{sr['result']}"
            for idx, sr in enumerate(step_results)
        )
        reflection["reason"] = "has_failures" if failures else "multi_step_synthesis"
        try:
            from agents.llm_router import get_llm_router
            router = get_llm_router()
            maybe = await router.chat(
                [{"role": "system", "content": "请把多步骤执行结果整合成一段连贯中文回复。先给结论再给依据。若有失败步骤标记影响。不要虚构。"},
                 {"role": "user", "content": compiled}],
                task_type="brief", temperature=0.2,
            )
            return (maybe or compiled), reflection
        except Exception:
            return compiled, reflection

    async def _persist_plan_record(self, msg_id: str, plan_record: dict):
        try:
            r = await self.get_redis()
            key = f"{PLAN_LOG_PREFIX}{msg_id}"
            await r.set(key, json.dumps(plan_record, ensure_ascii=False, default=str), ex=PLAN_LOG_TTL_SECONDS)
            await r.lpush(PLAN_HISTORY_KEY, msg_id)
            await r.ltrim(PLAN_HISTORY_KEY, 0, 199)
            await r.expire(PLAN_HISTORY_KEY, PLAN_LOG_TTL_SECONDS)
        except Exception as e:
            logger.debug("persist plan record failed: %s", e)


    async def _persist_to_memory(self, uid: str, user_input: str, reply_text: str):
        """将本轮对话持久化到 embedding 向量库，供长期记忆检索"""
        try:
            from agents.memory.embedding import EmbeddingClient
            from agents.memory.vector_store import VectorStore
            from datetime import date as _date
            import uuid as _uuid
            embedder = EmbeddingClient()
            content = f"Q: {user_input[:500]}\nA: {reply_text[:1000]}"
            vec = await embedder.embed(content)
            if vec is None:
                return
            store = VectorStore("orchestrator_memory")
            mem_id = f"orch_{int(time.time())}_{_uuid.uuid4().hex[:6]}"
            store.add(mem_id, vec, content, {
                "date": _date.today().isoformat(),
                "uid": uid,
                "type": "chat_turn",
            })
        except Exception as e:
            logger.debug(f"Memory persist failed (non-fatal): {e}")

    async def _intent_route(self, user_input: str, uid: str, reply_channel: str, msg_id: str, user_name: str = ""):
        """Viking 分层路由: L0(规则) -> L1(轻量分诊) -> L2(深度规划) -> 反思综合。"""
        r = await self.get_redis()
        canonical_uid = await resolve_canonical_uid(r, uid)
        if canonical_uid != uid:
            uid = canonical_uid
        if not user_name and uid:
            try:
                raw = await r.hget("rragent:user_profiles", uid)
                if raw:
                    profile = json.loads(raw)
                    user_name = profile.get("name", "")
            except Exception:
                pass
        await self._session_history_append(uid, "user", user_input)

        # 重复查询检测（用户不满足信号）
        _repeated = False
        try:
            engine = self._get_reflection_engine()
            if engine:
                recent_inputs = engine.get_recent_query_inputs(uid, limit=8)
                _repeated = engine.detect_repeated_query(user_input, recent_inputs)
                if _repeated:
                    logger.info("Repeated query detected for uid=%s: %s", uid, user_input[:60])
        except Exception:
            pass

        route_started_at = int(time.time() * 1000)
        # 预评估复杂度 (adaptive-reasoning)
        _complexity = self._assess_complexity(user_input)
        plan_record = {
            "id": msg_id, "uid": uid, "input": user_input,
            "route_level": "", "l0_hit": "", "l1_triage": None,
            "l2_plan": [], "steps": [], "reflection": {},
            "final_output": "", "tokens_used": {"l0": 0, "l1": 150, "l2": 0, "reflect": 0},
            "latency_ms": {}, "ts": time.time(),
            "repeated_query": _repeated,
            "complexity": _complexity,
        }
        final_text = ""
        try:
            l0_intent, l0_hit = self._l0_rule_route(user_input)
            if l0_intent:
                plan_record["route_level"] = "L0"
                plan_record["l0_hit"] = l0_hit
                agent_name = l0_intent.get("agent", "manager")
                await self._progress_to_channel(reply_channel, msg_id,
                                                f"🔄 匹配到快捷路由，正在执行...", source=agent_name)
                text = await self._execute_intent_step(l0_intent, uid=uid, user_name=user_name,
                                                        reply_channel=reply_channel, msg_id=msg_id)
                plan_record["steps"] = [{"step": l0_intent, "result": text, "ok": "\u274c" not in text}]
                final_text, reflection = await self._reflect_and_synthesize(user_input, "L0", plan_record["steps"])
                plan_record["reflection"] = reflection
                plan_record["final_output"] = final_text
                plan_record["latency_ms"]["total"] = int(time.time() * 1000) - route_started_at
                await self._persist_plan_record(msg_id, plan_record)
                source = "manager" if l0_intent.get("agent") == "_chat" else l0_intent.get("agent", "manager")
                await self._reply_to_channel(reply_channel, msg_id, final_text, source=source)
            else:
                await self._progress_to_channel(reply_channel, msg_id, "🧠 正在进行意图分析...")
                l1_started_at = int(time.time() * 1000)
                triage = await self._l1_triage(user_input, uid)
                plan_record["l1_triage"] = triage
                plan_record["latency_ms"]["l1"] = int(time.time() * 1000) - l1_started_at
                if triage.get("level", "simple") == "complex":
                    plan_record["route_level"] = "L2"
                    await self._progress_to_channel(reply_channel, msg_id, "📋 任务较复杂，正在制定执行计划...")
                    l2_started_at = int(time.time() * 1000)
                    plan = await self._l2_deep_plan(user_input, uid)
                    plan_record["l2_plan"] = plan
                    plan_record["latency_ms"]["l2_plan"] = int(time.time() * 1000) - l2_started_at
                    step_results = await self._execute_plan(plan, uid=uid, reply_channel=reply_channel, msg_id=msg_id, user_name=user_name)
                else:
                    plan_record["route_level"] = "L1"
                    single = {
                        "step": 1, "desc": "L1 单步执行",
                        "agent": triage.get("agent", "general"),
                        "action": triage.get("action", "ask"),
                        "params": triage.get("params") if isinstance(triage.get("params"), dict) else {},
                    }
                    if single["agent"] == "general" and single["action"] == "ask":
                        single["params"].setdefault("question", user_input)
                    result_text = await self._execute_intent_step(single, uid=uid, user_name=user_name,
                                                                  reply_channel=reply_channel, msg_id=msg_id)
                    step_results = [{"index": 1, "step": single, "result": result_text, "ok": "\u274c" not in result_text, "latency_ms": 0}]
                plan_record["steps"] = step_results
                final_text, reflection = await self._reflect_and_synthesize(user_input, plan_record["route_level"], step_results)
                plan_record["reflection"] = reflection
                plan_record["final_output"] = final_text
                plan_record["latency_ms"]["total"] = int(time.time() * 1000) - route_started_at
                await self._persist_plan_record(msg_id, plan_record)
                first_agent = step_results[0]["step"].get("agent", "manager") if step_results else "manager"
                source = "manager" if first_agent == "_chat" else first_agent
                await self._reply_to_channel(reply_channel, msg_id, final_text, source=source)
        except Exception as e:
            logger.exception("intent route failed")
            final_text = f"\u274c 路由执行失败: {e}"
            plan_record["route_level"] = plan_record.get("route_level") or "L1"
            plan_record["final_output"] = final_text
            plan_record["latency_ms"]["total"] = int(time.time() * 1000) - route_started_at
            await self._persist_plan_record(msg_id, plan_record)
            await self._reply_to_channel(reply_channel, msg_id, final_text)
        if final_text and uid:
            await self._session_history_append(uid, "assistant", final_text[:2000])
            try:
                await self._persist_to_memory(uid, user_input, final_text)
            except Exception:
                pass
            # 将 plan 结果持久化到反思引擎（不受 Redis 24h TTL 限制）
            try:
                engine = self._get_reflection_engine()
                if engine:
                    engine.record_plan_outcome(plan_record)
            except Exception as e:
                logger.debug("reflection engine record failed (non-fatal): %s", e)

    async def _execute_intent_step(self, intent: dict, uid: str = "", user_name: str = "",
                                    reply_channel: str = "", msg_id: str = "") -> str:
        """执行单个意图识别步骤，注入对话上下文到子 Agent"""
        agent = intent.get("agent", "")
        action = intent.get("action", "")
        params = intent.get("params", {})

        if not agent or not action:
            return "❌ 意图解析无效"

        GENERAL_ALIASES = {
            "answer": "ask", "reply": "ask", "respond": "ask", "query": "ask",
        }
        MARKET_ALIASES = {
            "get_market_overview": "get_summary", "market_overview": "get_summary",
        }
        if agent == "general":
            action = GENERAL_ALIASES.get(action, action)
        elif agent == "market":
            action = MARKET_ALIASES.get(action, action)
        intent["action"] = action

        if agent == "general" and action == "ask":
            q = params.get("question") or params.get("query") or params.get("text") or params.get("message", "")
            if q:
                params["question"] = q

        if agent == "_chat":
            return await self._handle_casual_chat(params.get("message", ""), uid, user_name=user_name)

        if uid and not params.get("context"):
            ctx_parts = []
            history = await self._session_history_get(uid, max_rounds=6)
            if len(history) > 1:
                ctx_lines = []
                for h in history[:-1]:
                    role_label = "用户" if h["role"] == "user" else "助手"
                    ctx_lines.append(f"{role_label}: {h['content'][:600]}")
                ctx_parts.append("\n".join(ctx_lines[-8:]))
            # 注入长期记忆
            question = params.get("question") or params.get("query") or params.get("text", "")
            if question and len(question) > 4:
                memory_ctx = await self._build_memory_context(uid, question)
                if memory_ctx:
                    ctx_parts.append(memory_ctx)
            if ctx_parts:
                params["context"] = "\n".join(ctx_parts).strip()

        try:
            timeout = ACTION_TIMEOUTS.get(action, 0)
            if reply_channel:
                resp = await self._send_with_progress(
                    agent, action, params, timeout=timeout,
                    reply_channel=reply_channel, msg_id=msg_id,
                )
            else:
                resp = await self.send(agent, action, params, timeout=timeout)
            if resp.error:
                return f"❌ [{agent}] {resp.error}"
            return self._result_to_text(resp.result, source=agent, action=action)
        except Exception as e:
            return f"❌ [{agent}] 执行失败: {e}"

    async def _handle_casual_chat(self, user_input: str, uid: str = "", user_name: str = "") -> str:
        """轻量闲聊: 简单问题直接用 LLM 回复，不触发 Agent 分析"""
        import re as _re
        if user_name and _re.match(r"^(我是谁|who am i).{0,6}$", user_input, _re.IGNORECASE):
            from datetime import datetime as _dt, timezone as _tz, timedelta as _td
            now_bj = _dt.now(_tz(_td(hours=8)))
            return f"你是 **{user_name}**，RRClaw 系统的已认证用户。当前时间: {now_bj.strftime('%H:%M')}。有什么我可以帮你的？"

        try:
            from agents.llm_router import get_llm_router
            router = get_llm_router()

            history = await self._session_history_get(uid, max_rounds=4) if uid else []

            import re as _re2
            needs_memory = uid and len(user_input) > 6 and not _re2.match(
                r"^(你好|hi|hello|嗨|hey|早|晚安|谢谢|帮助|help).{0,4}$", user_input, _re2.IGNORECASE
            )
            memory_context = await self._build_memory_context(uid, user_input) if needs_memory else ""

            from datetime import datetime as _dt, timezone as _tz, timedelta as _td
            now_bj = _dt.now(_tz(_td(hours=8)))
            time_str = now_bj.strftime("%Y-%m-%d %H:%M:%S (北京时间)")

            sys_content = (
                f"你是 RRClaw，智能多Agent协作系统的核心AI助手。当前时间: {time_str}\n"
            )
            if user_name:
                sys_content += f"当前用户: {user_name}。\n"
            sys_content += (
                "你具备三层记忆系统（向量语义检索+知识图谱+时序），能记住与用户的历史对话。\n"
                "请认真回答用户问题，给出有价值的、完整的回复。\n"
                "务必结合对话历史和记忆上下文连贯回答。绝对不要说'我看不到之前的对话'或'我没有记忆'之类的话。\n"
                "如果对话历史或记忆中有相关信息，直接引用并自然衔接。使用北京时间。\n"
            )
            if memory_context:
                sys_content += memory_context + "\n"

            wants_help = _re2.search(r"(功能|帮助|help|能做什么|什么命令|怎么用)", user_input, _re2.IGNORECASE)
            if wants_help:
                sys_content += (
                    "你的能力: 📈量化投研(/zt /lb /bk /ask /quant) 🧠通用(/q /translate /write /calc /websearch) "
                    "🍎Apple(/calendar /remind /note) 🌐浏览器(/browse /url) 🖥️桌面(/do /screen /shell) "
                    "💻开发(/claude /dev /cr /refactor /fix /test /explain /deploy /ssh /local) "
                    "🔄反思(/reflect /reflect_weekly /reflect_stats) 📋自省(/skills /agents /factor_list /factor_detail)\n"
                )

            messages = [{"role": "system", "content": sys_content}]
            if history and len(history) > 1:
                for h in history[:-1]:
                    messages.append({"role": h["role"], "content": h["content"][:800]})
            messages.append({"role": "user", "content": user_input})

            reply = await router.chat(messages, task_type="default", temperature=0.7)
            return reply or "你好，我是 RRClaw 🦀 有什么可以帮你的？"
        except Exception:
            return (
                "你好，我是 RRClaw 🦀 智能多Agent协作系统。\n\n"
                "📈 /zt /lb /bk /ask — 量化投研\n"
                "🧠 /q — 通用问答\n"
                "🍎 /calendar /remind /note — Apple生态\n"
                "输入任意问题，我会自动识别并路由到对应能力。"
            )

    async def _get_memory_health(self) -> dict:
        """全局图谱拓扑健康 + 降级日志"""
        health = {"graph": {}, "degradation_recent": []}
        try:
            from agents.memory.knowledge_graph import get_shared_graph
            graph = get_shared_graph()
            health["graph"] = graph.health_report()
        except Exception as e:
            health["graph"] = {"error": str(e)}

        try:
            r = await self.get_redis()
            logs = await r.lrange(DEGRADATION_LOG_KEY, 0, 9)
            health["degradation_recent"] = [json.loads(l) for l in logs]
        except Exception:
            pass

        return health

    async def _schedule_loop(self):
        while self._running:
            now = datetime.now()
            for rule in self.rules:
                trigger = rule.get("trigger", {})
                schedule_str = trigger.get("schedule")
                if not schedule_str:
                    continue
                schedule = parse_schedule(schedule_str)
                if not schedule:
                    continue
                if match_schedule(schedule, now):
                    logger.info(f"Rule triggered: {rule.get('name')}")
                    asyncio.create_task(self._execute_rule(rule))
            await asyncio.sleep(60)

    async def _event_monitor_loop(self):
        while self._running:
            try:
                from agents.market_agent import api_get
                zt = api_get("limitup", {"page_size": 1})
                if zt:
                    count = zt.get("count", 0)
                    if self._last_limitup_count is not None:
                        for rule in self.rules:
                            trigger = rule.get("trigger", {})
                            if trigger.get("event") == "limitup_count_change":
                                condition = trigger.get("condition", "")
                                if "count > " in condition:
                                    threshold = int(condition.split(">")[1].strip())
                                    if count > threshold and self._last_limitup_count <= threshold:
                                        logger.info(f"Event rule triggered: {rule.get('name')} (count={count})")
                                        asyncio.create_task(self._execute_rule(rule))
                    self._last_limitup_count = count
            except Exception as e:
                logger.error(f"Event monitor error: {e}")
            await asyncio.sleep(60)

    async def _memory_health_loop(self):
        """每 5 分钟检查记忆系统健康 + 降级告警"""
        while self._running:
            try:
                health = await self._get_memory_health()
                graph_health = health.get("graph", {})
                degradations = health.get("degradation_recent", [])

                if degradations:
                    recent_count = sum(1 for d in degradations if time.time() - d.get("ts", 0) < 300)
                    if recent_count >= 3:
                        await self._notify(
                            f"⚠️ 记忆系统降级告警: 5分钟内 {recent_count} 次降级\n"
                            f"图谱: {graph_health.get('total_nodes', 0)} 节点, "
                            f"{graph_health.get('orphan_nodes', 0)} 孤立节点",
                            topic="system", priority="high",
                        )

                orphans = graph_health.get("orphan_nodes", 0)
                total = graph_health.get("total_nodes", 0)
                if total > 10 and orphans / total > 0.2:
                    logger.warning(f"Graph health: {orphans}/{total} orphan nodes ({orphans/total:.0%})")

            except Exception as e:
                logger.debug(f"Memory health check error: {e}")
            await asyncio.sleep(300)

    def _rule_topic(self, rule: dict) -> str:
        """根据规则内容推断通知话题"""
        name = rule.get("name", "")
        topic = rule.get("topic", "")
        if topic:
            return topic
        if any(k in name for k in ("记忆", "SOUL", "嵌入", "卫生")):
            return "system"
        if any(k in name for k in ("策略", "复盘", "研判")):
            return "strategy"
        return "market"

    async def _execute_rule(self, rule: dict):
        actions = rule.get("actions", [])
        results = []
        topic = self._rule_topic(rule)
        for act in actions:
            agent = act.get("agent", "")
            action = act.get("action", "")
            params = act.get("params", {})
            if agent == "orchestrator":
                if action == "notify":
                    text = "\n".join(str(r) for r in results)
                    priority = params.get("priority", "normal")
                    await self._notify(
                        f"📋 {rule.get('name')}:\n{text}",
                        topic=topic, priority=priority,
                    )
            else:
                resp = await self.send(agent, action, params)
                if resp.error:
                    results.append(f"[{agent}] Error: {resp.error}")
                else:
                    results.append(self._result_to_text(resp.result, source=agent_name, action=action_name))

    async def _memory_remind_loop(self):
        """跨 Agent 冗余记忆互相提醒循环"""
        from agents.memory.config import REMINDER_CONFIG
        if not REMINDER_CONFIG.get("enabled"):
            return
        interval = REMINDER_CONFIG.get("interval_seconds", 600)
        await asyncio.sleep(30)
        while self._running:
            try:
                from agents.memory.reminder import MemoryReminder
                reminder = MemoryReminder()
                result = await reminder.scan_and_remind()
                if result.get("reminded", 0) > 0:
                    logger.info(f"Memory remind: {result.get('reminded')} reminders pushed")
            except Exception as e:
                logger.debug(f"Memory remind loop error: {e}")
            await asyncio.sleep(interval)

    async def _soul_guardian_loop(self):
        """SOUL 身份守护循环: 每小时检查文件完整性"""
        await asyncio.sleep(10)
        try:
            from agents.memory.soul_guardian import SoulGuardian
            guardian = SoulGuardian()
            guardian.compute_baseline()
            await guardian.save_baseline()
            logger.info("Soul Guardian: baseline initialized")
        except Exception as e:
            logger.warning(f"Soul Guardian init failed: {e}")
            return

        while self._running:
            try:
                guardian = SoulGuardian()
                result = await guardian.check_integrity()
                if result.get("status") == "tampered":
                    changes = result.get("changes", [])
                    logger.warning(f"Soul Guardian: {len(changes)} identity changes detected")
            except Exception as e:
                logger.debug(f"Soul Guardian check error: {e}")
            await asyncio.sleep(3600)

    async def _generate_daily_briefing(self) -> dict:
        """
        主动综合简报: 并行采集市场 + 新闻 + 记忆健康，聚合后用 LLM 生成摘要。
        灵感来源: awesome-openclaw-skills 中的 daily-briefing / proactive-agent
        """
        results = {}

        market_task = self.send("market", "get_summary")
        news_task = self.send("news", "get_news", {"keyword": ""})
        market_resp, news_resp = await asyncio.gather(market_task, news_task)

        if not market_resp.error:
            results["market"] = market_resp.result
        if not news_resp.error:
            results["news"] = news_resp.result

        try:
            health = await self._get_memory_health()
            results["memory_health"] = {
                "nodes": health.get("graph", {}).get("total_nodes", 0),
                "orphans": health.get("graph", {}).get("orphan_nodes", 0),
                "degradations": len(health.get("degradation_recent", [])),
            }
        except Exception:
            pass

        try:
            from agents.memory.soul_guardian import SoulGuardian
            guardian = SoulGuardian()
            results["soul_status"] = (await guardian.check_integrity()).get("status", "unknown")
        except Exception:
            results["soul_status"] = "unchecked"

        try:
            from agents.llm_router import get_llm_router
            results["llm_status"] = get_llm_router().get_status()
        except Exception:
            pass

        try:
            from agents.data_sources.source_router import get_router
            results["data_source"] = get_router().get_status()
        except Exception:
            pass

        market_text = results.get("market", {}).get("text", "暂无") if isinstance(results.get("market"), dict) else str(results.get("market", "暂无"))
        news_text = results.get("news", {}).get("text", "暂无") if isinstance(results.get("news"), dict) else str(results.get("news", "暂无"))

        briefing = (
            f"📋 每日综合简报 ({datetime.now().strftime('%Y-%m-%d %H:%M')})\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 市场: {market_text[:300]}\n"
            f"📰 新闻: {news_text[:300]}\n"
            f"🧠 记忆: {results.get('memory_health', {}).get('nodes', '?')}节点, "
            f"孤立{results.get('memory_health', {}).get('orphans', '?')}\n"
            f"🛡️ 身份: {results.get('soul_status', 'N/A')}\n"
            f"🤖 LLM: local={results.get('llm_status', {}).get('stats', {}).get('local_calls', 0)}, "
            f"cloud={results.get('llm_status', {}).get('stats', {}).get('cloud_calls', 0)}\n"
            f"📡 数据源: {results.get('data_source', {}).get('active_source', 'primary')}"
        )

        await self._notify(briefing, topic="market", priority="high")

        return {"briefing": briefing, "details": results}

    async def _handle_task_create(self, msg: AgentMessage):
        """创建并执行长期任务"""
        from agents.task_manager import PRESET_TASKS
        params = msg.params
        preset = params.get("preset", "")
        name = params.get("name", "")
        steps = params.get("steps", [])
        reply_channel = params.get("reply_channel", "")

        try:
            mgr = await self._get_task_manager()

            if preset and preset in PRESET_TASKS:
                tpl = PRESET_TASKS[preset]
                task = await mgr.create_task(tpl["name"], tpl["steps"], created_by=msg.sender)
            elif name and steps:
                task = await mgr.create_task(name, steps, created_by=msg.sender)
            else:
                presets = ", ".join(PRESET_TASKS.keys())
                text = f"请指定 preset 或自定义 name+steps。\n可用预设: {presets}"
                await self._reply_to_channel(reply_channel, msg.id, text)
                await self.reply(msg, result={"text": text})
                return

            asyncio.create_task(
                mgr.run_task(
                    task.id,
                    send_fn=self.send,
                    notify_fn=self._notify,
                ),
                name=f"task_{task.id}",
            )

            text = f"✅ 任务已创建并开始执行\nID: {task.id}\n名称: {task.name}\n步骤: {len(task.steps)}"
            await self._reply_to_channel(reply_channel, msg.id, text)
            await self.reply(msg, result={"text": text, "task_id": task.id})
        except Exception as e:
            err_text = f"❌ 任务创建失败: {e}"
            await self._reply_to_channel(reply_channel, msg.id, err_text)
            await self.reply(msg, error=str(e))

    async def _handle_task_status(self, msg: AgentMessage):
        """查询任务进度"""
        task_id = msg.params.get("task_id", "")
        reply_channel = msg.params.get("reply_channel", "")
        try:
            mgr = await self._get_task_manager()
            if task_id:
                task = await mgr.get_task(task_id)
                if task:
                    text = task.format_status()
                    await self._reply_to_channel(reply_channel, msg.id, text)
                    await self.reply(msg, result={"text": text})
                else:
                    text = f"任务 {task_id} 不存在"
                    await self._reply_to_channel(reply_channel, msg.id, text)
                    await self.reply(msg, error=text)
            else:
                tasks = await mgr.list_tasks(limit=10)
                if tasks:
                    lines = ["📋 最近任务:"]
                    for t in tasks:
                        icon = {"completed": "✅", "running": "🔄", "failed": "❌", "pending": "⏳", "cancelled": "🚫", "paused": "⏸️"}.get(t.status.value, "❓")
                        lines.append(f"  {icon} {t.id}: {t.name} [{t.progress}%]")
                    text = "\n".join(lines)
                else:
                    text = "暂无任务记录"
                await self._reply_to_channel(reply_channel, msg.id, text)
                await self.reply(msg, result={"text": text})
        except Exception as e:
            err_text = f"❌ 任务查询失败: {e}"
            await self._reply_to_channel(reply_channel, msg.id, err_text)
            await self.reply(msg, error=str(e))

    async def _handle_task_list(self, msg: AgentMessage):
        """列出所有任务"""
        await self._handle_task_status(msg)

    async def _handle_task_cancel(self, msg: AgentMessage):
        """取消任务"""
        task_id = msg.params.get("task_id", "")
        reply_channel = msg.params.get("reply_channel", "")
        if not task_id:
            text = "请指定 task_id"
            await self._reply_to_channel(reply_channel, msg.id, text)
            await self.reply(msg, error=text)
            return
        try:
            mgr = await self._get_task_manager()
            ok = await mgr.cancel_task(task_id)
            if ok:
                text = f"✅ 任务 {task_id} 已取消"
            else:
                text = f"无法取消任务 {task_id}（可能已完成或不存在）"
            await self._reply_to_channel(reply_channel, msg.id, text)
            await self.reply(msg, result={"text": text})
        except Exception as e:
            err_text = f"❌ 任务取消失败: {e}"
            await self._reply_to_channel(reply_channel, msg.id, err_text)
            await self.reply(msg, error=str(e))

    async def _channel_health_loop(self):
        """渠道健康检查: 监控渠道心跳，故障时自动转移 + 恢复时 flush pending"""
        await asyncio.sleep(15)
        was_offline: set[str] = set()
        while self._running:
            try:
                router = await self._get_notify_router()
                await router.refresh_channel_states()
                status = router.get_status()

                for ch_name, ch_info in status["channels"].items():
                    if not ch_info["online"]:
                        if ch_name not in was_offline:
                            was_offline.add(ch_name)
                            age = ch_info.get("age")
                            logger.warning(f"Channel {ch_name} went offline (age={age}s)")
                            other_active = [c for c in status["active"] if c != ch_name]
                            if other_active:
                                await self._notify(
                                    f"⚠️ 渠道 [{ch_name}] 离线，已切换到 {other_active}",
                                    topic="system", priority="high",
                                )
                    else:
                        if ch_name in was_offline:
                            was_offline.discard(ch_name)
                            logger.info(f"Channel {ch_name} recovered")
                            flushed = await router.flush_pending()
                            if flushed:
                                await self._notify(
                                    f"✅ 渠道 [{ch_name}] 恢复，已重发 {flushed} 条积压消息",
                                    topic="system", priority="normal",
                                )
            except Exception as e:
                logger.debug(f"Channel health loop error: {e}")
            await asyncio.sleep(30)

    async def _ack_listener(self):
        """监听渠道投递确认"""
        await asyncio.sleep(5)
        try:
            r = await self.get_redis()
            pubsub = r.pubsub()
            await pubsub.subscribe("rragent:notify:ack")
            while self._running:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg and msg["type"] == "message":
                    try:
                        data = json.loads(msg["data"])
                        router = await self._get_notify_router()
                        await router.handle_ack(data)
                    except Exception:
                        pass
                await asyncio.sleep(0.1)
        except Exception as e:
            logger.debug(f"ACK listener error: {e}")

    async def start(self):
        self._running = True
        self.logger.info(f"Orchestrator starting (pid={os.getpid()}, rules={len(self.rules)})")
        await asyncio.gather(
            self._listen(),
            self._heartbeat(),
            self._schedule_loop(),
            self._event_monitor_loop(),
            self._memory_health_loop(),
            self._memory_remind_loop(),
            self._soul_guardian_loop(),
            self._channel_health_loop(),
            self._ack_listener(),
        )


if __name__ == "__main__":
    run_agent(Orchestrator())

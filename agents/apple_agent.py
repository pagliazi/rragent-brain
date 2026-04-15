"""
AppleAgent — macOS 生态管家
职责: 日历、提醒事项、备忘录、通讯录、邮件、系统通知、Spotlight、Music、快捷指令、系统信息
数据源: macOS 原生 API (AppleScript / JXA / CLI tools)
不依赖 LLM，纯系统调用

重要: 必须以 GUI 用户(zayl) 运行，否则 osascript 无法访问 Calendar/Reminders 等 App
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta

from agents.base import BaseAgent, AgentMessage, run_agent

logger = logging.getLogger("agent.apple")

SHELL_TIMEOUT = int(os.getenv("APPLE_AGENT_TIMEOUT", "15"))


# ── 安全: AppleScript 字符串转义 ──

def _as_str(s: str) -> str:
    """转义字符串用于 AppleScript 双引号内嵌"""
    if not s:
        return ""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "")


def _sanitize_input(s: str, max_len: int = 1000) -> str:
    """清理用户输入，防止 AppleScript 注入"""
    if not s:
        return ""
    s = s[:max_len]
    s = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', s)
    return s.strip()


# ── 执行器: 通过 stdin 传递脚本，避免 shell 转义问题 ──
# 注意: 本 agent 必须以 GUI 用户 (run_as: zayl) 运行，
# 因为 macOS TCC 只允许 GUI session 中的进程访问 Calendar/Reminders 等 App。
# rragentctl 通过 registry.yaml 的 run_as 字段控制运行用户，
# agent 之间通过 Redis Pub/Sub 通信，不受运行用户差异影响。

async def _run(cmd: str, timeout: int = SHELL_TIMEOUT) -> tuple[int, str]:
    """执行 shell 命令"""
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode or 0, stdout.decode(errors="replace").strip()
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return -1, f"命令超时 ({timeout}s)，请稍后重试"
    except Exception as e:
        return -1, str(e)


async def _exec(args: list, timeout: int = SHELL_TIMEOUT, stdin_data: bytes = None) -> tuple[int, str]:
    """安全执行命令（使用 exec 而非 shell）"""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE if stdin_data is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        if stdin_data is not None:
            stdout, _ = await asyncio.wait_for(proc.communicate(input=stdin_data), timeout=timeout)
        else:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode or 0, stdout.decode(errors="replace").strip() if stdout else ""
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return -1, f"命令超时 ({timeout}s)"
    except Exception as e:
        return -1, str(e)


async def _osascript(script: str, timeout: int = SHELL_TIMEOUT) -> tuple[int, str]:
    """通过 stdin 管道执行 AppleScript"""
    return await _exec(["osascript", "-"], timeout=timeout, stdin_data=script.encode("utf-8"))


async def _jxa(script: str, timeout: int = SHELL_TIMEOUT) -> tuple[int, str]:
    """通过 stdin 管道执行 JXA (JavaScript for Automation)"""
    return await _exec(["osascript", "-l", "JavaScript", "-"], timeout=timeout, stdin_data=script.encode("utf-8"))


# ── 日期解析 (完整中英文自然语言支持) ──

_CN_WEEKDAY = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}


def _parse_natural_datetime(text: str) -> datetime | None:
    """解析自然语言日期时间 — 支持中英文

    支持:
      今天/明天/后天/大后天、周X/下周X/本周X
      上午/下午/晚上 + N点(半)、N:MM
      tomorrow, next monday, today, in N hours/minutes
      2026-03-25 14:00, 03-25 14:00
      7:30, 07:30, 3pm, 3:30pm
      明天下午3点, 后天上午10点半, 下周一早上9点
    """
    if not text:
        return None
    text = text.strip()
    now = datetime.now()

    # 1) 标准格式优先
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M",
                "%m-%d %H:%M", "%m/%d %H:%M"):
        try:
            dt = datetime.strptime(text, fmt)
            if dt.year == 1900:
                dt = dt.replace(year=now.year)
            return dt
        except ValueError:
            continue

    # 纯日期格式
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m-%d", "%m/%d"):
        try:
            dt = datetime.strptime(text, fmt)
            if dt.year == 1900:
                dt = dt.replace(year=now.year)
            return dt.replace(hour=9, minute=0)  # 默认9点
        except ValueError:
            continue

    # 2) 提取日期部分
    target_date = None
    remaining = text

    # 中文日期词
    cn_date_patterns = [
        (r'大后天', lambda: now + timedelta(days=3)),
        (r'后天', lambda: now + timedelta(days=2)),
        (r'明天', lambda: now + timedelta(days=1)),
        (r'今天', lambda: now),
        (r'昨天', lambda: now - timedelta(days=1)),
    ]
    for pat, fn in cn_date_patterns:
        if pat in remaining:
            target_date = fn().date()
            remaining = remaining.replace(pat, '', 1).strip()
            break

    # 下周X / 周X / 本周X / 这周X
    if target_date is None:
        m = re.search(r'(下个?|本|这)?周([一二三四五六日天])', remaining)
        if m:
            prefix = m.group(1) or ""
            wd = _CN_WEEKDAY.get(m.group(2), 0)
            current_wd = now.weekday()
            if "下" in prefix:
                days_ahead = (wd - current_wd) % 7
                if days_ahead == 0:
                    days_ahead = 7
                days_ahead += 7 if days_ahead <= 0 else 0
                # 确保是下周
                if days_ahead <= 7:
                    days_ahead = (wd - current_wd) % 7 + 7
            else:
                days_ahead = (wd - current_wd) % 7
                if days_ahead == 0:
                    days_ahead = 7
            target_date = (now + timedelta(days=days_ahead)).date()
            remaining = remaining[:m.start()] + remaining[m.end():]
            remaining = remaining.strip()

    # 英文日期词
    if target_date is None:
        en_date = {
            'today': 0, 'tomorrow': 1, 'day after tomorrow': 2,
        }
        lower = remaining.lower()
        for word, delta in en_date.items():
            if word in lower:
                target_date = (now + timedelta(days=delta)).date()
                remaining = re.sub(re.escape(word), '', remaining, count=1, flags=re.IGNORECASE).strip()
                break

    # next monday/tuesday/...
    if target_date is None:
        m = re.search(r'next\s+(mon|tue|wed|thu|fri|sat|sun)\w*', remaining, re.IGNORECASE)
        if m:
            en_wd = {'mon': 0, 'tue': 1, 'wed': 2, 'thu': 3, 'fri': 4, 'sat': 5, 'sun': 6}
            wd = en_wd.get(m.group(1).lower()[:3], 0)
            days_ahead = (wd - now.weekday()) % 7 + 7
            target_date = (now + timedelta(days=days_ahead)).date()
            remaining = remaining[:m.start()] + remaining[m.end():]
            remaining = remaining.strip()

    # in N hours/minutes/days
    if target_date is None:
        m = re.search(r'in\s+(\d+)\s*(hours?|minutes?|mins?|days?|小时|分钟|天)', remaining, re.IGNORECASE)
        if m:
            n = int(m.group(1))
            unit = m.group(2).lower()
            if unit.startswith(('hour', '小时')):
                return now + timedelta(hours=n)
            elif unit.startswith(('min', '分')):
                return now + timedelta(minutes=n)
            elif unit.startswith(('day', '天')):
                target_date = (now + timedelta(days=n)).date()
            remaining = remaining[:m.start()] + remaining[m.end():]
            remaining = remaining.strip()

    # N小时后 / N分钟后
    if target_date is None:
        m = re.search(r'(\d+)\s*(小时|分钟|分|天)(后|之后|以后)', remaining)
        if m:
            n = int(m.group(1))
            unit = m.group(2)
            if '小时' in unit:
                return now + timedelta(hours=n)
            elif '分' in unit:
                return now + timedelta(minutes=n)
            elif '天' in unit:
                target_date = (now + timedelta(days=n)).date()
            remaining = remaining[:m.start()] + remaining[m.end():]
            remaining = remaining.strip()

    # 3) 提取时间部分
    target_hour, target_minute = None, 0

    # 中文时段 + 数字: 上午/下午/晚上/早上 N点(半)/N:MM
    m = re.search(r'(上午|早上|早晨|am|AM|下午|午后|pm|PM|晚上|晚间|傍晚|凌晨|中午)\s*(\d{1,2})\s*[:：点时]\s*(\d{1,2})?\s*(半)?', remaining)
    if m:
        period = m.group(1)
        hour = int(m.group(2))
        minute = int(m.group(3)) if m.group(3) else (30 if m.group(4) else 0)
        if period in ('下午', '午后', 'pm', 'PM', '晚上', '晚间', '傍晚'):
            if hour < 12:
                hour += 12
        elif period in ('凌晨',):
            pass  # 0-6 as is
        elif period in ('中午',):
            if hour < 12:
                hour = 12
        elif period in ('上午', '早上', '早晨', 'am', 'AM'):
            if hour == 12:
                hour = 0
        target_hour = hour
        target_minute = minute
        remaining = remaining[:m.start()] + remaining[m.end():]

    # 纯中文: N点(半) / N点M分
    if target_hour is None:
        m = re.search(r'(\d{1,2})\s*[:：点时]\s*(\d{1,2})?\s*(半)?\s*(分)?', remaining)
        if m:
            target_hour = int(m.group(1))
            target_minute = int(m.group(2)) if m.group(2) else (30 if m.group(3) else 0)
            remaining = remaining[:m.start()] + remaining[m.end():]

    # 英文: 3pm, 3:30pm, 15:00
    if target_hour is None:
        m = re.search(r'(\d{1,2}):(\d{2})\s*(am|pm)?', remaining, re.IGNORECASE)
        if m:
            target_hour = int(m.group(1))
            target_minute = int(m.group(2))
            if m.group(3) and m.group(3).lower() == 'pm' and target_hour < 12:
                target_hour += 12
            if m.group(3) and m.group(3).lower() == 'am' and target_hour == 12:
                target_hour = 0
            remaining = remaining[:m.start()] + remaining[m.end():]

    if target_hour is None:
        m = re.search(r'(\d{1,2})\s*(am|pm)', remaining, re.IGNORECASE)
        if m:
            target_hour = int(m.group(1))
            if m.group(2).lower() == 'pm' and target_hour < 12:
                target_hour += 12
            if m.group(2).lower() == 'am' and target_hour == 12:
                target_hour = 0
            remaining = remaining[:m.start()] + remaining[m.end():]

    # 4) 组合
    if target_date is None and target_hour is not None:
        # 只有时间没有日期 — 如果时间已过就是明天
        target_date = now.date()
        candidate = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
        if candidate <= now:
            target_date = (now + timedelta(days=1)).date()

    if target_date is None:
        return None  # 无法解析

    if target_hour is None:
        target_hour = 9  # 默认早上9点
        target_minute = 0

    return datetime.combine(target_date, datetime.min.time().replace(hour=target_hour, minute=target_minute))


def _parse_date(date_str: str) -> str:
    """解析日期字符串 → YYYY-MM-DD"""
    dt = _parse_natural_datetime(date_str)
    if dt:
        return dt.strftime("%Y-%m-%d")
    if not date_str or date_str.lower() == "today":
        return datetime.now().strftime("%Y-%m-%d")
    return date_str


def _parse_datetime(dt_str: str) -> datetime | None:
    """解析日期时间字符串 → datetime"""
    return _parse_natural_datetime(dt_str)


def _permission_hint(app_name: str) -> str:
    return (
        f"请检查: 系统设置 → 隐私与安全性 → 自动化 → 确认 osascript 已授权访问 {app_name}\n"
        f"首次访问时 macOS 会弹出授权对话框，请点击「好」。\n"
        f"如果没有弹出，请在终端手动执行一次: osascript -e 'tell application \"{app_name}\" to return name'"
    )


class AppleAgent(BaseAgent):
    name = "apple"

    async def _startup_check(self):
        """启动时检查 osascript 是否可用"""
        import getpass
        current_user = getpass.getuser()
        logger.info(f"AppleAgent 启动: 用户={current_user}")

        code, output = await _osascript('return "ok"')
        if code != 0:
            logger.error(f"osascript 不可用: {output}")
            logger.error("Apple Agent 必须以 GUI 登录用户运行 (registry.yaml: run_as: zayl)")
        else:
            logger.info("AppleAgent: osascript 可用")

    async def handle(self, msg: AgentMessage):
        action = msg.action
        params = msg.params

        handlers = {
            "calendar_today": self._calendar_today,
            "calendar_create": self._calendar_create,
            "calendar_delete": self._calendar_delete,
            "remind_create": self._remind_create,
            "remind_list": self._remind_list,
            "remind_complete": self._remind_complete,
            "remind_edit": self._remind_edit,
            "remind_delete": self._remind_delete,
            "remind_lists": self._remind_lists,
            "note_create": self._note_create,
            "note_search": self._note_search,
            "contact_search": self._contact_search,
            "mail_send": self._mail_send,
            "notify": self._notify,
            "spotlight": self._spotlight,
            "music_control": self._music_control,
            "shortcut_run": self._shortcut_run,
            "shortcut_list": self._shortcut_list,
            "system_info": self._system_info,
            "clipboard_read": self._clipboard_read,
            "clipboard_write": self._clipboard_write,
            "finder_open": self._finder_open,
            "volume_control": self._volume_control,
            "app_control": self._app_control,
            "screen_brightness": self._screen_brightness,
            "do_not_disturb": self._do_not_disturb,
            "alarm_set": self._alarm_set,
            "alarm_list": self._alarm_list,
            "alarm_cancel": self._alarm_cancel,
            "timer_set": self._timer_set,
        }

        handler = handlers.get(action)
        if handler:
            try:
                await handler(msg, params)
            except Exception as e:
                logger.exception(f"Apple handler {action} error")
                await self.reply(msg, error=f"执行 {action} 时出错: {e}")
        else:
            available = ", ".join(sorted(handlers.keys()))
            await self.reply(msg, error=f"未知操作: {action}\n可用操作: {available}")

    # ── Calendar ──

    async def _calendar_today(self, msg: AgentMessage, params: dict):
        target_date = _parse_date(params.get("date", "today"))
        # JXA — 日期处理更可靠，输出 JSON（通过 sudo -u zayl 访问 GUI session）
        jxa_script = f"""
var app = Application("Calendar");
app.includeStandardAdditions = true;
var d = new Date("{target_date}T00:00:00");
var end = new Date("{target_date}T23:59:59");
var results = [];
try {{
    app.calendars().forEach(function(cal) {{
        try {{
            var evts = cal.events.whose({{
                startDate: {{_greaterThanEquals: d}},
                startDate: {{_lessThanEquals: end}}
            }})();
            evts.forEach(function(e) {{
                try {{
                    results.push({{
                        title: e.summary(),
                        start: e.startDate().toLocaleTimeString('zh-CN', {{hour:'2-digit', minute:'2-digit'}}),
                        end: e.endDate().toLocaleTimeString('zh-CN', {{hour:'2-digit', minute:'2-digit'}}),
                        location: e.location() || "",
                        allDay: e.alldayEvent(),
                        calendar: cal.name()
                    }});
                }} catch(ex) {{}}
            }});
        }} catch(ex) {{}}
    }});
}} catch(ex) {{
    results = [{{"error": ex.message}}];
}}
JSON.stringify(results);
"""
        code, output = await _jxa(jxa_script, timeout=20)
        if code != 0:
            hint = _permission_hint("日历")
            await self.reply(msg, error=f"日历查询失败: {output}\n\n{hint}")
            return

        try:
            events = json.loads(output) if output else []
        except json.JSONDecodeError:
            events = []

        if not events:
            await self.reply(msg, result={"text": f"📅 {target_date} 没有日程安排", "events": [], "date": target_date})
            return

        if events and isinstance(events[0], dict) and events[0].get("error"):
            await self.reply(msg, error=f"日历查询失败: {events[0]['error']}")
            return

        lines = []
        for e in events:
            time_str = "全天" if e.get("allDay") else f"{e.get('start', '?')} - {e.get('end', '?')}"
            loc = f" 📍{e['location']}" if e.get("location") else ""
            cal = f" [{e['calendar']}]" if e.get("calendar") else ""
            lines.append(f"  • {e.get('title', '无标题')}  {time_str}{loc}{cal}")

        text = f"📅 {target_date} 日程 ({len(events)} 项):\n" + "\n".join(lines)
        await self.reply(msg, result={"text": text, "events": events, "date": target_date})

    async def _calendar_create(self, msg: AgentMessage, params: dict):
        title = _sanitize_input(params.get("title", ""))
        start_str = params.get("start", "")
        end_str = params.get("end", "")
        location = _sanitize_input(params.get("location", ""))
        notes = _sanitize_input(params.get("notes", ""))
        cal_name = _sanitize_input(params.get("calendar_name", ""))

        if not title or not start_str:
            await self.reply(msg, error="缺少事件标题或开始时间\n格式: title=标题 start=2026-03-22 14:00 [end=...] [location=...]")
            return

        start_dt = _parse_datetime(start_str)
        if not start_dt:
            await self.reply(msg, error=f"无法解析开始时间: {start_str}\n支持格式: YYYY-MM-DD HH:MM")
            return

        end_dt = _parse_datetime(end_str) if end_str else None
        if not end_dt:
            end_dt = start_dt + timedelta(hours=1)

        # JXA 创建事件 — 日期处理更精确
        loc_line = f'evt.location = "{_as_str(location)}";' if location else ""
        notes_line = f'evt.description = "{_as_str(notes)}";' if notes else ""
        cal_line = f'app.calendars.byName("{_as_str(cal_name)}")' if cal_name else "app.defaultCalendar()"

        jxa_script = f"""
var app = Application("Calendar");
var cal = {cal_line};
var evt = app.Event({{
    summary: "{_as_str(title)}",
    startDate: new Date("{start_dt.strftime('%Y-%m-%dT%H:%M:%S')}"),
    endDate: new Date("{end_dt.strftime('%Y-%m-%dT%H:%M:%S')}"),
    alldayEvent: false
}});
{loc_line}
{notes_line}
cal.events.push(evt);
"OK";
"""
        code, output = await _jxa(jxa_script)
        if code != 0:
            await self.reply(msg, error=f"创建日历事件失败: {output}\n\n{_permission_hint('日历')}")
            return
        await self.reply(msg, result={
            "text": f"📅 已创建日历事件: {title}\n⏰ {start_str} → {end_str or '(+1h)'}" +
                    (f"\n📍 {location}" if location else ""),
            "created": True
        })

    async def _calendar_delete(self, msg: AgentMessage, params: dict):
        title = _sanitize_input(params.get("title", ""))
        target_date = _parse_date(params.get("date", "today"))

        if not title:
            await self.reply(msg, error="缺少事件标题")
            return

        jxa_script = f"""
var app = Application("Calendar");
var d = new Date("{target_date}T00:00:00");
var end = new Date("{target_date}T23:59:59");
var deleted = 0;
var keyword = "{_as_str(title)}".toLowerCase();
app.calendars().forEach(function(cal) {{
    try {{
        var evts = cal.events.whose({{
            startDate: {{_greaterThanEquals: d}},
            startDate: {{_lessThanEquals: end}}
        }})();
        for (var i = evts.length - 1; i >= 0; i--) {{
            try {{
                if (evts[i].summary().toLowerCase().indexOf(keyword) !== -1) {{
                    evts[i].delete();
                    deleted++;
                }}
            }} catch(ex) {{}}
        }}
    }} catch(ex) {{}}
}});
JSON.stringify({{deleted: deleted}});
"""
        code, output = await _jxa(jxa_script)
        if code != 0:
            await self.reply(msg, error=f"删除日历事件失败: {output}")
            return
        try:
            result = json.loads(output)
            count = result.get("deleted", 0)
        except Exception:
            count = 0
        await self.reply(msg, result={"text": f"📅 已删除 {count} 个匹配「{title}」的事件 ({target_date})", "deleted": count})

    # ── Reminders (via remindctl + JXA fallback) ──

    async def _remindctl(self, args: list) -> tuple[int, str]:
        """执行 remindctl 命令"""
        return await _exec(["/opt/homebrew/bin/remindctl"] + args, timeout=10)

    async def _remind_create(self, msg: AgentMessage, params: dict):
        """创建提醒 — 支持全部 Apple Reminders 字段
        params: title, notes, url, due, priority(high/medium/low/none/urgent),
                list_name, flagged(bool), remind_date
        """
        title = _sanitize_input(params.get("title", ""))
        if not title:
            await self.reply(msg, error=(
                "缺少提醒内容\n\n"
                "用法: title=内容 [due=明天 14:00] [notes=备注] [url=链接]\n"
                "      [priority=high|medium|low] [list_name=列表名] [flagged=true]"
            ))
            return

        notes = _sanitize_input(params.get("notes", ""), max_len=5000)
        url = params.get("url", "").strip()
        due = params.get("due", "").strip()
        remind_date = params.get("remind_date", "").strip()
        priority = params.get("priority", "").strip().lower()
        list_name = _sanitize_input(params.get("list_name", ""))
        flagged = str(params.get("flagged", "")).lower() in ("true", "1", "yes")

        # 优先级映射: urgent → high
        pri_map = {"urgent": "high", "high": "high", "medium": "medium",
                   "low": "low", "none": "none", "": ""}
        priority = pri_map.get(priority, priority)

        # 如果 notes 里没内容但有 url，把 url 附到 notes
        if url and not notes:
            notes = url
        elif url and notes:
            notes = f"{notes}\n{url}"

        # 自然语言日期 → ISO 格式 (remindctl 只懂英文)
        if due:
            due_dt = _parse_natural_datetime(due)
            if due_dt:
                due = due_dt.strftime("%Y-%m-%d %H:%M")

        if remind_date:
            rd_dt = _parse_natural_datetime(remind_date)
            if rd_dt:
                remind_date = rd_dt.strftime("%Y-%m-%d %H:%M")

        # 尝试 remindctl (更可靠的 CLI)
        cmd = ["add", title]
        if due:
            cmd += ["--due", due]
        if notes:
            cmd += ["--notes", notes]
        if priority and priority != "none":
            cmd += ["--priority", priority]
        if list_name:
            cmd += ["--list", list_name]
        cmd += ["--json", "--no-color", "--no-input"]

        code, output = await self._remindctl(cmd)
        if code == 0:
            # remindctl 成功
            details = []
            if due:
                details.append(f"⏰ 到期: {due}")
            if notes:
                details.append(f"📝 备注: {notes[:80]}{'...' if len(notes)>80 else ''}")
            if priority and priority != "none":
                details.append(f"🔺 优先级: {priority}")
            if list_name:
                details.append(f"📋 列表: {list_name}")
            if url:
                details.append(f"🔗 URL: {url}")

            detail_str = "\n".join(details)
            text = f"✅ 已创建提醒: {title}"
            if detail_str:
                text += f"\n{detail_str}"

            # 如果需要 flagged 或 remind_date，用 JXA 补充设置
            if flagged or remind_date:
                await self._remind_set_extra(title, flagged, remind_date)

            await self.reply(msg, result={"text": text, "created": True})
            return

        # remindctl 失败，fallback 到 JXA
        logger.warning(f"remindctl failed ({code}): {output}, falling back to JXA")
        due_line = ""
        if due:
            due_dt = _parse_datetime(due)
            if due_dt:
                due_line = f'rem.dueDate = new Date("{due_dt.strftime("%Y-%m-%dT%H:%M:%S")}");'

        remind_line = ""
        if remind_date:
            rd = _parse_datetime(remind_date)
            if rd:
                remind_line = f'rem.remindMeDate = new Date("{rd.strftime("%Y-%m-%dT%H:%M:%S")}");'

        notes_line = f'rem.body = "{_as_str(notes)}";' if notes else ""
        list_line = f'app.lists.byName("{_as_str(list_name)}")' if list_name else "app.defaultList()"
        # Apple priority: 0=none, 1=high, 5=medium, 9=low
        pri_val = {"high": 1, "medium": 5, "low": 9}.get(priority, 0)
        pri_line = f"rem.priority = {pri_val};" if pri_val else ""
        flag_line = "rem.flagged = true;" if flagged else ""

        jxa_script = f"""
var app = Application("Reminders");
var list = {list_line};
var rem = app.Reminder({{name: "{_as_str(title)}"}});
{due_line}
{remind_line}
{notes_line}
{pri_line}
{flag_line}
list.reminders.push(rem);
"OK";
"""
        code, output = await _jxa(jxa_script)
        if code != 0:
            await self.reply(msg, error=f"创建提醒失败: {output}\n\n{_permission_hint('提醒事项')}")
            return
        due_info = f"\n⏰ 到期: {due}" if due else ""
        await self.reply(msg, result={"text": f"⏰ 已创建提醒: {title}{due_info}", "created": True})

    async def _remind_set_extra(self, title: str, flagged: bool, remind_date: str):
        """JXA 补充设置 flagged 和 remindMeDate（remindctl 不支持的字段）"""
        lines = []
        if flagged:
            lines.append("r.flagged = true;")
        if remind_date:
            rd = _parse_datetime(remind_date)
            if rd:
                lines.append(f'r.remindMeDate = new Date("{rd.strftime("%Y-%m-%dT%H:%M:%S")}");')
        if not lines:
            return
        extra = "\n".join(lines)
        jxa = f"""
var app = Application("Reminders");
var keyword = "{_as_str(title)}".toLowerCase();
app.lists().forEach(function(lst) {{
    try {{
        lst.reminders().forEach(function(r) {{
            try {{
                if (!r.completed() && r.name().toLowerCase().indexOf(keyword) !== -1) {{
                    {extra}
                }}
            }} catch(ex) {{}}
        }});
    }} catch(ex) {{}}
}});
"OK";
"""
        await _jxa(jxa)

    async def _remind_list(self, msg: AgentMessage, params: dict):
        """列出提醒 — 支持 filter: today/tomorrow/week/overdue/upcoming/all + list"""
        list_name = _sanitize_input(params.get("list_name", ""))
        filter_type = params.get("filter", "").strip().lower() or "upcoming"

        cmd = ["show", filter_type, "--json", "--no-color", "--no-input"]
        if list_name:
            cmd += ["--list", list_name]

        code, output = await self._remindctl(cmd)
        if code == 0 and output.strip():
            try:
                data = json.loads(output)
                reminders = data if isinstance(data, list) else data.get("reminders", [])
                if not reminders:
                    await self.reply(msg, result={"text": f"⏰ 没有{filter_type}的提醒事项", "reminders": []})
                    return

                lines = []
                for r in reminders:
                    parts = []
                    idx = r.get("index", "")
                    title = r.get("title", "")
                    lst = r.get("list", "")
                    due = r.get("due", r.get("dueDate", ""))
                    pri = r.get("priority", "none")
                    notes = r.get("notes", "")
                    flagged = r.get("flagged", False)

                    marker = f"[{idx}]" if idx else "•"
                    flag_icon = " 🚩" if flagged else ""
                    pri_icon = {"high": " ❗", "medium": " ❕", "low": ""}.get(pri, "")
                    due_str = f"  ⏰ {due}" if due else ""
                    notes_str = f"\n      📝 {notes[:60]}..." if notes and len(notes) > 60 else (f"\n      📝 {notes}" if notes else "")

                    lines.append(f"  {marker} [{lst}] {title}{flag_icon}{pri_icon}{due_str}{notes_str}")

                text = f"⏰ 提醒事项 — {filter_type} ({len(reminders)} 项):\n" + "\n".join(lines)
                await self.reply(msg, result={"text": text, "reminders": reminders})
                return
            except json.JSONDecodeError:
                pass

        # remindctl 失败或无 JSON，fallback 纯文本
        if code == 0 and output.strip():
            await self.reply(msg, result={"text": f"⏰ 提醒事项:\n{output}"})
            return

        # fallback JXA
        list_filter = f'.byName("{_as_str(list_name)}")' if list_name else ""
        jxa_script = f"""
var app = Application("Reminders");
var results = [];
var lists = {f'[app.lists{list_filter}]' if list_name else 'app.lists()'};
lists.forEach(function(lst) {{
    try {{
        var rems = lst.reminders.whose({{completed: false}})();
        rems.forEach(function(r) {{
            try {{
                var due = "";
                try {{ due = r.dueDate() ? r.dueDate().toISOString() : ""; }} catch(e) {{}}
                var notes = "";
                try {{ notes = r.body() || ""; }} catch(e) {{}}
                results.push({{
                    title: r.name(),
                    list: lst.name(),
                    due: due,
                    priority: r.priority(),
                    flagged: r.flagged(),
                    notes: notes
                }});
            }} catch(ex) {{}}
        }});
    }} catch(ex) {{}}
}});
JSON.stringify(results);
"""
        code, output = await _jxa(jxa_script)
        if code != 0:
            await self.reply(msg, error=f"查询提醒失败: {output}\n\n{_permission_hint('提醒事项')}")
            return

        try:
            reminders = json.loads(output) if output else []
        except json.JSONDecodeError:
            reminders = []

        if not reminders:
            await self.reply(msg, result={"text": "⏰ 没有未完成的提醒事项", "reminders": []})
            return

        lines = []
        for r in reminders:
            due = f"  ⏰{r['due']}" if r.get("due") else ""
            pri = " ❗" if r.get("priority", 0) in (1, 2, 3) else ""
            flag = " 🚩" if r.get("flagged") else ""
            notes_str = f"\n      📝 {r['notes'][:60]}" if r.get("notes") else ""
            lines.append(f"  • [{r.get('list', '')}] {r.get('title', '')}{flag}{pri}{due}{notes_str}")

        text = f"⏰ 未完成提醒 ({len(reminders)} 项):\n" + "\n".join(lines)
        await self.reply(msg, result={"text": text, "reminders": reminders})

    async def _remind_complete(self, msg: AgentMessage, params: dict):
        title = _sanitize_input(params.get("title", ""))
        idx = params.get("id", params.get("index", "")).strip()

        if idx:
            # 用 remindctl complete by index
            code, output = await self._remindctl(["complete", idx, "--no-color", "--no-input"])
            if code == 0:
                await self.reply(msg, result={"text": f"✅ 已完成提醒 #{idx}"})
                return

        if not title:
            await self.reply(msg, error="缺少提醒标题或索引\n用法: title=关键词 或 id=索引号")
            return

        # JXA fallback: match by title keyword
        jxa_script = f"""
var app = Application("Reminders");
var keyword = "{_as_str(title)}".toLowerCase();
var count = 0;
app.lists().forEach(function(lst) {{
    try {{
        var rems = lst.reminders.whose({{completed: false}})();
        rems.forEach(function(r) {{
            try {{
                if (r.name().toLowerCase().indexOf(keyword) !== -1) {{
                    r.completed = true;
                    count++;
                }}
            }} catch(ex) {{}}
        }});
    }} catch(ex) {{}}
}});
JSON.stringify({{completed: count}});
"""
        code, output = await _jxa(jxa_script)
        if code != 0:
            await self.reply(msg, error=f"完成提醒失败: {output}")
            return
        try:
            result = json.loads(output)
            count = result.get("completed", 0)
        except Exception:
            count = 0
        await self.reply(msg, result={"text": f"✅ 已完成 {count} 个匹配「{title}」的提醒"})

    async def _remind_edit(self, msg: AgentMessage, params: dict):
        """编辑已有提醒 — params: id/index, title, notes, due, priority, list_name"""
        idx = params.get("id", params.get("index", "")).strip()
        if not idx:
            await self.reply(msg, error="缺少提醒索引号\n先用 /remind_list 查看索引，再用 id=索引号 编辑")
            return

        cmd = ["edit", idx]
        title = _sanitize_input(params.get("title", ""))
        notes = _sanitize_input(params.get("notes", ""), max_len=5000)
        due = params.get("due", "").strip()
        priority = params.get("priority", "").strip().lower()
        list_name = _sanitize_input(params.get("list_name", ""))

        if title:
            cmd += ["--title", title]
        if notes:
            cmd += ["--notes", notes]
        if due:
            cmd += ["--due", due]
        if priority:
            pri_map = {"urgent": "high", "high": "high", "medium": "medium",
                       "low": "low", "none": "none"}
            cmd += ["--priority", pri_map.get(priority, priority)]
        if list_name:
            cmd += ["--list", list_name]
        if params.get("clear_due"):
            cmd += ["--clear-due"]

        cmd += ["--no-color", "--no-input"]
        code, output = await self._remindctl(cmd)
        if code == 0:
            await self.reply(msg, result={"text": f"✏️ 已编辑提醒 #{idx}\n{output}"})
        else:
            await self.reply(msg, error=f"编辑失败: {output}")

    async def _remind_delete(self, msg: AgentMessage, params: dict):
        """删除提醒 — params: id/index"""
        idx = params.get("id", params.get("index", "")).strip()
        if not idx:
            await self.reply(msg, error="缺少提醒索引号\n用法: id=索引号")
            return

        code, output = await self._remindctl(["delete", idx, "--force", "--no-color", "--no-input"])
        if code == 0:
            await self.reply(msg, result={"text": f"🗑️ 已删除提醒 #{idx}"})
        else:
            await self.reply(msg, error=f"删除失败: {output}")

    async def _remind_lists(self, msg: AgentMessage, params: dict):
        """列出所有提醒列表"""
        code, output = await self._remindctl(["list", "--json", "--no-color", "--no-input"])
        if code == 0:
            await self.reply(msg, result={"text": f"📋 提醒列表:\n{output}"})
        else:
            # JXA fallback
            jxa = """
var app = Application("Reminders");
var lists = app.lists();
var result = lists.map(function(l) { return l.name(); });
JSON.stringify(result);
"""
            code2, output2 = await _jxa(jxa)
            if code2 == 0:
                try:
                    names = json.loads(output2)
                    text = "📋 提醒列表:\n" + "\n".join(f"  • {n}" for n in names)
                    await self.reply(msg, result={"text": text})
                    return
                except Exception:
                    pass
            await self.reply(msg, error=f"获取列表失败: {output}")

    # ── Notes ──

    async def _note_create(self, msg: AgentMessage, params: dict):
        title = _sanitize_input(params.get("title", ""))
        body = _sanitize_input(params.get("body", ""), max_len=10000)
        folder = _sanitize_input(params.get("folder", ""))

        if not title:
            await self.reply(msg, error="缺少备忘录标题")
            return

        folder_line = f'app.folders.byName("{_as_str(folder)}")' if folder else "app.defaultFolder()"
        # Notes body 需要 HTML 格式
        html_body = body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")

        jxa_script = f"""
var app = Application("Notes");
var folder = {folder_line};
var note = app.Note({{
    name: "{_as_str(title)}",
    body: "<html><head></head><body><h1>{_as_str(title)}</h1><p>{html_body}</p></body></html>"
}});
folder.notes.push(note);
"OK";
"""
        code, output = await _jxa(jxa_script)
        if code != 0:
            # JXA 创建 Notes 可能失败，回退到 AppleScript
            folder_clause = f'folder "{_as_str(folder)}"' if folder else "default folder"
            script = (
                f'tell application "Notes"\n'
                f'  tell {folder_clause}\n'
                f'    make new note with properties {{name:"{_as_str(title)}", body:"{_as_str(body)}"}}\n'
                f'  end tell\n'
                f'end tell\n'
                f'return "OK"'
            )
            code, output = await _osascript(script)
            if code != 0:
                await self.reply(msg, error=f"创建备忘录失败: {output}\n\n{_permission_hint('备忘录')}")
                return

        await self.reply(msg, result={"text": f"📝 已创建备忘录: {title}", "created": True})

    async def _note_search(self, msg: AgentMessage, params: dict):
        keyword = _sanitize_input(params.get("keyword", ""))
        if not keyword:
            await self.reply(msg, error="缺少搜索关键词")
            return

        jxa_script = f"""
var app = Application("Notes");
var keyword = "{_as_str(keyword)}".toLowerCase();
var results = [];
app.folders().forEach(function(folder) {{
    try {{
        folder.notes().forEach(function(n) {{
            try {{
                var name = n.name();
                if (name.toLowerCase().indexOf(keyword) !== -1) {{
                    var body = "";
                    try {{ body = n.plaintext().substring(0, 150); }} catch(e) {{}}
                    results.push({{
                        title: name,
                        snippet: body,
                        folder: folder.name(),
                        modDate: n.modificationDate().toLocaleDateString("zh-CN")
                    }});
                }}
            }} catch(ex) {{}}
        }});
    }} catch(ex) {{}}
}});
JSON.stringify(results.slice(0, 20));
"""
        code, output = await _jxa(jxa_script, timeout=20)
        if code != 0:
            await self.reply(msg, error=f"搜索备忘录失败: {output}\n\n{_permission_hint('备忘录')}")
            return

        try:
            notes = json.loads(output) if output else []
        except json.JSONDecodeError:
            notes = []

        if not notes:
            await self.reply(msg, result={"text": f"📝 未找到包含「{keyword}」的备忘录", "notes": []})
            return

        lines = []
        for n in notes:
            snippet = n.get("snippet", "")[:80]
            if snippet:
                snippet = f" — {snippet}..."
            lines.append(f"  • {n.get('title', '')} [{n.get('folder', '')}]{snippet}")

        text = f"📝 搜索结果 ({len(notes)} 项):\n" + "\n".join(lines)
        await self.reply(msg, result={"text": text, "notes": notes})

    # ── Contacts ──

    async def _contact_search(self, msg: AgentMessage, params: dict):
        name = _sanitize_input(params.get("name", ""))
        if not name:
            await self.reply(msg, error="缺少联系人姓名")
            return

        jxa_script = f"""
var app = Application("Contacts");
var keyword = "{_as_str(name)}".toLowerCase();
var results = [];
app.people().forEach(function(p) {{
    try {{
        var pName = p.name();
        if (pName.toLowerCase().indexOf(keyword) !== -1) {{
            var phone = "";
            var email = "";
            var org = "";
            try {{ phone = p.phones[0].value(); }} catch(e) {{}}
            try {{ email = p.emails[0].value(); }} catch(e) {{}}
            try {{ org = p.organization(); }} catch(e) {{}}
            results.push({{name: pName, phone: phone, email: email, company: org}});
        }}
    }} catch(ex) {{}}
}});
JSON.stringify(results.slice(0, 20));
"""
        code, output = await _jxa(jxa_script, timeout=20)
        if code != 0:
            await self.reply(msg, error=f"搜索联系人失败: {output}\n\n{_permission_hint('通讯录')}")
            return

        try:
            contacts = json.loads(output) if output else []
        except json.JSONDecodeError:
            contacts = []

        if not contacts:
            await self.reply(msg, result={"text": f"👤 未找到匹配「{name}」的联系人", "contacts": []})
            return

        lines = []
        for c in contacts:
            parts = [c.get("name", "")]
            if c.get("company"):
                parts.append(f"🏢{c['company']}")
            if c.get("phone"):
                parts.append(f"📱{c['phone']}")
            if c.get("email"):
                parts.append(f"📧{c['email']}")
            lines.append(f"  • {' | '.join(parts)}")

        text = f"👤 联系人 ({len(contacts)} 人):\n" + "\n".join(lines)
        await self.reply(msg, result={"text": text, "contacts": contacts})

    # ── Mail ──

    async def _mail_send(self, msg: AgentMessage, params: dict):
        to = _sanitize_input(params.get("to", ""))
        subject = _sanitize_input(params.get("subject", ""))
        body = _sanitize_input(params.get("body", ""), max_len=10000)

        if not to or not subject:
            await self.reply(msg, error="缺少收件人或邮件主题\n格式: to=xxx@email.com subject=主题 body=正文")
            return

        # 用 AppleScript 因为 Mail JXA 有兼容问题
        script = (
            f'tell application "Mail"\n'
            f'  set newMsg to make new outgoing message with properties '
            f'{{subject:"{_as_str(subject)}", content:"{_as_str(body)}", visible:true}}\n'
            f'  tell newMsg\n'
            f'    make new to recipient with properties {{address:"{_as_str(to)}"}}\n'
            f'    send\n'
            f'  end tell\n'
            f'end tell\n'
            f'return "OK"'
        )
        code, output = await _osascript(script, timeout=30)
        if code != 0:
            await self.reply(msg, error=f"发送邮件失败: {output}\n\n{_permission_hint('邮件')}")
            return
        await self.reply(msg, result={"text": f"📧 已发送邮件至 {to}\n主题: {subject}"})

    # ── Notifications ──

    async def _notify(self, msg: AgentMessage, params: dict):
        title = _sanitize_input(params.get("title", "OpenClaw"))
        message = _sanitize_input(params.get("message", ""))
        sound = params.get("sound", "default")

        # 优先用 terminal-notifier（更可靠）
        code, _ = await _run("which terminal-notifier")
        if code == 0:
            code, output = await _exec(
                ["terminal-notifier", "-title", title, "-message", message or "(无内容)",
                 "-sound", sound or "default", "-sender", "com.openclaw.agent"],
                timeout=10,
            )
        else:
            script = f'display notification "{_as_str(message)}" with title "{_as_str(title)}" sound name "{_as_str(sound)}"'
            code, output = await _osascript(script)

        if code != 0:
            await self.reply(msg, error=f"发送通知失败: {output}")
            return
        await self.reply(msg, result={"text": f"🔔 已发送系统通知: {title}"})

    # ── Spotlight ──

    async def _spotlight(self, msg: AgentMessage, params: dict):
        query = _sanitize_input(params.get("query", ""))
        kind = params.get("kind", "")
        limit = min(int(params.get("limit", 10)), 50)

        if not query:
            await self.reply(msg, error="缺少搜索关键词")
            return

        kind_map = {
            "pdf": "kMDItemContentType == 'com.adobe.pdf'",
            "image": "kMDItemContentTypeTree == 'public.image'",
            "document": "kMDItemContentTypeTree == 'public.content'",
            "folder": "kMDItemContentType == 'public.folder'",
            "app": "kMDItemContentType == 'com.apple.application-bundle'",
        }

        code, output = await _exec(["mdfind", query], timeout=10)

        if code != 0:
            await self.reply(msg, error=f"Spotlight 搜索失败: {output}")
            return

        if not output.strip():
            await self.reply(msg, result={"text": f"🔍 未找到匹配「{query}」的文件", "files": []})
            return

        files = [f for f in output.strip().split("\n") if f][:limit]
        lines = "\n".join(f"  {i+1}. {f}" for i, f in enumerate(files))
        await self.reply(msg, result={"text": f"🔍 Spotlight 搜索结果 ({len(files)} 个):\n{lines}", "files": files})

    # ── Music ──

    async def _music_control(self, msg: AgentMessage, params: dict):
        action = params.get("action", "status")
        query = _sanitize_input(params.get("query", ""))

        simple_actions = {
            "play": ('tell application "Music" to play', "🎵 播放中"),
            "pause": ('tell application "Music" to pause', "⏸️ 已暂停"),
            "next": ('tell application "Music" to next track', "⏭️ 下一首"),
            "prev": ('tell application "Music" to previous track', "⏮️ 上一首"),
            "stop": ('tell application "Music" to stop', "⏹️ 已停止"),
        }

        if action in simple_actions:
            script, text = simple_actions[action]
            code, output = await _osascript(script)
            if code != 0:
                await self.reply(msg, error=f"Music 操作失败: {output}\n\n{_permission_hint('Music')}")
                return
            await self.reply(msg, result={"text": text})
            return

        if action == "status":
            jxa_script = """
var app = Application("Music");
var result = {};
try {
    result.state = app.playerState();
    if (result.state === "playing" || result.state === "paused") {
        var t = app.currentTrack;
        result.name = t.name();
        result.artist = t.artist();
        result.album = t.album();
        result.duration = t.duration();
        result.position = app.playerPosition();
    }
} catch(e) {
    result.error = e.message;
}
JSON.stringify(result);
"""
            code, output = await _jxa(jxa_script)
            if code != 0:
                await self.reply(msg, error=f"查询音乐状态失败: {output}")
                return
            try:
                info = json.loads(output)
            except json.JSONDecodeError:
                info = {}

            if info.get("error"):
                await self.reply(msg, result={"text": "🎵 Music 未运行或无法访问"})
                return

            state = info.get("state", "stopped")
            if state in ("playing", "paused"):
                icon = "🎵" if state == "playing" else "⏸️"
                pos = int(info.get("position", 0))
                dur = int(info.get("duration", 0))
                text = (f"{icon} {info.get('name', '?')} - {info.get('artist', '?')}\n"
                        f"   💿 {info.get('album', '')}\n"
                        f"   ⏱️ {pos//60}:{pos%60:02d} / {dur//60}:{dur%60:02d}")
            else:
                text = "⏹️ 当前未播放"
            await self.reply(msg, result={"text": text, "info": info})
            return

        if action == "search" and query:
            script = (
                f'tell application "Music"\n'
                f'  set results to (search library playlist 1 for "{_as_str(query)}")\n'
                f'  set output to ""\n'
                f'  set maxCount to 10\n'
                f'  set i to 0\n'
                f'  repeat with t in results\n'
                f'    if i >= maxCount then exit repeat\n'
                f'    set output to output & name of t & " - " & artist of t & linefeed\n'
                f'    set i to i + 1\n'
                f'  end repeat\n'
                f'  return output\n'
                f'end tell'
            )
            code, output = await _osascript(script)
            if code != 0:
                await self.reply(msg, error=f"搜索音乐失败: {output}")
                return
            await self.reply(msg, result={
                "text": f"🎵 搜索「{query}」:\n{output}" if output.strip() else f"🎵 未找到「{query}」"
            })
            return

        if action == "volume":
            vol = params.get("value", "")
            if not vol:
                code, output = await _osascript('tell application "Music" to return sound volume')
                await self.reply(msg, result={"text": f"🔊 当前音量: {output}"})
            else:
                code, output = await _osascript(f'tell application "Music" to set sound volume to {int(vol)}')
                await self.reply(msg, result={"text": f"🔊 音量已设置为 {vol}"})
            return

        await self.reply(msg, error=f"未知 Music 操作: {action}\n可用: play/pause/next/prev/stop/status/search/volume")

    # ── Shortcuts ──

    async def _shortcut_run(self, msg: AgentMessage, params: dict):
        name = _sanitize_input(params.get("name", ""))
        input_text = params.get("input", "")

        if not name:
            await self.reply(msg, error="缺少快捷指令名称\n输入 shortcut_list 查看可用列表")
            return

        code, output = await _exec(
            ["shortcuts", "run", name],
            timeout=60,
            stdin_data=input_text.encode("utf-8") if input_text else None,
            as_gui_user=True,
        )

        if code != 0:
            await self.reply(msg, error=f"运行快捷指令「{name}」失败: {output}")
            return

        text = f"⚡ 快捷指令「{name}」执行完成"
        if output:
            text += f"\n输出:\n{output[:2000]}"
        await self.reply(msg, result={"text": text})

    async def _shortcut_list(self, msg: AgentMessage, params: dict):
        code, output = await _exec(["shortcuts", "list"], timeout=10)

        if code != 0:
            await self.reply(msg, error=f"获取快捷指令列表失败: {output}")
            return
        shortcuts = [s.strip() for s in output.strip().split("\n") if s.strip()]
        lines = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(shortcuts))
        await self.reply(msg, result={"text": f"⚡ 可用快捷指令 ({len(shortcuts)} 个):\n{lines}", "shortcuts": shortcuts})

    # ── System Info ──

    async def _system_info(self, msg: AgentMessage, params: dict):
        category = params.get("category", "all")
        info = {}

        async def _get(key, cmd):
            code, output = await _run(cmd)
            if code == 0:
                info[key] = output

        tasks = []
        if category in ("all", "version"):
            tasks.append(_get("version", "sw_vers"))
        if category in ("all", "battery"):
            tasks.append(_get("battery", "pmset -g batt"))
        if category in ("all", "disk"):
            tasks.append(_get("disk", "df -h / | tail -1 | awk '{print \"总计:\"$2\" 已用:\"$3\" 可用:\"$4\" 使用率:\"$5}'"))
        if category in ("all", "network"):
            tasks.append(_get("wifi", "networksetup -getairportnetwork en0 2>/dev/null || echo 'N/A'"))
            tasks.append(_get("ip", "ipconfig getifaddr en0 2>/dev/null || echo 'N/A'"))
        if category in ("all", "memory"):
            tasks.append(_get("memory", "sysctl -n hw.memsize | awk '{printf \"%.0f GB\", $1/1073741824}'"))
        if category in ("all", "cpu"):
            tasks.append(_get("cpu", "sysctl -n machdep.cpu.brand_string"))
        if category in ("all", "uptime"):
            tasks.append(_get("uptime", "uptime | sed 's/.*up /运行时间: /' | sed 's/,.*//'"))

        await asyncio.gather(*tasks)

        # 组合 network
        if "wifi" in info or "ip" in info:
            info["network"] = f"Wi-Fi: {info.pop('wifi', 'N/A')}\nIP: {info.pop('ip', 'N/A')}"

        lines = []
        label_map = {"version": "系统版本", "battery": "电池", "disk": "磁盘", "network": "网络", "memory": "内存", "cpu": "处理器", "uptime": "运行"}
        for k, v in info.items():
            lines.append(f"【{label_map.get(k, k)}】\n{v}")
        text = "\n\n".join(lines) if lines else "无法获取系统信息"
        await self.reply(msg, result={"text": f"💻 系统信息:\n\n{text}", "info": info})

    # ── Clipboard ──

    async def _clipboard_read(self, msg: AgentMessage, params: dict):
        code, output = await _exec(["pbpaste"], timeout=5)

        if code != 0:
            await self.reply(msg, error=f"读取剪贴板失败: {output}")
            return
        preview = output[:500] + ("..." if len(output) > 500 else "")
        await self.reply(msg, result={"text": f"📋 剪贴板内容 ({len(output)} 字符):\n{preview}", "content": output[:2000]})

    async def _clipboard_write(self, msg: AgentMessage, params: dict):
        text = params.get("text", "")
        if not text:
            await self.reply(msg, error="缺少要写入的文本")
            return
        code, _ = await _exec(["pbcopy"], timeout=5, stdin_data=text.encode("utf-8"))
        if code != 0:
            await self.reply(msg, error="写入剪贴板失败")
            return
        await self.reply(msg, result={"text": f"📋 已写入剪贴板 ({len(text)} 字符)"})

    # ── Finder ──

    async def _finder_open(self, msg: AgentMessage, params: dict):
        path = params.get("path", "")
        if not path:
            await self.reply(msg, error="缺少文件路径")
            return
        real_path = os.path.realpath(os.path.expanduser(path))
        code, output = await _exec(["open", real_path], timeout=10)

        if code != 0:
            await self.reply(msg, error=f"打开失败: {output}\n路径: {real_path}")
            return
        await self.reply(msg, result={"text": f"📂 已打开: {real_path}"})

    # ── 新增: 音量控制 ──

    async def _volume_control(self, msg: AgentMessage, params: dict):
        action = params.get("action", "get")
        value = params.get("value", "")

        if action == "get":
            code, output = await _osascript("output volume of (get volume settings)")
            if code != 0:
                await self.reply(msg, error=f"获取音量失败: {output}")
                return
            await self.reply(msg, result={"text": f"🔊 当前系统音量: {output}", "volume": output})
        elif action == "set" and value:
            vol = max(0, min(100, int(value)))
            code, output = await _osascript(f"set volume output volume {vol}")
            if code != 0:
                await self.reply(msg, error=f"设置音量失败: {output}")
                return
            await self.reply(msg, result={"text": f"🔊 系统音量已设置为 {vol}"})
        elif action == "mute":
            code, output = await _osascript("set volume output muted true")
            await self.reply(msg, result={"text": "🔇 已静音"})
        elif action == "unmute":
            code, output = await _osascript("set volume output muted false")
            await self.reply(msg, result={"text": "🔊 已取消静音"})
        else:
            await self.reply(msg, error="用法: action=get|set|mute|unmute [value=0-100]")

    # ── 新增: App 控制 ──

    async def _app_control(self, msg: AgentMessage, params: dict):
        action = params.get("action", "list")
        app_name = _sanitize_input(params.get("name", ""))

        if action == "list":
            jxa_script = """
var se = Application("System Events");
var apps = se.processes.whose({backgroundOnly: false})();
var result = apps.map(function(a) {
    return {name: a.name(), frontmost: a.frontmost()};
});
JSON.stringify(result);
"""
            code, output = await _jxa(jxa_script)
            if code != 0:
                await self.reply(msg, error=f"获取应用列表失败: {output}")
                return
            try:
                apps = json.loads(output)
            except Exception:
                apps = []
            lines = [f"  {'→' if a.get('frontmost') else ' '} {a.get('name', '?')}" for a in apps]
            await self.reply(msg, result={"text": f"🖥️ 运行中的应用 ({len(apps)}):\n" + "\n".join(lines), "apps": apps})

        elif action == "open" and app_name:
            code, output = await _osascript(f'tell application "{_as_str(app_name)}" to activate')
            if code != 0:
                await self.reply(msg, error=f"打开应用失败: {output}")
                return
            await self.reply(msg, result={"text": f"🖥️ 已打开: {app_name}"})

        elif action == "quit" and app_name:
            code, output = await _osascript(f'tell application "{_as_str(app_name)}" to quit')
            if code != 0:
                await self.reply(msg, error=f"关闭应用失败: {output}")
                return
            await self.reply(msg, result={"text": f"🖥️ 已关闭: {app_name}"})

        else:
            await self.reply(msg, error="用法: action=list|open|quit [name=应用名]")

    # ── 新增: 屏幕亮度 ──

    async def _screen_brightness(self, msg: AgentMessage, params: dict):
        value = params.get("value", "")
        if not value:
            code, output = await _run("brightness -l 2>/dev/null | grep brightness | head -1 | awk '{print $NF}'")
            if code != 0 or not output:
                # fallback: 用 AppleScript
                code, output = await _osascript('tell application "System Events" to return "亮度控制需要安装 brightness CLI"')
                await self.reply(msg, result={"text": "🔆 亮度查询需要安装: brew install brightness"})
                return
            await self.reply(msg, result={"text": f"🔆 当前屏幕亮度: {float(output)*100:.0f}%"})
        else:
            brightness = max(0.0, min(1.0, float(value) / 100.0))
            code, output = await _run(f"brightness {brightness}")
            if code != 0:
                await self.reply(msg, error=f"设置亮度失败: {output}\n需要安装: brew install brightness")
                return
            await self.reply(msg, result={"text": f"🔆 屏幕亮度已设置为 {float(value):.0f}%"})

    # ── 新增: 勿扰模式 ──

    async def _do_not_disturb(self, msg: AgentMessage, params: dict):
        action = params.get("action", "status")
        if action == "on":
            code, output = await _run("shortcuts run '勿扰模式开启' 2>/dev/null || osascript -e 'do shell script \"defaults -currentHost write ~/Library/Preferences/ByHost/com.apple.notificationcenterui doNotDisturb -boolean true && killall NotificationCenter\"'")
            await self.reply(msg, result={"text": "🌙 勿扰模式已开启"})
        elif action == "off":
            code, output = await _run("shortcuts run '勿扰模式关闭' 2>/dev/null || osascript -e 'do shell script \"defaults -currentHost write ~/Library/Preferences/ByHost/com.apple.notificationcenterui doNotDisturb -boolean false && killall NotificationCenter\"'")
            await self.reply(msg, result={"text": "🔔 勿扰模式已关闭"})
        else:
            await self.reply(msg, error="用法: action=on|off")

    # ── Alarm / Timer ──

    _alarms: dict[str, asyncio.Task] = {}
    _alarm_counter: int = 0

    async def _fire_alarm(self, alarm_id: str, label: str):
        """闹钟触发: 系统通知 + 播放声音"""
        script = f'''
display notification "{_as_str(label)}" with title "⏰ 闹钟" sound name "Glass"
delay 1
do shell script "afplay /System/Library/Sounds/Glass.aiff &"
delay 2
do shell script "afplay /System/Library/Sounds/Glass.aiff &"
'''
        await _osascript(script)
        self._alarms.pop(alarm_id, None)
        logger.info(f"Alarm {alarm_id} fired: {label}")

    async def _alarm_set(self, msg: AgentMessage, params: dict):
        """设置闹钟 — params: time (HH:MM), label (可选), date (可选, YYYY-MM-DD)"""
        time_str = params.get("time", "").strip()
        if not time_str:
            await self.reply(msg, error="请指定闹钟时间，如 time=08:30")
            return

        label = _sanitize_input(params.get("label", "闹钟"))
        date_str = params.get("date", "")

        now = datetime.now()
        # 合并 date + time 为一个自然语言字符串解析
        combined = f"{date_str} {time_str}".strip() if date_str else time_str
        target = _parse_natural_datetime(combined)
        if not target:
            # 最后兜底: 纯 HH:MM
            try:
                target = datetime.strptime(time_str, "%H:%M").replace(
                    year=now.year, month=now.month, day=now.day)
                if target <= now:
                    target += timedelta(days=1)
            except ValueError:
                await self.reply(msg, error=(
                    "无法理解时间，支持的格式:\n"
                    "  07:30, 7点半, 下午3点, 明天早上8点\n"
                    "  tomorrow 8am, 3:30pm, in 2 hours"
                ))
                return

        delay_sec = (target - now).total_seconds()
        if delay_sec <= 0:
            await self.reply(msg, error="闹钟时间已过，请设置未来的时间")
            return

        self.__class__._alarm_counter += 1
        alarm_id = f"alarm_{self.__class__._alarm_counter}"

        async def _wait_and_fire():
            try:
                await asyncio.sleep(delay_sec)
                await self._fire_alarm(alarm_id, label)
            except asyncio.CancelledError:
                self._alarms.pop(alarm_id, None)

        task = asyncio.create_task(_wait_and_fire())
        self._alarms[alarm_id] = task

        target_str = target.strftime("%Y-%m-%d %H:%M")
        minutes = int(delay_sec // 60)
        await self.reply(msg, result={
            "text": f"⏰ 闹钟已设置\n  ID: {alarm_id}\n  时间: {target_str}\n  标签: {label}\n  距离触发: {minutes} 分钟"
        })

    async def _alarm_list(self, msg: AgentMessage, params: dict):
        """列出所有活跃闹钟"""
        active = {k: t for k, t in self._alarms.items() if not t.done()}
        if not active:
            await self.reply(msg, result={"text": "当前没有活跃的闹钟"})
            return
        lines = [f"⏰ 活跃闹钟 ({len(active)} 个):"]
        for alarm_id in sorted(active.keys()):
            lines.append(f"  - {alarm_id}")
        await self.reply(msg, result={"text": "\n".join(lines)})

    async def _alarm_cancel(self, msg: AgentMessage, params: dict):
        """取消闹钟 — params: id (闹钟ID) 或 all"""
        alarm_id = params.get("id", "").strip()
        if alarm_id == "all":
            count = 0
            for k, t in list(self._alarms.items()):
                if not t.done():
                    t.cancel()
                    count += 1
            self._alarms.clear()
            await self.reply(msg, result={"text": f"🔕 已取消全部 {count} 个闹钟"})
            return

        if not alarm_id:
            await self.reply(msg, error="请指定闹钟 ID（如 id=alarm_1）或 id=all 取消全部")
            return

        task = self._alarms.get(alarm_id)
        if task and not task.done():
            task.cancel()
            self._alarms.pop(alarm_id, None)
            await self.reply(msg, result={"text": f"🔕 闹钟 {alarm_id} 已取消"})
        else:
            await self.reply(msg, error=f"未找到活跃闹钟: {alarm_id}")

    async def _timer_set(self, msg: AgentMessage, params: dict):
        """设置倒计时定时器 — params: minutes (分钟数), label (可选)"""
        minutes_str = params.get("minutes", "").strip()
        if not minutes_str:
            await self.reply(msg, error="请指定定时分钟数，如 minutes=5")
            return

        try:
            minutes = float(minutes_str)
            if minutes <= 0 or minutes > 1440:
                raise ValueError
        except ValueError:
            await self.reply(msg, error="分钟数必须在 0-1440 之间")
            return

        label = _sanitize_input(params.get("label", f"{minutes}分钟定时器"))
        delay_sec = minutes * 60

        self.__class__._alarm_counter += 1
        alarm_id = f"timer_{self.__class__._alarm_counter}"

        async def _wait_and_fire():
            try:
                await asyncio.sleep(delay_sec)
                await self._fire_alarm(alarm_id, label)
            except asyncio.CancelledError:
                self._alarms.pop(alarm_id, None)

        task = asyncio.create_task(_wait_and_fire())
        self._alarms[alarm_id] = task

        fire_time = (datetime.now() + timedelta(seconds=delay_sec)).strftime("%H:%M")
        await self.reply(msg, result={
            "text": f"⏳ 定时器已启动\n  ID: {alarm_id}\n  时长: {minutes} 分钟\n  标签: {label}\n  将在 {fire_time} 触发"
        })


if __name__ == "__main__":
    agent = AppleAgent()
    run_agent(agent)

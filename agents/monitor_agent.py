"""
MonitorAgent — 基础设施监控告警 Agent
职责: 自动巡检 Prometheus / Grafana，检测异常告警并通知
数据源: Prometheus API (192.168.1.133:9090)、Grafana API (192.168.1.133:3030)
通知: Redis Pub/Sub → Orchestrator → Telegram / 飞书 / WebChat

NOTE: macOS 对 Python.app 有网络沙箱限制，aiohttp/httpx 无法直连内网 192.168.1.x，
      所有外部 HTTP 请求统一走 subprocess + curl 绕过限制。
"""
from __future__ import annotations


import asyncio
import json
import logging
import os
import subprocess
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any

from agents.base import BaseAgent, AgentMessage, run_agent

logger = logging.getLogger("agent.monitor")

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://192.168.1.133:9090")
GRAFANA_URL = os.getenv("GRAFANA_URL", "http://192.168.1.133:3030")
GRAFANA_API_KEY = os.getenv("GRAFANA_API_KEY", "")

POLL_INTERVAL = int(os.getenv("MONITOR_POLL_INTERVAL", "60"))
ALERT_HISTORY_KEY = "rragent:monitor:alert_history"
ALERT_STATE_KEY = "rragent:monitor:alert_state"
NOTIFY_CHANNEL = os.getenv("MONITOR_NOTIFY_CHANNEL", "")
MAX_HISTORY_RECORDS = 500

CURL_TIMEOUT = "10"

HOST_QUERIES = {
    "cpu": '100 - (avg by(instance) (rate(node_cpu_seconds_total{{mode="idle",instance=~"{filter}"}}[5m])) * 100)',
    "memory": '(1 - node_memory_MemAvailable_bytes{{instance=~"{filter}"}} / node_memory_MemTotal_bytes{{instance=~"{filter}"}}) * 100',
    "disk": '(1 - node_filesystem_avail_bytes{{instance=~"{filter}",mountpoint="/",fstype!="tmpfs"}} / node_filesystem_size_bytes{{instance=~"{filter}",mountpoint="/",fstype!="tmpfs"}}) * 100',
    "load1": 'node_load1{{instance=~"{filter}"}}',
    "uptime": '(time() - node_boot_time_seconds{{instance=~"{filter}"}}) / 86400',
    "net_rx": 'rate(node_network_receive_bytes_total{{instance=~"{filter}",device!~"lo|veth.*"}}[5m]) / 1024',
    "net_tx": 'rate(node_network_transmit_bytes_total{{instance=~"{filter}",device!~"lo|veth.*"}}[5m]) / 1024',
}


async def _curl_get(url: str, headers: dict | None = None, timeout: str = CURL_TIMEOUT) -> dict | None:
    """通过 subprocess + curl 发起 GET 请求，绕过 macOS Python 网络沙箱"""
    cmd = ["curl", "-s", "--max-time", timeout, url]
    if headers:
        for k, v in headers.items():
            cmd.extend(["-H", f"{k}: {v}"])
    try:
        loop = asyncio.get_event_loop()
        proc = await loop.run_in_executor(
            None,
            lambda: subprocess.run(cmd, capture_output=True, text=True),
        )
        if proc.returncode != 0:
            logger.error(f"curl failed ({proc.returncode}): {proc.stderr[:200]}")
            return None
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        logger.error(f"curl JSON parse error: {e} — body: {proc.stdout[:200]}")
        return None
    except Exception as e:
        logger.error(f"curl error: {e}")
        return None


class MonitorAgent(BaseAgent):
    name = "monitor"

    def __init__(self):
        super().__init__()
        self._last_alert_hash: set[str] = set()

    _HANDLERS: dict[str, str] = {}  # populated in _resolve_action

    async def handle(self, msg: AgentMessage):
        action = msg.action
        params = msg.params

        handler_map = {
            "check_alerts": self._handle_check_alerts,
            "check_targets": self._handle_check_targets,
            "alert_history": self._handle_alert_history,
            "grafana_alerts": self._handle_grafana_alerts,
            "summary": self._handle_summary,
            "silence": self._handle_silence,
            "check_cert": self._handle_check_cert,
            "query": self._handle_query,
            "metrics": self._handle_metrics,
            "host_health": self._handle_host_health,
            "grafana_dash": self._handle_grafana_dash,
        }

        resolved = self._resolve_action(action)
        handler = handler_map.get(resolved)

        if handler:
            await handler(msg, params)
        else:
            logger.warning(f"Unknown action '{action}', falling back to summary")
            await self._handle_summary(msg, params)

    @staticmethod
    def _resolve_action(action: str) -> str:
        """将任意 action 名智能匹配到已知 handler，支持关键词模糊匹配"""
        KEYWORD_MAP = {
            "alert": "check_alerts",
            "target": "check_targets",
            "history": "alert_history",
            "grafana": "grafana_alerts",
            "summary": "summary",
            "patrol": "summary",
            "overview": "summary",
            "status": "summary",
            "monitor": "summary",
            "silence": "silence",
            "mute": "silence",
            "cert": "check_cert",
            "ssl": "check_cert",
            "tls": "check_cert",
            "query": "query",
            "promql": "query",
            "metric": "metrics",
            "host": "host_health",
            "server": "host_health",
            "health": "host_health",
            "cpu": "host_health",
            "memory": "host_health",
            "disk": "host_health",
            "dashboard": "grafana_dash",
        }
        KNOWN = {"check_alerts", "check_targets", "alert_history", "grafana_alerts",
                 "summary", "silence", "check_cert", "query", "metrics",
                 "host_health", "grafana_dash"}
        if action in KNOWN:
            return action
        a = action.lower().replace("-", "_")
        for keyword, target in KEYWORD_MAP.items():
            if keyword in a:
                return target
        return action

    # ═══════════════════ Data Fetching (curl) ═══════════════════

    async def _prom_get(self, path: str, params: dict | None = None) -> dict | None:
        url = f"{PROMETHEUS_URL}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return await _curl_get(url)

    async def _grafana_get(self, path: str) -> dict | None:
        url = f"{GRAFANA_URL}{path}"
        headers = {"Accept": "application/json"}
        if GRAFANA_API_KEY:
            headers["Authorization"] = f"Bearer {GRAFANA_API_KEY}"
        return await _curl_get(url, headers=headers)

    async def _fetch_prometheus_alerts(self) -> list | None:
        data = await self._prom_get("/api/v1/alerts")
        if data and data.get("status") == "success":
            return data.get("data", {}).get("alerts", [])
        return None

    async def _fetch_prometheus_targets(self) -> list | None:
        data = await self._prom_get("/api/v1/targets")
        if data and data.get("status") == "success":
            return data.get("data", {}).get("activeTargets", [])
        return None

    async def _fetch_grafana_alerts(self) -> list | None:
        for endpoint in [
            "/api/prometheus/grafana/api/v1/alerts",
            "/api/alertmanager/grafana/api/v2/alerts",
            "/api/v1/provisioning/alert-rules",
        ]:
            data = await self._grafana_get(endpoint)
            if data is not None:
                if isinstance(data, dict) and "data" in data:
                    return data["data"].get("alerts", [])
                if isinstance(data, list):
                    return data
        return None

    async def _prom_query(self, promql: str) -> list | None:
        data = await self._prom_get("/api/v1/query", {"query": promql})
        if data and data.get("status") == "success":
            return data.get("data", {}).get("result", [])
        return None

    async def _prom_query_range(self, promql: str, start: str, end: str, step: str = "60s") -> list | None:
        data = await self._prom_get("/api/v1/query_range", {
            "query": promql, "start": start, "end": end, "step": step,
        })
        if data and data.get("status") == "success":
            return data.get("data", {}).get("result", [])
        return None

    # ═══════════════════ Action Handlers ═══════════════════

    async def _handle_check_alerts(self, msg: AgentMessage, params: dict):
        """查询 Prometheus 当前活跃告警"""
        alerts = await self._fetch_prometheus_alerts()
        if alerts is None:
            await self.reply(msg, error="Prometheus 不可达")
            return

        firing = [a for a in alerts if a.get("state") == "firing"]
        pending = [a for a in alerts if a.get("state") == "pending"]

        text = self._format_alerts(firing, pending)
        await self.reply(msg, result={
            "text": text,
            "firing_count": len(firing),
            "pending_count": len(pending),
            "alerts": alerts,
        })

    async def _handle_check_targets(self, msg: AgentMessage, params: dict):
        """查询 Prometheus 采集目标健康状态"""
        targets = await self._fetch_prometheus_targets()
        if targets is None:
            await self.reply(msg, error="Prometheus 不可达")
            return

        down_targets = [t for t in targets if t.get("health") != "up"]
        up_targets = [t for t in targets if t.get("health") == "up"]

        lines = [f"📡 **监控目标状态** ({len(up_targets)} 正常 / {len(down_targets)} 异常)"]
        lines.append("")

        if down_targets:
            lines.append("🔴 **异常目标:**")
            for t in down_targets:
                job = t.get("labels", {}).get("job", "?")
                url = t.get("scrapeUrl", t.get("labels", {}).get("instance", "?"))
                error = t.get("lastError", "")
                lines.append(f"  - `{job}` → {url}")
                if error:
                    lines.append(f"    错误: {error}")
        else:
            lines.append("✅ 所有目标正常运行")

        lines.append("")
        lines.append("🟢 **正常目标:**")
        for t in up_targets:
            job = t.get("labels", {}).get("job", "?")
            url = t.get("scrapeUrl", "?")
            lines.append(f"  - `{job}` → {url}")

        text = "\n".join(lines)
        await self.reply(msg, result={
            "text": text,
            "up_count": len(up_targets),
            "down_count": len(down_targets),
        })

    async def _handle_alert_history(self, msg: AgentMessage, params: dict):
        """查询告警历史记录"""
        count = min(params.get("count", 20), 100)
        r = await self.get_redis()
        raw_list = await r.lrange(ALERT_HISTORY_KEY, 0, count - 1)
        records = [json.loads(x) for x in raw_list]

        if not records:
            await self.reply(msg, result={"text": "📭 暂无告警历史记录"})
            return

        lines = [f"📋 **告警历史** (最近 {len(records)} 条)"]
        for rec in records:
            ts = rec.get("time", "?")
            name = rec.get("alertname", "?")
            state = rec.get("state", "?")
            severity = rec.get("severity", "info")
            icon = {"critical": "🔴", "warning": "🟡"}.get(severity, "🔵")
            lines.append(f"  {icon} [{ts}] **{name}** → {state} ({severity})")

        await self.reply(msg, result={"text": "\n".join(lines), "records": records})

    async def _handle_grafana_alerts(self, msg: AgentMessage, params: dict):
        """查询 Grafana 告警"""
        alerts = await self._fetch_grafana_alerts()
        if alerts is None:
            await self.reply(msg, error="Grafana 不可达或未配置 API Key")
            return

        if not alerts:
            await self.reply(msg, result={"text": "✅ Grafana 无活跃告警"})
            return

        lines = [f"📊 **Grafana 告警** ({len(alerts)} 条)"]
        for a in alerts:
            name = a.get("labels", {}).get("alertname", a.get("name", "?"))
            state = a.get("state", "?")
            icon = "🔴" if state in ("alerting", "firing") else "🟡"
            msg_text = a.get("annotations", {}).get("summary", "")
            lines.append(f"  {icon} **{name}** [{state}] {msg_text}")

        await self.reply(msg, result={"text": "\n".join(lines), "alerts": alerts})

    async def _handle_summary(self, msg: AgentMessage, params: dict):
        """综合巡检报告: Prometheus 告警 + 目标状态 + Grafana"""
        prom_alerts, prom_targets, grafana_alerts = await asyncio.gather(
            self._fetch_prometheus_alerts(),
            self._fetch_prometheus_targets(),
            self._fetch_grafana_alerts(),
        )

        sections = []
        sections.append("# 🏥 基础设施巡检报告")
        sections.append(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        sections.append("")

        if prom_alerts is not None:
            firing = [a for a in prom_alerts if a.get("state") == "firing"]
            pending = [a for a in prom_alerts if a.get("state") == "pending"]
            sections.append(f"## Prometheus 告警 ({len(firing)} firing / {len(pending)} pending)")
            if firing:
                for a in firing:
                    name = a["labels"].get("alertname", "?")
                    sev = a["labels"].get("severity", "info")
                    summary = a.get("annotations", {}).get("summary", "")
                    sections.append(f"  🔴 **{name}** [{sev}] {summary}")
            elif not pending:
                sections.append("  ✅ 无活跃告警")
            if pending:
                for a in pending:
                    name = a["labels"].get("alertname", "?")
                    sections.append(f"  🟡 {name} (pending)")
        else:
            sections.append("## Prometheus: ❌ 不可达")

        sections.append("")

        if prom_targets is not None:
            down = [t for t in prom_targets if t.get("health") != "up"]
            sections.append(f"## 采集目标 ({len(prom_targets)} 总计, {len(down)} 异常)")
            if down:
                for t in down:
                    job = t.get("labels", {}).get("job", "?")
                    sections.append(f"  🔴 `{job}` DOWN — {t.get('lastError', '?')}")
            else:
                sections.append("  ✅ 所有目标正常")
        else:
            sections.append("## 采集目标: ❌ 查询失败")

        sections.append("")

        if grafana_alerts is not None:
            active_g = [a for a in grafana_alerts if a.get("state") in ("alerting", "firing")]
            sections.append(f"## Grafana 告警 ({len(active_g)} 活跃)")
            if active_g:
                for a in active_g:
                    name = a.get("labels", {}).get("alertname", a.get("name", "?"))
                    sections.append(f"  🔴 **{name}**")
            else:
                sections.append("  ✅ 无活跃告警")
        else:
            sections.append("## Grafana: ⚠️ 未连接")

        text = "\n".join(sections)

        try:
            analysis = await self._llm_analyze(text)
            if analysis:
                text += f"\n\n## 🤖 AI 分析\n{analysis}"
        except Exception as e:
            logger.warning(f"LLM analysis skipped: {e}")

        await self.reply(msg, result={"text": text})

    async def _handle_silence(self, msg: AgentMessage, params: dict):
        """静默指定告警（暂停通知）"""
        alert_name = params.get("alertname", "")
        duration_min = params.get("duration", 60)

        if not alert_name:
            await self.reply(msg, error="请指定 alertname")
            return

        r = await self.get_redis()
        silence_key = f"rragent:monitor:silence:{alert_name}"
        await r.setex(silence_key, duration_min * 60, "1")

        await self.reply(msg, result={
            "text": f"🔇 已静默告警 **{alert_name}** {duration_min} 分钟",
        })

    async def _handle_check_cert(self, msg: AgentMessage, params: dict):
        """查询 SSL 证书到期时间（从 Prometheus blackbox_exporter 获取）"""
        domain = params.get("domain", "").strip()
        try:
            results = await self._prom_query("probe_ssl_earliest_cert_expiry")
            if results is None:
                await self.reply(msg, error="Prometheus 不可达")
                return
            if not results:
                await self.reply(msg, error="未找到 SSL 证书监控数据，请确认 blackbox_exporter 已配置")
                return

            now = time.time()
            lines = ["🔐 **SSL 证书到期检查**", ""]
            found_match = False

            for r in results:
                instance = r["metric"].get("instance", "?")
                job = r["metric"].get("job", "?")
                expiry_ts = float(r["value"][1])
                days_left = (expiry_ts - now) / 86400

                if domain and domain.lower() not in instance.lower():
                    continue
                found_match = True

                expiry_str = datetime.fromtimestamp(expiry_ts).strftime("%Y-%m-%d %H:%M")
                if days_left < 7:
                    icon, status = "🔴", "即将过期!"
                elif days_left < 30:
                    icon, status = "🟡", "需要关注"
                else:
                    icon, status = "🟢", "正常"

                lines.append(f"{icon} **{instance}** ({job})")
                lines.append(f"  到期时间: {expiry_str}")
                lines.append(f"  剩余天数: **{days_left:.0f} 天** — {status}")
                lines.append("")

            if not found_match:
                if domain:
                    lines.append(f"未找到 `{domain}` 的证书监控数据")
                    lines.append("已监控的域名:")
                    for r in results:
                        lines.append(f"  - {r['metric'].get('instance', '?')}")
                else:
                    lines.append("未找到证书监控数据")

            await self.reply(msg, result={"text": "\n".join(lines)})

        except Exception as e:
            logger.error(f"Check cert error: {e}")
            await self.reply(msg, error=f"证书查询失败: {e}")

    # ═══════════════════ NEW: PromQL Query ═══════════════════

    async def _handle_query(self, msg: AgentMessage, params: dict):
        """执行任意 PromQL 查询"""
        promql = params.get("promql", "").strip()
        if not promql:
            await self.reply(msg, error="请提供 PromQL 表达式，例如: /query up")
            return

        results = await self._prom_query(promql)
        if results is None:
            await self.reply(msg, error="Prometheus 查询失败")
            return

        if not results:
            await self.reply(msg, result={"text": f"📊 查询 `{promql}` 无结果"})
            return

        lines = [f"📊 **PromQL 查询结果** (`{promql}`)", f"共 {len(results)} 条", ""]
        for r in results[:30]:
            metric = r.get("metric", {})
            value = r.get("value", [0, "?"])
            label_str = ", ".join(f'{k}="{v}"' for k, v in metric.items() if k != "__name__")
            name = metric.get("__name__", "")
            display = f"`{name}{{{label_str}}}`" if name else f"`{{{label_str}}}`"
            lines.append(f"  {display} → **{value[1]}**")

        if len(results) > 30:
            lines.append(f"  ... 还有 {len(results) - 30} 条结果")

        await self.reply(msg, result={"text": "\n".join(lines), "results": results})

    # ═══════════════════ NEW: Metric Search ═══════════════════

    async def _handle_metrics(self, msg: AgentMessage, params: dict):
        """搜索/浏览 Prometheus 可用 metric"""
        keyword = params.get("keyword", "").strip().lower()

        data = await self._prom_get("/api/v1/label/__name__/values")
        if data is None or data.get("status") != "success":
            await self.reply(msg, error="Prometheus 不可达")
            return

        all_metrics = data.get("data", [])

        if keyword:
            matched = [m for m in all_metrics if keyword in m.lower()]
        else:
            matched = all_metrics

        if not matched:
            await self.reply(msg, result={
                "text": f"🔍 未找到包含 `{keyword}` 的 metric (共 {len(all_metrics)} 个 metric)",
            })
            return

        lines = [f"🔍 **Metric 搜索** {'`' + keyword + '`' if keyword else '(全部)'}",
                 f"匹配 {len(matched)} / {len(all_metrics)} 个", ""]

        categories: dict[str, list[str]] = {}
        for m in matched[:100]:
            prefix = m.split("_")[0] if "_" in m else "other"
            categories.setdefault(prefix, []).append(m)

        for prefix in sorted(categories):
            items = categories[prefix]
            lines.append(f"**{prefix}** ({len(items)})")
            for m in items[:10]:
                lines.append(f"  - `{m}`")
            if len(items) > 10:
                lines.append(f"  - ... 还有 {len(items) - 10} 个")
            lines.append("")

        if len(matched) > 100:
            lines.append(f"⚠️ 结果截断，仅显示前 100 个 (共 {len(matched)} 匹配)")

        await self.reply(msg, result={"text": "\n".join(lines)})

    # ═══════════════════ NEW: Host Health ═══════════════════

    async def _handle_host_health(self, msg: AgentMessage, params: dict):
        """查询指定主机的健康摘要（CPU/内存/磁盘/负载/网络）"""
        host = params.get("host", "").strip()

        if host:
            if ":" not in host:
                host_filter = f"{host}.*"
            else:
                host_filter = host
        else:
            host_filter = ".*:9100"

        queries = {}
        for name, template in HOST_QUERIES.items():
            queries[name] = self._prom_query(template.format(filter=host_filter))

        raw = {}
        keys = list(HOST_QUERIES.keys())
        results_list = await asyncio.gather(*[queries[k] for k in keys])
        for k, res in zip(keys, results_list):
            raw[k] = res

        if all(v is None for v in raw.values()):
            await self.reply(msg, error="Prometheus 不可达")
            return

        has_data = any(v for v in raw.values())
        if not has_data:
            await self.reply(msg, error=f"未找到主机 `{host or '(all)'}` 的 node_exporter 数据")
            return

        instances = set()
        for res_list in raw.values():
            if res_list:
                for r in res_list:
                    instances.add(r.get("metric", {}).get("instance", "?"))

        lines = [f"🖥️ **主机健康摘要** {'`' + host + '`' if host else '(全部 node_exporter)'}",
                 f"数据源: Prometheus | 实例: {', '.join(sorted(instances))}", ""]

        def _val(key: str, instance_filter: str = "") -> str:
            res = raw.get(key)
            if not res:
                return "N/A"
            for r in res:
                if instance_filter and instance_filter not in r.get("metric", {}).get("instance", ""):
                    continue
                return r["value"][1]
            return "N/A"

        for inst in sorted(instances):
            lines.append(f"### {inst}")
            cpu = _val("cpu", inst)
            mem = _val("memory", inst)
            disk = _val("disk", inst)
            load = _val("load1", inst)
            uptime = _val("uptime", inst)
            rx = _val("net_rx", inst)
            tx = _val("net_tx", inst)

            def _pct_icon(val_str: str) -> str:
                try:
                    v = float(val_str)
                    if v > 90:
                        return "🔴"
                    if v > 70:
                        return "🟡"
                    return "🟢"
                except (ValueError, TypeError):
                    return "⚪"

            lines.append(f"  {_pct_icon(cpu)} CPU: **{self._fmt_pct(cpu)}**")
            lines.append(f"  {_pct_icon(mem)} 内存: **{self._fmt_pct(mem)}**")
            lines.append(f"  {_pct_icon(disk)} 磁盘 /: **{self._fmt_pct(disk)}**")
            lines.append(f"  📈 Load 1m: **{self._fmt_num(load)}**")
            lines.append(f"  ⏱️ 运行天数: **{self._fmt_num(uptime)}**")
            lines.append(f"  📥 网络入: **{self._fmt_num(rx)} KB/s** | 📤 出: **{self._fmt_num(tx)} KB/s**")
            lines.append("")

        await self.reply(msg, result={"text": "\n".join(lines)})

    # ═══════════════════ NEW: Grafana Dashboards ═══════════════════

    async def _handle_grafana_dash(self, msg: AgentMessage, params: dict):
        """列出 Grafana 仪表盘或查看指定仪表盘详情"""
        dash_uid = params.get("uid", "").strip()

        if dash_uid:
            data = await self._grafana_get(f"/api/dashboards/uid/{dash_uid}")
            if data is None:
                await self.reply(msg, error=f"无法获取仪表盘 `{dash_uid}`")
                return

            dash = data.get("dashboard", {})
            title = dash.get("title", "?")
            panels = dash.get("panels", [])

            lines = [f"📊 **Grafana 仪表盘: {title}**",
                     f"UID: `{dash_uid}` | 面板数: {len(panels)}", ""]

            for p in panels:
                p_title = p.get("title", "无标题")
                p_type = p.get("type", "?")
                targets = p.get("targets", [])
                lines.append(f"  **{p_title}** (`{p_type}`)")
                for t in targets[:3]:
                    expr = t.get("expr", t.get("rawSql", ""))
                    if expr:
                        lines.append(f"    查询: `{expr[:120]}`")
                lines.append("")

            await self.reply(msg, result={"text": "\n".join(lines)})
        else:
            data = await self._grafana_get("/api/search?type=dash-db&limit=50")
            if data is None:
                await self.reply(msg, error="Grafana 不可达")
                return

            if not data:
                await self.reply(msg, result={"text": "📊 Grafana 暂无仪表盘"})
                return

            lines = [f"📊 **Grafana 仪表盘** ({len(data)} 个)", ""]
            for d in data:
                title = d.get("title", "?")
                uid = d.get("uid", "?")
                folder = d.get("folderTitle", "General")
                url_path = d.get("url", "")
                lines.append(f"  - **{title}** (`{uid}`)")
                lines.append(f"    文件夹: {folder} | URL: {GRAFANA_URL}{url_path}")

            lines.append("")
            lines.append("💡 查看详情: `/grafana_dash <uid>`")

            await self.reply(msg, result={"text": "\n".join(lines)})

    # ═══════════════════ Background Polling ═══════════════════

    async def _poll_loop(self):
        """后台定时巡检，发现新告警时主动通知"""
        await asyncio.sleep(5)
        logger.info(f"Monitor poll loop started (interval={POLL_INTERVAL}s)")

        while self._running:
            try:
                alerts = await self._fetch_prometheus_alerts()
                if alerts is not None:
                    firing = [a for a in alerts if a.get("state") == "firing"]
                    await self._process_firing_alerts(firing)

                targets = await self._fetch_prometheus_targets()
                if targets is not None:
                    down = [t for t in targets if t.get("health") != "up"]
                    if down:
                        await self._notify_down_targets(down)

            except Exception as e:
                logger.error(f"Poll loop error: {e}", exc_info=True)

            await asyncio.sleep(POLL_INTERVAL)

    async def _process_firing_alerts(self, firing: list):
        """处理正在触发的告警，新增告警时主动通知"""
        r = await self.get_redis()
        current_hashes = set()

        for a in firing:
            labels = a.get("labels", {})
            alert_id = f"{labels.get('alertname', '')}:{labels.get('instance', '')}:{labels.get('job', '')}"
            current_hashes.add(alert_id)

            if alert_id not in self._last_alert_hash:
                silence_key = f"rragent:monitor:silence:{labels.get('alertname', '')}"
                if await r.exists(silence_key):
                    continue

                record = {
                    "time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                    "alertname": labels.get("alertname", "?"),
                    "instance": labels.get("instance", "?"),
                    "job": labels.get("job", "?"),
                    "severity": labels.get("severity", "info"),
                    "state": "firing",
                    "summary": a.get("annotations", {}).get("summary", ""),
                }
                await r.lpush(ALERT_HISTORY_KEY, json.dumps(record, ensure_ascii=False))
                await r.ltrim(ALERT_HISTORY_KEY, 0, MAX_HISTORY_RECORDS - 1)

                await self._notify_new_alert(record)

        resolved = self._last_alert_hash - current_hashes
        for alert_id in resolved:
            record = {
                "time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                "alertname": alert_id.split(":")[0],
                "state": "resolved",
                "severity": "info",
            }
            await r.lpush(ALERT_HISTORY_KEY, json.dumps(record, ensure_ascii=False))
            await r.ltrim(ALERT_HISTORY_KEY, 0, MAX_HISTORY_RECORDS - 1)
            logger.info(f"Alert resolved: {alert_id}")

        self._last_alert_hash = current_hashes

    async def _notify_new_alert(self, record: dict):
        """向 orchestrator 发送新告警通知"""
        sev = record.get("severity", "info")
        icon = {"critical": "🚨", "warning": "⚠️"}.get(sev, "ℹ️")
        text = (
            f"{icon} **新告警: {record['alertname']}**\n"
            f"实例: {record.get('instance', '?')}\n"
            f"级别: {sev}\n"
            f"说明: {record.get('summary', '-')}"
        )
        logger.warning(f"NEW ALERT: {record['alertname']} [{sev}]")

        try:
            notify_ch = NOTIFY_CHANNEL
            if notify_ch:
                r = await self.get_redis()
                payload = json.dumps({
                    "text": text,
                    "source": "monitor",
                    "timestamp": time.time(),
                    "alert": record,
                }, ensure_ascii=False)
                await r.publish(notify_ch, payload)
            else:
                await self.publish(AgentMessage.create(
                    self.name, "orchestrator", "alert_notify",
                    params={"text": text, "alert": record},
                ))
        except Exception as e:
            logger.error(f"Notify error: {e}")

    async def _notify_down_targets(self, down_targets: list):
        """目标 down 时通知（去重，同一目标 10 分钟内只通知一次）"""
        r = await self.get_redis()
        for t in down_targets:
            job = t.get("labels", {}).get("job", "?")
            dedup_key = f"rragent:monitor:dedup:target:{job}"
            if await r.exists(dedup_key):
                continue
            await r.setex(dedup_key, 600, "1")

            text = f"🔴 **采集目标 DOWN**: `{job}` — {t.get('lastError', '?')}"
            logger.warning(f"TARGET DOWN: {job}")

            try:
                await self.publish(AgentMessage.create(
                    self.name, "orchestrator", "alert_notify",
                    params={"text": text},
                ))
            except Exception as e:
                logger.error(f"Target down notify error: {e}")

    # ═══════════════════ LLM Analysis ═══════════════════

    async def _llm_analyze(self, report: str) -> str | None:
        try:
            from agents.llm_router import get_llm_router
            router = get_llm_router()
            messages = [
                {"role": "system", "content": (
                    "你是一位资深 SRE / DevOps 工程师。"
                    "分析以下监控巡检报告，指出关键风险点和建议操作。"
                    "用中文简洁回复，按优先级排列，每条建议一行。"
                )},
                {"role": "user", "content": report},
            ]
            return await router.chat(messages, task_type="brief", temperature=0.3)
        except Exception as e:
            logger.error(f"LLM analyze error: {e}")
            return None

    # ═══════════════════ Formatting Helpers ═══════════════════

    @staticmethod
    def _fmt_pct(val: str) -> str:
        try:
            return f"{float(val):.1f}%"
        except (ValueError, TypeError):
            return "N/A"

    @staticmethod
    def _fmt_num(val: str) -> str:
        try:
            v = float(val)
            return f"{v:.1f}" if v != int(v) else str(int(v))
        except (ValueError, TypeError):
            return "N/A"

    @staticmethod
    def _format_alerts(firing: list, pending: list) -> str:
        if not firing and not pending:
            return "✅ 当前无活跃 Prometheus 告警"

        lines = []
        if firing:
            lines.append(f"🔴 **正在触发的告警** ({len(firing)} 条)")
            for a in firing:
                labels = a.get("labels", {})
                name = labels.get("alertname", "?")
                sev = labels.get("severity", "?")
                instance = labels.get("instance", "?")
                summary = a.get("annotations", {}).get("summary", "")
                active_at = a.get("activeAt", "?")
                lines.append(f"  🔴 **{name}** [{sev}]")
                lines.append(f"    实例: {instance}")
                lines.append(f"    说明: {summary}")
                lines.append(f"    触发时间: {active_at}")
                lines.append("")

        if pending:
            lines.append(f"🟡 **待触发告警** ({len(pending)} 条)")
            for a in pending:
                name = a.get("labels", {}).get("alertname", "?")
                lines.append(f"  🟡 {name}")

        return "\n".join(lines)

    # ═══════════════════ Lifecycle ═══════════════════

    async def start(self):
        self._running = True
        self.logger.info(f"Agent [{self.name}] starting (pid={os.getpid()})")
        await asyncio.gather(
            self._listen(),
            self._heartbeat(),
            self._poll_loop(),
        )

    async def stop(self):
        self._running = False
        await super().stop()


if __name__ == "__main__":
    run_agent(MonitorAgent())

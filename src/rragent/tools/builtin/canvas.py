"""
Canvas Tool — render interactive visualizations via Canvas/A2UI.

Sends HTML/chart data to Gateway for rendering in WebChat or other frontends.
Supports:
- Limit-up heatmaps
- Backtest equity curves
- Sector fund flow Sankey diagrams
- Portfolio risk dashboards
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from rragent.tools.base import Tool, ToolResult, ToolSpec

if TYPE_CHECKING:
    from gateway.gateway import GatewayChannel

logger = logging.getLogger("rragent.tools.builtin.canvas")


class CanvasTool(Tool):
    """
    Render visualizations through Canvas.

    The tool generates HTML/JS chart code and sends it to the
    Gateway for rendering. Supports multiple chart types.
    """

    spec = ToolSpec(
        name="canvas",
        description=(
            "Render interactive charts and visualizations. "
            "Supports: heatmap, line, bar, sankey, table, dashboard. "
            "Data is sent to the chat interface for display."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "chart_type": {
                    "type": "string",
                    "enum": ["heatmap", "line", "bar", "sankey", "table", "dashboard", "custom"],
                    "description": "Type of chart to render",
                },
                "title": {
                    "type": "string",
                    "description": "Chart title",
                },
                "data": {
                    "type": "object",
                    "description": "Chart data (format depends on chart_type)",
                },
                "options": {
                    "type": "object",
                    "description": "Additional chart options (width, height, colors, etc.)",
                    "default": {},
                },
            },
            "required": ["chart_type", "data"],
        },
        is_tier0=True,
        is_concurrent_safe=True,
        timeout=10,
        category="visualization",
    )

    def __init__(self, gateway: GatewayChannel | None = None):
        self._gateway = gateway

    async def call(self, input_data: dict) -> ToolResult:
        chart_type = input_data["chart_type"]
        title = input_data.get("title", "")
        data = input_data["data"]
        options = input_data.get("options", {})

        try:
            html = self._render(chart_type, title, data, options)

            if self._gateway:
                await self._gateway.canvas_present(html)
                return ToolResult(
                    content=f"Chart rendered: {chart_type} - {title}",
                    metadata={"chart_type": chart_type, "rendered": True},
                )
            else:
                # No gateway — return HTML for inline display
                return ToolResult(
                    content=f"```html\n{html[:2000]}\n```",
                    metadata={"chart_type": chart_type, "rendered": False},
                )
        except Exception as e:
            return ToolResult(
                content=f"Canvas render error: {e}",
                is_error=True,
            )

    def _render(self, chart_type: str, title: str, data: dict, options: dict) -> str:
        """Generate HTML for the chart."""
        width = options.get("width", "100%")
        height = options.get("height", "400px")

        renderers = {
            "heatmap": self._render_heatmap,
            "line": self._render_line,
            "bar": self._render_bar,
            "table": self._render_table,
            "sankey": self._render_sankey,
            "dashboard": self._render_dashboard,
            "custom": self._render_custom,
        }

        renderer = renderers.get(chart_type, self._render_table)
        chart_html = renderer(data, options)

        return f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{title}</title>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
    <style>
        body {{ margin: 0; padding: 8px; font-family: system-ui; }}
        #chart {{ width: {width}; height: {height}; }}
        h3 {{ margin: 0 0 8px 0; color: #333; }}
    </style>
</head>
<body>
    {"<h3>" + title + "</h3>" if title else ""}
    {chart_html}
</body>
</html>
"""

    def _render_heatmap(self, data: dict, options: dict) -> str:
        """Render a heatmap (e.g., limitup board by sector)."""
        data_json = json.dumps(data, ensure_ascii=False)
        return f"""
<div id="chart"></div>
<script>
var chart = echarts.init(document.getElementById('chart'));
var data = {data_json};
var option = {{
    tooltip: {{ position: 'top' }},
    grid: {{ top: 10, bottom: 60, left: 80 }},
    xAxis: {{ type: 'category', data: data.x_labels || [] }},
    yAxis: {{ type: 'category', data: data.y_labels || [] }},
    visualMap: {{ min: 0, max: data.max_value || 10, calculable: true }},
    series: [{{ type: 'heatmap', data: data.values || [] }}]
}};
chart.setOption(option);
</script>
"""

    def _render_line(self, data: dict, options: dict) -> str:
        """Render a line chart (e.g., equity curve)."""
        data_json = json.dumps(data, ensure_ascii=False)
        return f"""
<div id="chart"></div>
<script>
var chart = echarts.init(document.getElementById('chart'));
var data = {data_json};
var series = (data.series || []).map(function(s) {{
    return {{ type: 'line', name: s.name, data: s.values, smooth: true }};
}});
var option = {{
    tooltip: {{ trigger: 'axis' }},
    legend: {{ data: series.map(function(s) {{ return s.name; }}) }},
    xAxis: {{ type: 'category', data: data.x_labels || [] }},
    yAxis: {{ type: 'value' }},
    series: series
}};
chart.setOption(option);
</script>
"""

    def _render_bar(self, data: dict, options: dict) -> str:
        """Render a bar chart."""
        data_json = json.dumps(data, ensure_ascii=False)
        return f"""
<div id="chart"></div>
<script>
var chart = echarts.init(document.getElementById('chart'));
var data = {data_json};
var option = {{
    tooltip: {{}},
    xAxis: {{ type: 'category', data: data.labels || [] }},
    yAxis: {{ type: 'value' }},
    series: [{{ type: 'bar', data: data.values || [], itemStyle: {{ color: '#e74c3c' }} }}]
}};
chart.setOption(option);
</script>
"""

    def _render_table(self, data: dict, options: dict) -> str:
        """Render an HTML table."""
        headers = data.get("headers", [])
        rows = data.get("rows", [])

        header_html = "".join(f"<th>{h}</th>" for h in headers)
        rows_html = ""
        for row in rows[:100]:
            cells = "".join(f"<td>{c}</td>" for c in row)
            rows_html += f"<tr>{cells}</tr>"

        return f"""
<style>
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    th, td {{ border: 1px solid #ddd; padding: 6px 8px; text-align: left; }}
    th {{ background: #f5f5f5; font-weight: 600; }}
    tr:nth-child(even) {{ background: #fafafa; }}
</style>
<table>
    <thead><tr>{header_html}</tr></thead>
    <tbody>{rows_html}</tbody>
</table>
"""

    def _render_sankey(self, data: dict, options: dict) -> str:
        """Render a Sankey diagram (e.g., fund flows)."""
        data_json = json.dumps(data, ensure_ascii=False)
        return f"""
<div id="chart"></div>
<script>
var chart = echarts.init(document.getElementById('chart'));
var data = {data_json};
var option = {{
    tooltip: {{ trigger: 'item' }},
    series: [{{ type: 'sankey', data: data.nodes || [], links: data.links || [] }}]
}};
chart.setOption(option);
</script>
"""

    def _render_dashboard(self, data: dict, options: dict) -> str:
        """Render a multi-panel dashboard."""
        panels = data.get("panels", [])
        panels_html = []
        for i, panel in enumerate(panels):
            panels_html.append(
                f'<div style="flex:1;min-width:300px;padding:8px;">'
                f'<h4>{panel.get("title", "")}</h4>'
                f'<div id="panel{i}" style="height:250px;"></div>'
                f'</div>'
            )
        return f"""
<div style="display:flex;flex-wrap:wrap;">
    {"".join(panels_html)}
</div>
"""

    def _render_custom(self, data: dict, options: dict) -> str:
        """Render custom HTML."""
        return data.get("html", "<p>No content</p>")

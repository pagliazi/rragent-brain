"""
BrowserAgent — 浏览器自动化
职责: 使用 agent-browser (Rust CLI) 或 browser-use 执行浏览器任务、网页截图、URL 访问

执行链:
  1. agent-browser CLI (Rust, accessibility-tree, 快速, 支持 session 持久化)
  2. Playwright (Python, 截图/PDF 专用)
"""

import asyncio
import json
import logging
import os
import shutil
import tempfile

from agents.base import BaseAgent, AgentMessage, create_llm, run_agent

logger = logging.getLogger("agent.browser")

# agent-browser CLI 路径探测
AGENT_BROWSER_BIN = os.getenv("AGENT_BROWSER_BIN", shutil.which("agent-browser") or "")
_AB_TIMEOUT = int(os.getenv("AGENT_BROWSER_TIMEOUT", "30"))


async def _run_agent_browser(*args: str, timeout: int = _AB_TIMEOUT) -> tuple[int, str]:
    """执行 agent-browser CLI 命令"""
    if not AGENT_BROWSER_BIN:
        return -1, "agent-browser not installed"
    cmd = [AGENT_BROWSER_BIN] + list(args)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode or 0, stdout.decode(errors="replace")[:30000]
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return -1, f"agent-browser timeout ({timeout}s)"
    except Exception as e:
        return -1, str(e)


class BrowserAgent(BaseAgent):
    name = "browser"

    async def handle(self, msg: AgentMessage):
        action = msg.action
        params = msg.params

        if action == "open_url":
            await self._handle_open_url(msg, params)
        elif action == "task":
            await self._handle_task(msg, params)
        elif action == "smart_task":
            await self._handle_task(msg, params)
        elif action == "screenshot":
            await self._handle_screenshot(msg, params)
        elif action == "snapshot":
            await self._handle_snapshot(msg, params)
        elif action == "click":
            await self._handle_interact(msg, "click", params)
        elif action == "fill":
            await self._handle_interact(msg, "fill", params)
        else:
            await self.reply(msg, error=f"Unknown action: {action}")

    # ── agent-browser 优先路径 ──────────────────────────

    async def _handle_open_url(self, msg: AgentMessage, params: dict):
        url = params.get("url", "")
        if not url:
            await self.reply(msg, error="Missing url")
            return

        # 优先 agent-browser: 快速、带 accessibility snapshot
        if AGENT_BROWSER_BIN:
            code, output = await _run_agent_browser("open", url)
            if code == 0:
                # 自动取 snapshot 获取页面结构
                _, snapshot = await _run_agent_browser("snapshot", "-i", "--json")
                try:
                    snap_data = json.loads(snapshot) if snapshot.startswith("{") or snapshot.startswith("[") else {}
                except (json.JSONDecodeError, ValueError):
                    snap_data = {}
                await self.reply(msg, result={
                    "url": url, "title": snap_data.get("title", ""),
                    "snapshot": snap_data, "source": "agent-browser",
                })
                return

        # Fallback: Playwright
        screenshot = params.get("screenshot", True)
        try:
            from playwright.async_api import async_playwright
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                page = await browser.new_page(viewport={"width": 1280, "height": 800})
                await page.goto(url, wait_until="networkidle", timeout=30000)
                title = await page.title()
                result = {"title": title, "url": page.url, "source": "playwright"}
                if screenshot:
                    path = tempfile.mktemp(suffix=".png")
                    await page.screenshot(path=path, full_page=False)
                    result["screenshot_path"] = path
                await browser.close()
            await self.reply(msg, result=result)
        except Exception as e:
            logger.error(f"open_url error: {e}", exc_info=True)
            await self.reply(msg, error=str(e))

    async def _handle_task(self, msg: AgentMessage, params: dict):
        task_text = params.get("task", "")
        if not task_text:
            await self.reply(msg, error="Missing task")
            return
        max_steps = params.get("max_steps", 20)

        # 优先 agent-browser: 将 task 拆解为 CLI 命令序列
        if AGENT_BROWSER_BIN:
            result_parts = []
            # Step 1: 如果 task 包含 URL，先导航
            import re
            urls = re.findall(r'https?://\S+', task_text)
            if urls:
                code, out = await _run_agent_browser("open", urls[0])
                if code == 0:
                    result_parts.append(f"Opened {urls[0]}")

            # Step 2: 取 snapshot 了解页面
            code, snapshot = await _run_agent_browser("snapshot", "-i", "--json")
            if code == 0 and snapshot:
                result_parts.append(f"Page snapshot taken ({len(snapshot)} chars)")
                # 对于简单导航+读取任务，直接返回 snapshot
                if any(kw in task_text.lower() for kw in ("搜索", "search", "查看", "读取", "打开", "open", "fetch")):
                    await self.reply(msg, result={
                        "text": "\n".join(result_parts) + f"\n\nPage content:\n{snapshot[:8000]}",
                        "snapshot": snapshot[:8000],
                        "source": "agent-browser",
                    })
                    return

        # agent-browser 不可用或任务复杂，返回错误提示
        await self.reply(msg, error="agent-browser 不可用，请检查 AGENT_BROWSER_BIN 配置")

    async def _handle_screenshot(self, msg: AgentMessage, params: dict):
        url = params.get("url", "")
        if not url:
            await self.reply(msg, error="Missing url")
            return
        # 截图仍用 Playwright (agent-browser 主要输出 accessibility tree，不适合截图)
        try:
            from playwright.async_api import async_playwright
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                page = await browser.new_page(viewport={"width": 1280, "height": 800})
                await page.goto(url, wait_until="networkidle", timeout=30000)
                path = tempfile.mktemp(suffix=".png")
                await page.screenshot(path=path, full_page=params.get("full_page", False))
                await browser.close()
            await self.reply(msg, result={"screenshot_path": path})
        except Exception as e:
            await self.reply(msg, error=str(e))

    # ── agent-browser 新能力: 直接交互 ──────────────────

    async def _handle_snapshot(self, msg: AgentMessage, params: dict):
        """获取当前页面的 accessibility tree snapshot"""
        if not AGENT_BROWSER_BIN:
            await self.reply(msg, error="agent-browser not installed")
            return
        args = ["snapshot", "-i", "--json"]
        if params.get("selector"):
            args.extend(["-s", params["selector"]])
        if params.get("compact"):
            args.append("-c")
        code, output = await _run_agent_browser(*args)
        if code != 0:
            await self.reply(msg, error=output)
        else:
            await self.reply(msg, result={"snapshot": output, "source": "agent-browser"})

    async def _handle_interact(self, msg: AgentMessage, action_type: str, params: dict):
        """ref-based 元素交互 (click/fill)"""
        if not AGENT_BROWSER_BIN:
            await self.reply(msg, error="agent-browser not installed")
            return
        ref = params.get("ref", "")
        if not ref:
            await self.reply(msg, error=f"Missing ref for {action_type}")
            return
        args = [action_type, ref]
        if action_type == "fill" and params.get("text"):
            args.append(params["text"])
        code, output = await _run_agent_browser(*args)
        if code != 0:
            await self.reply(msg, error=output)
        else:
            await self.reply(msg, result={"text": output or f"{action_type} {ref} done", "source": "agent-browser"})


if __name__ == "__main__":
    run_agent(BrowserAgent())

"""
DesktopAgent — 桌面控制
职责: 截屏、App 控制、键鼠模拟
数据源: macOS API (screencapture, osascript)
不需要 LLM
"""

import asyncio
import logging
import os
import tempfile

from agents.base import BaseAgent, AgentMessage, run_agent

logger = logging.getLogger("agent.desktop")

SHELL_TIMEOUT = int(os.getenv("SHELL_TIMEOUT", "30"))


async def run_shell(cmd: str, timeout: int = SHELL_TIMEOUT) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode or 0, stdout.decode(errors="replace")[:4000]
    except asyncio.TimeoutError:
        return -1, f"Timeout ({timeout}s)"
    except Exception as e:
        return -1, str(e)


class DesktopAgent(BaseAgent):
    name = "desktop"

    async def handle(self, msg: AgentMessage):
        action = msg.action
        params = msg.params

        if action == "screenshot":
            path = tempfile.mktemp(suffix=".png")
            code, _ = await run_shell(f"screencapture -x {path}")
            if code != 0 or not os.path.exists(path):
                await self.reply(msg, error="Screenshot failed")
                return
            resize = params.get("resize", "800")
            if resize:
                await run_shell(f"sips --resampleWidth {resize} {path} > /dev/null 2>&1")
            await self.reply(msg, result={"screenshot_path": path})

        elif action == "shell":
            cmd = params.get("command", "")
            if not cmd:
                await self.reply(msg, error="Missing command")
                return
            timeout = params.get("timeout", SHELL_TIMEOUT)
            code, output = await run_shell(cmd, timeout=timeout)
            await self.reply(msg, result={"exit_code": code, "output": output})

        elif action == "open_app":
            app = params.get("app", "")
            if not app:
                await self.reply(msg, error="Missing app name")
                return
            code, output = await run_shell(f'open -a "{app}"')
            await self.reply(msg, result={"exit_code": code, "output": output or f"{app} opened"})

        elif action == "applescript":
            script = params.get("script", "")
            if not script:
                await self.reply(msg, error="Missing script")
                return
            code, output = await run_shell(f"osascript -e '{script}'")
            await self.reply(msg, result={"exit_code": code, "output": output})

        elif action == "type_text":
            text = params.get("text", "")
            if not text:
                await self.reply(msg, error="Missing text")
                return
            escaped = text.replace('"', '\\"')
            script = f'tell application "System Events" to keystroke "{escaped}"'
            code, output = await run_shell(f"osascript -e '{script}'")
            await self.reply(msg, result={"exit_code": code, "output": output or "Typed"})

        elif action == "key_press":
            key = params.get("key", "")
            modifiers = params.get("modifiers", [])
            if not key:
                await self.reply(msg, error="Missing key")
                return
            mod_str = ""
            if modifiers:
                mod_str = " using {" + ", ".join(f"{m} down" for m in modifiers) + "}"
            script = f'tell application "System Events" to key code {key}{mod_str}'
            code, output = await run_shell(f"osascript -e '{script}'")
            await self.reply(msg, result={"exit_code": code, "output": output or "Key pressed"})

        elif action == "click":
            x = params.get("x", 0)
            y = params.get("y", 0)
            script = (
                f'do shell script "cliclick c:{x},{y}"'
            )
            code, output = await run_shell(f"osascript -e '{script}'")
            if code != 0:
                code, output = await run_shell(
                    f'python3 -c "import Quartz; '
                    f'e=Quartz.CGEventCreateMouseEvent(None,Quartz.kCGEventLeftMouseDown,({x},{y}),0); '
                    f'Quartz.CGEventPost(Quartz.kCGHIDEventTap,e); '
                    f'e2=Quartz.CGEventCreateMouseEvent(None,Quartz.kCGEventLeftMouseUp,({x},{y}),0); '
                    f'Quartz.CGEventPost(Quartz.kCGHIDEventTap,e2)"'
                )
            await self.reply(msg, result={"exit_code": code, "output": output or f"Clicked ({x},{y})"})

        elif action == "list_windows":
            code, output = await run_shell(
                "osascript -e 'tell application \"System Events\" to get name of every process whose visible is true'"
            )
            await self.reply(msg, result={"output": output})

        else:
            await self.reply(msg, error=f"Unknown action: {action}")


if __name__ == "__main__":
    run_agent(DesktopAgent())

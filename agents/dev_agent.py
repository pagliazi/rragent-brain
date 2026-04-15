"""
DevAgent — 智能代码开发 Agent
职责:
  - 调用 Claude Code CLI 实现接近原生的 AI 编程体验
  - SSH 到 VM 执行远程命令 / 文件操作
  - 本地命令执行（安全沙箱内）
  - 自动代码审查、重构、Bug 修复、测试生成

核心能力路径:
  /claude <prompt>          → Claude Code CLI 全能开发
  /dev <instruction>        → AI 辅助开发（SSH + LLM）
  /ssh <command>            → 远程 SSH 执行
  /local <command>          → 本地命令执行
  /code_review <path>       → 代码审查
  /refactor <path> <desc>   → 代码重构
  /fix <path> <desc>        → Bug 修复
  /test <path>              → 生成测试
  /explain <path>           → 代码解释
  /deploy ...               → 前端部署

数据源: Claude Code CLI + SSH VM + DeepSeek LLM
记忆: 操作链图谱 + 语义检索历史
"""
from __future__ import annotations


import asyncio
import json
import logging
import os
import shlex
import shutil
import subprocess
from datetime import date

from agents.base import BaseAgent, AgentMessage, run_agent
from agents.memory.memory_mixin import MemoryMixin

logger = logging.getLogger("agent.dev")

VM_SSH_HOST = os.getenv("VM_SSH_HOST", "192.168.1.138")
VM_SSH_USER = os.getenv("VM_SSH_USER", "root")
SSH_TIMEOUT = int(os.getenv("SSH_TIMEOUT", "15"))
CLAUDE_BIN = os.getenv("CLAUDE_BIN", shutil.which("claude") or "/opt/homebrew/bin/claude")
CLAUDE_TIMEOUT = int(os.getenv("CLAUDE_TIMEOUT", "600"))
CLAUDE_SKIP_PERMISSIONS = os.getenv("CLAUDE_SKIP_PERMISSIONS", "1").lower() in ("1", "true", "yes")
CLAUDE_DEFAULT_MODEL = os.getenv("CLAUDE_DEFAULT_MODEL", "sonnet")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
LOCAL_ALLOWED_DIRS = os.getenv(
    "LOCAL_ALLOWED_DIRS",
    "/Users/zayl/OpenClaw-Universe",
).split(",")

OUTPUT_LIMIT = 30000
SAFE_LOCAL_COMMANDS = {"ls", "cat", "head", "tail", "wc", "find", "grep", "rg", "git", "python3", "node", "npm", "pnpm"}

SSH_HOST_PRESETS = {
    "local": {"host": "localhost", "user": "clawagent", "label": "Mac Mini (agent)"},
    "mac": {"host": "localhost", "user": "zayl", "label": "Mac Mini (zayl)"},
    "138": {"host": "192.168.1.138", "user": "root", "label": "RR-FE"},
    "139": {"host": "192.168.1.139", "user": "root", "label": "RR-BE"},
    "136": {"host": "192.168.1.136", "user": "root", "label": "Git"},
    "133": {"host": "192.168.1.133", "user": "root", "label": "Monitor"},
}

GIT_REPO_OVERRIDES: dict[str, dict] = {
    "openclaw-brain": {
        "work_dir": "/Users/clawagent/openclaw",
        "label": "OpenClaw Brain",
    },
}

SCAN_DEPTH = int(os.getenv("REPO_SCAN_DEPTH", "2"))


async def _scan_git_repos() -> dict[str, dict]:
    """动态扫描 LOCAL_ALLOWED_DIRS 下的 Git 仓库（通过 SSH 到有权限的用户执行）"""
    scan_dirs = [d.strip() for d in LOCAL_ALLOWED_DIRS if d.strip()]
    if not scan_dirs:
        return {}

    find_parts = []
    for d in scan_dirs:
        find_parts.append(f"find '{d}' -maxdepth {SCAN_DEPTH} -type d -name .git 2>/dev/null")
    find_cmd = " ; ".join(find_parts)

    current_user = os.getenv("USER", "")
    if current_user != GIT_SSH_USER:
        shell_cmd = f"ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 {GIT_SSH_USER}@{GIT_SSH_HOST} \"{find_cmd}\""
    else:
        shell_cmd = find_cmd

    try:
        proc = await asyncio.create_subprocess_shell(
            shell_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        lines = stdout.decode(errors="replace").strip().splitlines()
    except Exception as e:
        logger.warning(f"Repo scan failed: {e}")
        return {}

    repos = {}
    for line in lines:
        line = line.strip()
        if not line.endswith("/.git"):
            continue
        repo_path = line[:-5]  # remove /.git
        repo_name = os.path.basename(repo_path)
        key = repo_name.lower().replace(" ", "-")
        overrides = GIT_REPO_OVERRIDES.get(repo_name, GIT_REPO_OVERRIDES.get(key, {}))
        repos[key] = {
            "path": repo_path,
            "work_dir": overrides.get("work_dir", repo_path),
            "label": overrides.get("label", repo_name),
            "remote": overrides.get("remote", "origin"),
        }
    return repos

GIT_SSH_USER = "zayl"
GIT_SSH_HOST = "localhost"


SSH_JUMP_HOST = f"{GIT_SSH_USER}@{GIT_SSH_HOST}"


async def ssh_exec(cmd: str, timeout: int = SSH_TIMEOUT, host: str = "", user: str = "") -> tuple[int, str]:
    target_host = host or VM_SSH_HOST
    target_user = user or VM_SSH_USER
    is_local = target_host in ("localhost", "127.0.0.1")
    current_user = os.getenv("USER", "clawagent")

    if is_local and target_user == current_user:
        shell_cmd = cmd
    elif is_local:
        shell_cmd = f'ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 {target_user}@{target_host} {shlex.quote(cmd)}'
    else:
        shell_cmd = (
            f'ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 '
            f'-J {SSH_JUMP_HOST} {target_user}@{target_host} {shlex.quote(cmd)}'
        )
    try:
        proc = await asyncio.create_subprocess_shell(
            shell_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode or 0, stdout.decode(errors="replace")[:OUTPUT_LIMIT]
    except asyncio.TimeoutError:
        return -1, f"SSH timeout ({timeout}s)"
    except Exception as e:
        return -1, str(e)


async def git_exec(cmd: str, cwd: str, timeout: int = 30) -> tuple[int, str]:
    git_cmd = f"git -C '{cwd}' {cmd}"
    current_user = os.getenv("USER", "")
    if current_user != GIT_SSH_USER:
        full_cmd = f"ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 {GIT_SSH_USER}@{GIT_SSH_HOST} {shlex.quote(git_cmd)}"
    else:
        full_cmd = git_cmd
    try:
        proc = await asyncio.create_subprocess_shell(
            full_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode or 0, stdout.decode(errors="replace")[:OUTPUT_LIMIT]
    except asyncio.TimeoutError:
        return -1, f"Git timeout ({timeout}s)"
    except Exception as e:
        return -1, str(e)


CRED_PATH = os.path.expanduser("~/.claude/.credentials.json")
TOKEN_WARN_HOURS = 2


def _check_token_expiry() -> tuple[str, float]:
    """检查 OAuth token 过期状态。返回 (status, hours_remaining)。
    status: 'ok' | 'expiring_soon' | 'expired' | 'no_token'
    """
    import time as _time
    if not os.path.isfile(CRED_PATH):
        return "no_token", 0
    try:
        with open(CRED_PATH) as f:
            creds = json.load(f)
        oauth = creds.get("claudeAiOauth", {})
        exp = oauth.get("expiresAt", 0)
        if not exp:
            return "no_token", 0
        exp_s = exp / 1000 if exp > 1e12 else exp
        remaining_h = (exp_s - _time.time()) / 3600
        if remaining_h <= 0:
            return "expired", remaining_h
        if remaining_h < TOKEN_WARN_HOURS:
            return "expiring_soon", remaining_h
        return "ok", remaining_h
    except Exception:
        return "no_token", 0


async def _auto_refresh_oauth() -> tuple[bool, str]:
    """尝试自动刷新 OAuth: 启动 claude auth login + open 浏览器。
    需要用户已在浏览器中保持 claude.ai 登录状态才能自动完成。
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            CLAUDE_BIN, "auth", "login",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        import re
        output = b""
        try:
            chunk = await asyncio.wait_for(proc.stdout.read(4096), timeout=5)
            output = chunk
        except asyncio.TimeoutError:
            pass

        url_match = re.search(rb'(https://claude\.ai/oauth/authorize\S+)', output)
        if url_match:
            url = url_match.group(1).decode()
            subprocess.Popen(["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            logger.info("OAuth refresh: browser opened, waiting for authorization...")

            try:
                await asyncio.wait_for(proc.communicate(), timeout=120)
                if proc.returncode == 0:
                    logger.info("OAuth refresh succeeded")
                    return True, "OAuth token refreshed"
            except asyncio.TimeoutError:
                proc.kill()
                return False, "OAuth browser auth timed out (120s)"

        return False, "Could not extract OAuth URL from CLI"
    except Exception as e:
        return False, str(e)


def _check_claude_available() -> tuple[bool, str]:
    if not os.path.isfile(CLAUDE_BIN):
        return False, f"Claude CLI not found at {CLAUDE_BIN}"
    if ANTHROPIC_API_KEY:
        return True, "ok (API Key)"
    token_status, hours = _check_token_expiry()
    if token_status == "ok":
        return True, f"ok (OAuth, expires in {hours:.1f}h)"
    if token_status == "expiring_soon":
        return True, f"ok (OAuth, WARNING: expires in {hours:.1f}h)"
    if token_status == "expired":
        return False, f"OAuth token expired ({abs(hours):.1f}h ago). Attempting auto-refresh..."
    try:
        r = subprocess.run([CLAUDE_BIN, "auth", "status"], capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            return False, f"Claude auth failed: {r.stderr or r.stdout}"
        info = json.loads(r.stdout)
        if not info.get("loggedIn"):
            return False, "Not logged in. Set ANTHROPIC_API_KEY or run: claude auth login"
        return True, f"ok (OAuth: {info.get('email', 'unknown')})"
    except Exception as e:
        return False, str(e)


def _build_claude_cmd(
    model: str | None = None,
    max_budget: float | None = None,
    allowed_tools: str | None = None,
    work_dir: str | None = None,
    output_format: str = "json",
    session_id: str = "",
    resume: bool = False,
    permission_mode: str = "",
    skip_permissions: bool | None = None,
) -> list[str]:
    """构建 Claude Code CLI 命令行参数"""
    cmd = [CLAUDE_BIN, "-p", "--output-format", output_format]
    if output_format == "stream-json":
        cmd += ["--verbose"]
    do_skip = skip_permissions if skip_permissions is not None else CLAUDE_SKIP_PERMISSIONS
    if do_skip:
        cmd += ["--dangerously-skip-permissions"]
    elif permission_mode:
        cmd += ["--permission-mode", permission_mode]
    if model:
        cmd += ["--model", model]
    if max_budget:
        cmd += ["--max-budget-usd", str(max_budget)]
    if allowed_tools:
        cmd += ["--allowedTools", allowed_tools]
    if work_dir and os.path.isdir(work_dir):
        cmd += ["--add-dir", work_dir]
    if resume and session_id:
        cmd += ["--resume", session_id]
    elif session_id:
        cmd += ["--session-id", session_id]
    return cmd


async def claude_code_exec(
    prompt: str,
    work_dir: str | None = None,
    model: str | None = None,
    max_budget: float | None = None,
    allowed_tools: str | None = None,
    timeout: int = CLAUDE_TIMEOUT,
    output_format: str = "json",
    session_id: str = "",
    resume: bool = False,
    permission_mode: str = "",
) -> dict:
    """
    调用 Claude Code CLI 执行开发任务（-p 非交互模式，stdin 传入 prompt）。
    返回 { ok, result, cost, model, duration, session_id, raw }
    """
    cmd = _build_claude_cmd(
        model=model, max_budget=max_budget, allowed_tools=allowed_tools,
        work_dir=work_dir, output_format=output_format,
        session_id=session_id, resume=resume, permission_mode=permission_mode,
    )

    env = os.environ.copy()
    if ANTHROPIC_API_KEY:
        env["ANTHROPIC_API_KEY"] = ANTHROPIC_API_KEY
    cwd = work_dir if work_dir and os.path.isdir(work_dir) else None
    auth_mode = "api_key" if ANTHROPIC_API_KEY else "oauth"

    logger.info(f"Claude Code exec: model={model}, auth={auth_mode}, timeout={timeout}s, dir={cwd}, prompt={prompt[:120]}...")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=cwd, env=env,
        )
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(input=prompt.encode()), timeout=timeout,
        )
        stdout_s = stdout_b.decode(errors="replace")
        stderr_s = stderr_b.decode(errors="replace")

        if proc.returncode != 0:
            err_msg = stderr_s or stdout_s or f"exit code {proc.returncode}"
            logger.warning(f"Claude Code non-zero exit: {err_msg[:500]}")
            return {"ok": False, "result": err_msg[:OUTPUT_LIMIT], "cost": 0, "model": model or CLAUDE_DEFAULT_MODEL, "duration": 0, "session_id": "", "raw": {}}

        if output_format == "json":
            try:
                raw = json.loads(stdout_s)
                result_text = raw.get("result", "") or raw.get("text", "") or raw.get("content", "")
                if not result_text and isinstance(raw, dict):
                    result_text = json.dumps(raw, ensure_ascii=False, indent=2)
                return {
                    "ok": True,
                    "result": result_text[:OUTPUT_LIMIT],
                    "cost": raw.get("total_cost_usd", raw.get("cost_usd", raw.get("cost", 0))),
                    "model": raw.get("model", model or CLAUDE_DEFAULT_MODEL),
                    "duration": raw.get("duration_ms", 0) / 1000 if raw.get("duration_ms") else 0,
                    "session_id": raw.get("session_id", ""),
                    "raw": {k: v for k, v in raw.items() if k not in ("result", "text", "content")},
                }
            except json.JSONDecodeError:
                return {"ok": True, "result": stdout_s[:OUTPUT_LIMIT], "cost": 0, "model": model or CLAUDE_DEFAULT_MODEL, "duration": 0, "session_id": "", "raw": {}}
        else:
            return {"ok": True, "result": stdout_s[:OUTPUT_LIMIT], "cost": 0, "model": model or CLAUDE_DEFAULT_MODEL, "duration": 0, "session_id": "", "raw": {}}

    except asyncio.TimeoutError:
        logger.warning(f"Claude Code timeout ({timeout}s)")
        try:
            proc.kill()
        except Exception:
            pass
        return {
            "ok": False,
            "result": f"Claude Code 超时 ({timeout}s)，任务可能过于复杂。可尝试:\n- 拆分任务为更小步骤\n- 增加 --timeout\n- 指定更快的模型 --model haiku",
            "cost": 0, "model": model or CLAUDE_DEFAULT_MODEL, "duration": timeout, "session_id": "", "raw": {},
        }
    except Exception as e:
        logger.error(f"Claude Code error: {e}", exc_info=True)
        return {"ok": False, "result": str(e), "cost": 0, "model": model or CLAUDE_DEFAULT_MODEL, "duration": 0, "session_id": "", "raw": {}}


async def claude_code_stream(
    prompt: str,
    on_progress=None,
    work_dir: str | None = None,
    model: str | None = None,
    max_budget: float | None = None,
    allowed_tools: str | None = None,
    timeout: int = CLAUDE_TIMEOUT,
    session_id: str = "",
    resume: bool = False,
    permission_mode: str = "",
) -> dict:
    """
    调用 Claude Code CLI（stream-json 模式），实时回调 on_progress。
    on_progress(text: str) — 每当有新的输出片段时调用。
    最终返回与 claude_code_exec 相同格式的 dict。
    """
    cmd = _build_claude_cmd(
        model=model, max_budget=max_budget, allowed_tools=allowed_tools,
        work_dir=work_dir, output_format="stream-json",
        session_id=session_id, resume=resume, permission_mode=permission_mode,
    )

    env = os.environ.copy()
    if ANTHROPIC_API_KEY:
        env["ANTHROPIC_API_KEY"] = ANTHROPIC_API_KEY
    cwd = work_dir if work_dir and os.path.isdir(work_dir) else None

    logger.info(f"Claude Code stream: model={model}, dir={cwd}, prompt={prompt[:120]}...")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=cwd, env=env,
        )
        # 发送 prompt 并关闭 stdin
        proc.stdin.write(prompt.encode())
        await proc.stdin.drain()
        proc.stdin.close()

        collected_text = []
        final_result = {}
        last_progress_len = 0

        async def _read_stream():
            nonlocal last_progress_len, final_result
            buffer = b""
            while True:
                chunk = await proc.stdout.read(4096)
                if not chunk:
                    break
                buffer += chunk
                # stream-json: 每行一个 JSON 对象
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    line_s = line.decode(errors="replace").strip()
                    if not line_s:
                        continue
                    try:
                        event = json.loads(line_s)
                    except json.JSONDecodeError:
                        continue

                    etype = event.get("type", "")

                    if etype == "assistant" and "message" in event:
                        # 助手消息 — 提取文本片段
                        msg_content = event["message"].get("content", [])
                        for block in msg_content:
                            if block.get("type") == "text":
                                text = block.get("text", "")
                                collected_text.append(text)
                                # 每累积 200 字符回调一次进度
                                total = sum(len(t) for t in collected_text)
                                if on_progress and total - last_progress_len >= 200:
                                    last_progress_len = total
                                    try:
                                        await on_progress(f"[Claude Code] ...{text[-120:]}")
                                    except Exception:
                                        pass

                    elif etype == "result":
                        final_result = event

                    elif etype == "tool_use":
                        tool_name = event.get("tool", event.get("name", ""))
                        if on_progress and tool_name:
                            try:
                                await on_progress(f"[Claude Code] 使用工具: {tool_name}")
                            except Exception:
                                pass

        try:
            await asyncio.wait_for(_read_stream(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return {
                "ok": False,
                "result": f"Claude Code 超时 ({timeout}s)",
                "cost": 0, "model": model or CLAUDE_DEFAULT_MODEL,
                "duration": timeout, "session_id": "", "raw": {},
            }

        await proc.wait()

        if final_result:
            result_text = final_result.get("result", "") or "".join(collected_text)
            return {
                "ok": not final_result.get("is_error", False),
                "result": result_text[:OUTPUT_LIMIT],
                "cost": final_result.get("total_cost_usd", 0),
                "model": final_result.get("model", model or CLAUDE_DEFAULT_MODEL),
                "duration": final_result.get("duration_ms", 0) / 1000 if final_result.get("duration_ms") else 0,
                "session_id": final_result.get("session_id", ""),
                "raw": {k: v for k, v in final_result.items() if k not in ("result", "text", "content")},
            }
        else:
            full = "".join(collected_text)
            return {
                "ok": proc.returncode == 0,
                "result": full[:OUTPUT_LIMIT] or "(无输出)",
                "cost": 0, "model": model or CLAUDE_DEFAULT_MODEL,
                "duration": 0, "session_id": "", "raw": {},
            }

    except Exception as e:
        logger.error(f"Claude Code stream error: {e}", exc_info=True)
        return {"ok": False, "result": str(e), "cost": 0, "model": model or CLAUDE_DEFAULT_MODEL, "duration": 0, "session_id": "", "raw": {}}


class DevAgent(BaseAgent, MemoryMixin):
    name = "dev"

    def __init__(self):
        super().__init__()
        self._init_memory()
        self._last_session_id = ""

    @staticmethod
    def _resolve_action(action: str) -> str:
        KNOWN = {
            "claude_code", "claude_continue", "ssh_exec", "read_file", "write_file", "local_exec",
            "ai_dev", "code_review", "refactor", "fix_bug", "gen_test",
            "explain", "deploy_frontend", "status",
            "git_status", "git_pull", "git_log", "git_diff", "git_sync",
            "host_list", "host_test",
        }
        if action in KNOWN:
            return action
        ALIAS = {
            "claude": "claude_code", "cc": "claude_code",
            "continue": "claude_continue", "resume": "claude_continue",
            "ssh": "ssh_exec", "exec": "ssh_exec",
            "read": "read_file", "cat": "read_file",
            "write": "write_file", "edit": "write_file",
            "local": "local_exec", "shell": "local_exec",
            "dev": "ai_dev", "develop": "ai_dev", "code": "ai_dev",
            "review": "code_review", "cr": "code_review",
            "fix": "fix_bug", "bug": "fix_bug", "debug": "fix_bug",
            "test": "gen_test", "unittest": "gen_test",
            "deploy": "deploy_frontend",
            "health": "status", "check": "status",
        }
        a = action.lower().replace("-", "_")
        if a in ALIAS:
            return ALIAS[a]
        for kw, target in ALIAS.items():
            if kw in a:
                return target
        return "claude_code"

    async def handle(self, msg: AgentMessage):
        action = self._resolve_action(msg.action)
        params = msg.params

        handler_map = {
            "claude_code": self._handle_claude_code,
            "claude_continue": self._handle_claude_continue,
            "ssh_exec": self._handle_ssh_exec,
            "read_file": self._handle_read_file,
            "write_file": self._handle_write_file,
            "local_exec": self._handle_local_exec,
            "ai_dev": self._handle_ai_dev,
            "code_review": self._handle_code_review,
            "refactor": self._handle_refactor,
            "fix_bug": self._handle_fix_bug,
            "gen_test": self._handle_gen_test,
            "explain": self._handle_explain,
            "deploy_frontend": self._handle_deploy,
            "status": self._handle_status,
            "git_status": self._handle_git_status,
            "git_pull": self._handle_git_pull,
            "git_log": self._handle_git_log,
            "git_diff": self._handle_git_diff,
            "git_sync": self._handle_git_sync,
            "host_list": self._handle_host_list,
            "host_test": self._handle_host_test,
        }

        handler = handler_map.get(action)
        if handler:
            await handler(msg, params)
        else:
            await self._handle_claude_code(msg, {"prompt": params.get("prompt") or params.get("instruction") or str(params)})

    # ─── Claude Code CLI 核心 ──────────────────────────────────────────

    async def _handle_claude_code(self, msg: AgentMessage, params: dict):
        prompt = params.get("prompt", "")
        if not prompt:
            await self.reply(msg, error="需要提供开发指令")
            return

        # 非 Claude provider → 走 LLM Router
        provider = params.get("provider", "")
        if provider and provider != "claude":
            return await self._handle_llm_dev(msg, params)

        model = params.get("model", CLAUDE_DEFAULT_MODEL)
        work_dir = params.get("work_dir") or self._default_work_dir()
        max_budget = float(params.get("max_budget", 0)) or None
        allowed_tools = params.get("allowed_tools")
        timeout = int(params.get("timeout", CLAUDE_TIMEOUT))
        session_id = params.get("session_id", params.get("resume", ""))
        resume = bool(params.get("resume"))
        stream = params.get("stream", True)  # 默认开启流式
        permission_mode = params.get("permission_mode", "")

        available, reason = _check_claude_available()
        if not available:
            if "expired" in reason.lower() or "auto-refresh" in reason.lower():
                logger.info("Attempting auto OAuth refresh...")
                ok, refresh_msg = await _auto_refresh_oauth()
                if ok:
                    available, reason = True, refresh_msg
                else:
                    logger.warning(f"Auto-refresh failed: {refresh_msg}")
            if not available:
                logger.warning(f"Claude CLI unavailable: {reason}, falling back to ai_dev")
                await self._handle_ai_dev(msg, {"instruction": prompt, "target_file": params.get("target_file", "")})
                return

        # 发送启动提示
        on_progress = None
        if stream and hasattr(msg, 'progress_callback'):
            on_progress = msg.progress_callback
        elif stream:
            # 通过 reply 发送进度 (orchestrator 会转发到频道)
            async def _progress(text):
                await self.reply(msg, result={"text": text, "_progress": True})
            on_progress = _progress

        if stream and on_progress:
            await on_progress(f"[Claude Code] 开始处理: {prompt[:80]}...")
            result = await claude_code_stream(
                prompt=prompt, on_progress=on_progress,
                work_dir=work_dir, model=model,
                max_budget=max_budget, allowed_tools=allowed_tools,
                timeout=timeout, session_id=session_id, resume=resume,
                permission_mode=permission_mode,
            )
        else:
            result = await claude_code_exec(
                prompt=prompt, work_dir=work_dir, model=model,
                max_budget=max_budget, allowed_tools=allowed_tools,
                timeout=timeout, session_id=session_id, resume=resume,
                permission_mode=permission_mode,
            )

        # 保存 session_id 以便后续 resume
        sid = result.get("session_id", "")
        if sid:
            self._last_session_id = sid

        await self.remember(
            content=f"Claude Code [{model}]: {prompt[:200]}\n---\n{result['result'][:500]}",
            metadata={
                "type": "claude_code", "model": result["model"],
                "cost": result["cost"], "ok": result["ok"],
                "session_id": sid,
                "date": date.today().isoformat(),
            },
        )

        cost_str = f"${result['cost']:.4f}" if result["cost"] else ""
        dur_str = f"{result['duration']:.1f}s" if result["duration"] else ""
        meta_parts = [x for x in [result["model"], cost_str, dur_str] if x]
        meta_line = f"\n\n---\n📊 {' | '.join(meta_parts)}" if meta_parts else ""
        session_line = f" | session: `{sid[:12]}...`" if sid else ""

        await self.reply(msg, result={
            "text": result["result"] + meta_line + session_line,
            "output": result["result"],
            "model": result["model"],
            "cost_usd": result["cost"],
            "duration_s": result["duration"],
            "success": result["ok"],
            "session_id": sid,
        })

    def _default_work_dir(self) -> str:
        """默认工作目录: openclaw-brain 源码目录"""
        for d in LOCAL_ALLOWED_DIRS:
            d = d.strip()
            if d and os.path.isdir(d):
                return d
        return "/Users/zayl/OpenClaw-Universe/openclaw-brain"

    async def _handle_claude_continue(self, msg: AgentMessage, params: dict):
        """继续上一个 Claude Code 会话"""
        prompt = params.get("prompt", "继续")
        session_id = params.get("session_id", self._last_session_id)
        if not session_id:
            await self.reply(msg, error="没有可恢复的会话。请先用 /claude 开始一个开发任务。")
            return
        params["prompt"] = prompt
        params["resume"] = session_id
        params["session_id"] = session_id
        await self._handle_claude_code(msg, params)

    # ─── LLM Router 开发（非 Claude 模型）─────────────────────────────────

    async def _handle_llm_dev(self, msg: AgentMessage, params: dict):
        """通过 LLM Router 调用非 Claude 模型处理开发任务"""
        prompt = params.get("prompt", "")
        provider = params.get("provider", "")
        model = params.get("model", "")
        work_dir = params.get("work_dir") or self._default_work_dir()

        # 进度回调
        async def _progress(text):
            await self.reply(msg, result={"text": text, "_progress": True})

        await _progress(f"[{provider}/{model}] 正在处理: {prompt[:80]}...")

        # 收集文件上下文
        context = ""
        import re
        file_refs = re.findall(r'[\w./\-]+\.(?:py|js|jsx|ts|tsx|yaml|yml|json|md|sh|sql|go|rs|java|c|cpp|h)', prompt)
        if file_refs:
            for fref in file_refs[:5]:
                fpath = os.path.join(work_dir, fref) if not fref.startswith('/') else fref
                if os.path.isfile(fpath):
                    try:
                        with open(fpath, encoding='utf-8', errors='replace') as f:
                            content = f.read(50000)
                        context += f"\n\n### 文件: {fref}\n```\n{content}\n```\n"
                    except Exception:
                        pass

        system_prompt = (
            "你是一个资深全栈开发专家，精通 Python / JavaScript / TypeScript / Go / Rust 等主流语言。\n"
            "请根据用户指令分析代码并给出专业建议。输出使用 Markdown 格式。\n"
            f"工作目录: {work_dir}\n"
        )
        if self.soul:
            system_prompt = self.soul.split("\n## 错误处理")[0] + "\n\n" + system_prompt

        user_content = prompt
        if context:
            user_content += "\n\n---\n以下是相关文件内容:" + context

        import time as _time
        t0 = _time.time()
        try:
            from agents.llm_router import get_llm_router, CLOUD_PROVIDERS
            router = get_llm_router()

            # 临时切换 provider/model
            old_pinned = router._pinned
            if provider in CLOUD_PROVIDERS and model in CLOUD_PROVIDERS[provider]["models"]:
                router._pinned = (provider, model)

            reply_text = await router.chat([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ], task_type="code", temperature=0.3)

            router._pinned = old_pinned
            duration = _time.time() - t0

            text = reply_text or f"[{provider}/{model}] 无输出"
        except Exception as e:
            duration = _time.time() - t0
            self.logger.error(f"LLM dev error: {e}", exc_info=True)
            text = f"❌ {provider}/{model} 调用失败: {e}"

        meta_line = f"\n\n---\n📊 {provider}/{model} | {duration:.1f}s"

        await self.reply(msg, result={
            "text": text + meta_line,
            "output": text,
            "model": f"{provider}/{model}",
            "cost_usd": 0,
            "duration_s": duration,
            "success": not text.startswith("❌"),
        })

    # ─── 专项开发任务（Claude Code 封装）─────────────────────────────────

    async def _handle_code_review(self, msg: AgentMessage, params: dict):
        path = params.get("path", params.get("file", ""))
        focus = params.get("focus", "")
        prompt = (
            f"请对以下代码进行全面的 Code Review:\n"
            f"文件: {path}\n"
            f"{'重点关注: ' + focus if focus else ''}\n\n"
            f"请按以下格式输出:\n"
            f"## 总体评价\n(优/良/中/差 + 一句话)\n\n"
            f"## 问题列表\n(按严重程度排序: 🔴关键 🟡建议 🟢优化)\n\n"
            f"## 安全检查\n(潜在安全风险)\n\n"
            f"## 改进建议\n(具体可执行的改进)"
        )
        await self._handle_claude_code(msg, {
            "prompt": prompt,
            "work_dir": params.get("work_dir"),
            "model": params.get("model", CLAUDE_DEFAULT_MODEL),
            "allowed_tools": "Read,Bash(git:*),Grep",
        })

    async def _handle_refactor(self, msg: AgentMessage, params: dict):
        path = params.get("path", params.get("file", ""))
        description = params.get("description", params.get("instruction", ""))
        prompt = (
            f"请重构以下代码:\n"
            f"文件: {path}\n"
            f"重构需求: {description}\n\n"
            f"要求:\n"
            f"1. 保持所有现有功能不变\n"
            f"2. 确保向后兼容\n"
            f"3. 改进代码结构和可读性\n"
            f"4. 完成后运行现有测试确认无回归"
        )
        await self._handle_claude_code(msg, {
            "prompt": prompt,
            "work_dir": params.get("work_dir"),
            "model": params.get("model", CLAUDE_DEFAULT_MODEL),
        })

    async def _handle_fix_bug(self, msg: AgentMessage, params: dict):
        description = params.get("description", params.get("instruction", params.get("prompt", "")))
        path = params.get("path", params.get("file", ""))
        error_log = params.get("error", "")
        file_line = "文件: " + path if path else ""
        error_block = "错误日志:\n```\n" + error_log + "\n```" if error_log else ""
        prompt = (
            f"请修复以下 Bug:\n"
            f"{file_line}\n"
            f"问题描述: {description}\n"
            f"{error_block}\n\n"
            f"要求:\n"
            f"1. 定位根因\n"
            f"2. 实施修复\n"
            f"3. 说明修复原理\n"
            f"4. 确认不引入新问题"
        )
        await self._handle_claude_code(msg, {
            "prompt": prompt,
            "work_dir": params.get("work_dir"),
            "model": params.get("model", CLAUDE_DEFAULT_MODEL),
        })

    async def _handle_gen_test(self, msg: AgentMessage, params: dict):
        path = params.get("path", params.get("file", ""))
        framework = params.get("framework", "")
        prompt = (
            f"请为以下代码生成全面的单元测试:\n"
            f"文件: {path}\n"
            f"{'测试框架: ' + framework if framework else '自动检测项目使用的测试框架'}\n\n"
            f"要求:\n"
            f"1. 覆盖正常路径和边界条件\n"
            f"2. 包含错误处理测试\n"
            f"3. 使用有意义的测试名称\n"
            f"4. 测试写完后运行确认全部通过"
        )
        await self._handle_claude_code(msg, {
            "prompt": prompt,
            "work_dir": params.get("work_dir"),
            "model": params.get("model", CLAUDE_DEFAULT_MODEL),
        })

    async def _handle_explain(self, msg: AgentMessage, params: dict):
        path = params.get("path", params.get("file", params.get("code", "")))
        prompt = (
            f"请详细解释以下代码的功能和工作原理:\n"
            f"{'文件: ' + path if path else '代码: ' + str(params)}\n\n"
            f"请包含:\n"
            f"1. 总体功能概述\n"
            f"2. 核心逻辑流程\n"
            f"3. 关键数据结构\n"
            f"4. 依赖关系\n"
            f"5. 潜在改进点"
        )
        await self._handle_claude_code(msg, {
            "prompt": prompt,
            "work_dir": params.get("work_dir"),
            "model": params.get("model", "haiku"),
            "allowed_tools": "Read,Grep",
        })

    # ─── SSH 远程操作 ──────────────────────────────────────────────────

    async def _handle_ssh_exec(self, msg: AgentMessage, params: dict):
        cmd = params.get("command", "")
        if not cmd:
            await self.reply(msg, error="Missing command")
            return
        host_key = params.get("host", "")
        preset = SSH_HOST_PRESETS.get(host_key, {})
        target_host = preset.get("host") or params.get("ip", VM_SSH_HOST)
        target_user = preset.get("user") or params.get("user", VM_SSH_USER)

        timeout = int(params.get("timeout", SSH_TIMEOUT))
        code, output = await ssh_exec(cmd, timeout=timeout, host=target_host, user=target_user)
        await self.remember(
            content=f"SSH [{target_user}@{target_host}]: {cmd}\n[exit:{code}] {output[:500]}",
            metadata={"type": "ssh_exec", "command": cmd[:200], "exit_code": code,
                      "host": target_host, "date": date.today().isoformat()},
        )
        await self.reply(msg, result={"exit_code": code, "output": output, "host": f"{target_user}@{target_host}"})

    async def _handle_read_file(self, msg: AgentMessage, params: dict):
        path = params.get("path", "")
        if not path:
            await self.reply(msg, error="Missing path")
            return
        code, content = await ssh_exec(f"cat '{path}'")
        if code != 0:
            await self.reply(msg, error=content)
        else:
            await self.reply(msg, result={"content": content, "path": path})

    async def _handle_write_file(self, msg: AgentMessage, params: dict):
        path = params.get("path", "")
        content = params.get("content", "")
        if not path:
            await self.reply(msg, error="Missing path")
            return
        escaped = content.replace("'", "'\\''")
        code, output = await ssh_exec(f"cat > '{path}' << 'OPENCLAW_EOF'\n{escaped}\nOPENCLAW_EOF")
        if code != 0:
            await self.reply(msg, error=output)
        else:
            await self.remember(
                content=f"Write file: {path}\n{content[:500]}",
                metadata={"type": "write_file", "path": path, "date": date.today().isoformat()},
            )
            await self.reply(msg, result={"path": path, "written": True})

    # ─── 本地命令执行（安全限制）──────────────────────────────────────

    async def _handle_local_exec(self, msg: AgentMessage, params: dict):
        cmd = params.get("command", "")
        if not cmd:
            await self.reply(msg, error="Missing command")
            return

        base_cmd = cmd.split()[0] if cmd.split() else ""
        if base_cmd not in SAFE_LOCAL_COMMANDS:
            await self.reply(msg, error=f"命令 `{base_cmd}` 不在安全白名单中。允许: {', '.join(sorted(SAFE_LOCAL_COMMANDS))}")
            return

        try:
            proc = await asyncio.create_subprocess_shell(
                cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                cwd=LOCAL_ALLOWED_DIRS[0] if LOCAL_ALLOWED_DIRS else None,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
            output = stdout.decode(errors="replace")[:OUTPUT_LIMIT]
            await self.reply(msg, result={"exit_code": proc.returncode or 0, "output": output})
        except asyncio.TimeoutError:
            await self.reply(msg, error="Local command timeout (30s)")
        except Exception as e:
            await self.reply(msg, error=str(e))

    # ─── AI Dev（LLM 降级方案）────────────────────────────────────────

    async def _handle_ai_dev(self, msg: AgentMessage, params: dict):
        instruction = params.get("instruction", "")
        if not instruction:
            await self.reply(msg, error="Missing instruction")
            return

        available, _ = _check_claude_available()
        if available:
            await self._handle_claude_code(msg, {"prompt": instruction, "work_dir": params.get("work_dir")})
            return

        # Claude CLI 不可用 — 使用 LLM Router (云端模型) 作为降级方案
        logger.info("[ai_dev] Claude CLI unavailable, using LLM Router fallback")

        context_parts = []
        try:
            code, files = await ssh_exec("find /root -name '*.py' -newer /tmp/.openclaw_mark 2>/dev/null | head -20 || ls /root/*.py 2>/dev/null | head -10")
            if files.strip():
                context_parts.append(f"VM ({VM_SSH_HOST}) 文件列表:\n{files}")
        except Exception:
            pass

        target_file = params.get("target_file", "")
        if target_file:
            try:
                _, content = await ssh_exec(f"cat '{target_file}'")
                context_parts.append(f"\n目标文件 {target_file} 内容:\n{content}")
            except Exception:
                pass

        history_context = ""
        try:
            memories = await self.recall(instruction, top_k=3, cross_agent=True)
            reminds = await self.get_reminds(max_items=2)
            if memories or reminds:
                history_context = self.format_recall_context(memories, reminds=reminds) + "\n\n"
        except Exception:
            pass

        system_prompt = (
            "你是一个资深全栈开发专家。请根据用户指令分析并生成需要执行的命令或代码。\n"
            "如果需要执行 SSH 命令，每条命令一行，用 --- 分隔。\n"
            "如果需要修改文件，用 cat > file << 'EOF' 格式。\n"
            "如果是代码分析/解释类任务，直接给出专业分析。\n"
            "输出使用 Markdown 格式。"
        )
        if self.soul:
            system_prompt = self.soul.split("\n## 错误处理")[0] + "\n\n" + system_prompt

        user_prompt = ""
        if history_context:
            user_prompt += history_context
        if context_parts:
            user_prompt += "".join(context_parts) + "\n\n"
        user_prompt += f"用户指令: {instruction}"

        try:
            from agents.llm_router import get_llm_router
            router = get_llm_router()
            reply_text = await router.chat([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ], task_type="code", temperature=0.3)

            if not reply_text:
                await self.reply(msg, error="LLM Router 无输出")
                return

            plan_text = reply_text
            commands = [c.strip() for c in plan_text.replace("---", "\n").strip().split("\n") if c.strip() and not c.strip().startswith("#")]

            plan_mem_id = await self.remember(
                content=f"AI Dev Plan: {instruction}\n---\n{plan_text[:1000]}",
                metadata={"type": "ai_dev_plan", "instruction": instruction[:200], "date": date.today().isoformat()},
            )

            if params.get("auto_execute", False) and commands:
                outputs = []
                prev_mem_id = plan_mem_id
                for cmd_item in commands[:10]:
                    rc, out = await ssh_exec(cmd_item, timeout=30)
                    outputs.append(f"$ {cmd_item}\n[exit:{rc}] {out}")
                    cmd_mem_id = await self.remember(
                        content=f"$ {cmd_item}\n[exit:{rc}] {out[:500]}",
                        metadata={"type": "ai_dev_exec", "command": cmd_item[:200], "exit_code": rc, "date": date.today().isoformat()},
                    )
                    if cmd_mem_id and prev_mem_id:
                        await self.connect(prev_mem_id, cmd_mem_id, "causal")
                    prev_mem_id = cmd_mem_id
                    if rc != 0:
                        break
                await self.reply(msg, result={"plan": plan_text, "executed": True, "outputs": "\n\n".join(outputs)})
            else:
                await self.reply(msg, result={"text": plan_text, "plan": plan_text, "executed": False})
        except Exception as e:
            logger.error(f"AI dev LLM Router error: {e}", exc_info=True)
            await self.reply(msg, error=f"AI Dev 降级失败: {e}")

    # ─── 部署 ─────────────────────────────────────────────────────────

    async def _handle_deploy(self, msg: AgentMessage, params: dict):
        skip_build = params.get("skip_build", False)
        prompt = (
            f"帮我部署前端项目到生产环境。\n"
            f"项目路径: /Users/zayl/OpenClaw-Universe/ReachRich/frontend\n"
            f"{'跳过构建步骤，直接部署' if skip_build else '先构建再部署'}\n\n"
            f"步骤: 1) git pull 2) {'skip' if skip_build else 'pnpm build'} 3) 部署到服务器 4) 验证"
        )
        available, _ = _check_claude_available()
        if available:
            await self._handle_claude_code(msg, {"prompt": prompt, "work_dir": "/Users/zayl/OpenClaw-Universe/ReachRich/frontend"})
        else:
            await self._handle_ai_dev(msg, {"instruction": prompt})

    # ─── Git 操作 ─────────────────────────────────────────────────────

    async def _resolve_repo(self, params: dict) -> tuple[str, str]:
        repo_key = params.get("repo", "")
        if not hasattr(self, "_cached_repos") or not self._cached_repos:
            self._cached_repos = await _scan_git_repos()
        if repo_key in self._cached_repos:
            preset = self._cached_repos[repo_key]
            return preset["path"], preset["label"]
        path = params.get("path", "").strip()
        if path:
            rc, _ = await git_exec("rev-parse --is-inside-work-tree", path)
            if rc == 0:
                return path, os.path.basename(path)
        first = next(iter(self._cached_repos.values()), None)
        if first:
            return first["path"], first["label"]
        return "/Users/zayl/OpenClaw-Universe/openclaw-brain", "OpenClaw Brain"

    async def _handle_git_status(self, msg: AgentMessage, params: dict):
        repo_path, label = await self._resolve_repo(params)
        rc, out = await git_exec("status --short --branch", repo_path)
        if rc != 0:
            await self.reply(msg, error=f"git status failed in {repo_path}: {out}")
            return
        rc2, stash = await git_exec("stash list", repo_path)
        rc3, remote = await git_exec("remote -v", repo_path, timeout=5)
        await self.reply(msg, result={
            "text": f"📂 {label} ({repo_path})\n\n{out}" + (f"\n\n📦 Stash:\n{stash}" if stash.strip() else ""),
            "repo": repo_path,
            "label": label,
            "remotes": remote if rc3 == 0 else "",
        })

    async def _handle_git_pull(self, msg: AgentMessage, params: dict):
        repo_path, label = await self._resolve_repo(params)
        branch = params.get("branch", "")
        remote = params.get("remote", "origin")
        cmd = f"pull {remote} {branch}" if branch else f"pull {remote}"
        rc, out = await git_exec(cmd, repo_path, timeout=60)
        status_icon = "✅" if rc == 0 else "❌"
        await self.reply(msg, result={
            "text": f"{status_icon} git pull {label}\n\n{out}",
            "success": rc == 0,
        })

    async def _handle_git_log(self, msg: AgentMessage, params: dict):
        repo_path, label = await self._resolve_repo(params)
        count = params.get("count", 15)
        rc, out = await git_exec(f"log --oneline --graph --decorate -n {count}", repo_path)
        await self.reply(msg, result={"text": f"📜 {label} 最近 {count} 次提交\n\n{out}"})

    async def _handle_git_diff(self, msg: AgentMessage, params: dict):
        repo_path, label = await self._resolve_repo(params)
        target = params.get("target", "")
        cmd = f"diff {target}" if target else "diff --stat"
        rc, out = await git_exec(cmd, repo_path)
        await self.reply(msg, result={"text": f"📊 {label} diff\n\n{out if out.strip() else '无改动'}"})

    async def _handle_git_sync(self, msg: AgentMessage, params: dict):
        repo_path, label = await self._resolve_repo(params)
        deploy_target = params.get("deploy_to", "")
        steps = []
        rc, out = await git_exec("pull origin", repo_path, timeout=60)
        steps.append(f"1. git pull: {'✅' if rc == 0 else '❌'}\n{out}")
        if rc != 0:
            await self.reply(msg, result={"text": f"❌ {label} 同步失败\n\n" + "\n".join(steps), "success": False})
            return
        rc2, log = await git_exec("log --oneline -3", repo_path)
        steps.append(f"2. 最新提交:\n{log}")

        if deploy_target:
            preset = SSH_HOST_PRESETS.get(deploy_target, {})
            if preset:
                deploy_host = preset["host"]
                deploy_user = preset["user"]
                deploy_path = params.get("deploy_path", repo_path)
                rsync_cmd = f"rsync -avz --exclude '.git' --exclude '__pycache__' --exclude 'node_modules' --exclude '.venv' {repo_path}/ {deploy_user}@{deploy_host}:{deploy_path}/"
                try:
                    proc = await asyncio.create_subprocess_shell(
                        rsync_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                    )
                    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
                    rsync_out = stdout.decode(errors="replace")[:2000]
                    steps.append(f"3. rsync → {preset['label']}: {'✅' if proc.returncode == 0 else '❌'}\n{rsync_out}")
                except Exception as e:
                    steps.append(f"3. rsync → {preset['label']}: ❌ {e}")

        await self.reply(msg, result={"text": f"🔄 {label} 同步完成\n\n" + "\n".join(steps), "success": True})

    # ─── 主机管理 ──────────────────────────────────────────────────────

    async def _handle_host_list(self, msg: AgentMessage, params: dict):
        results = []
        for key, preset in SSH_HOST_PRESETS.items():
            results.append({"key": key, **preset})
        discovered = await _scan_git_repos()
        self._cached_repos = discovered
        await self.reply(msg, result={"hosts": results, "repos": [{"key": k, **v} for k, v in discovered.items()]})

    async def _handle_host_test(self, msg: AgentMessage, params: dict):
        host_key = params.get("host", "")
        preset = SSH_HOST_PRESETS.get(host_key, {})
        target_host = preset.get("host") or params.get("ip", host_key)
        target_user = preset.get("user") or params.get("user", "root")
        label = preset.get("label", target_host)

        rc, out = await ssh_exec("echo ok && uname -a && uptime", timeout=10, host=target_host, user=target_user)
        if rc == 0:
            await self.reply(msg, result={"text": f"✅ {label} ({target_user}@{target_host}) 连接正常\n\n{out}", "connected": True})
        else:
            await self.reply(msg, result={"text": f"❌ {label} ({target_user}@{target_host}) 连接失败\n\n{out}", "connected": False})

    # ─── 状态检查 ──────────────────────────────────────────────────────

    async def _handle_status(self, msg: AgentMessage, params: dict):
        claude_ok, claude_msg = _check_claude_available()
        ssh_code, ssh_out = await ssh_exec("echo ok", timeout=5)

        host_statuses = {}
        for key, preset in SSH_HOST_PRESETS.items():
            rc, _ = await ssh_exec("echo ok", timeout=5, host=preset["host"], user=preset["user"])
            host_statuses[key] = {"label": preset["label"], "host": preset["host"], "user": preset["user"], "connected": rc == 0}

        discovered = await _scan_git_repos()
        self._cached_repos = discovered

        repo_statuses = {}
        for key, preset in discovered.items():
            rc_check, _ = await git_exec("rev-parse --is-inside-work-tree", preset["path"])
            if rc_check == 0:
                rc, branch = await git_exec("rev-parse --abbrev-ref HEAD", preset["path"])
                rc2, dirty = await git_exec("status --porcelain", preset["path"])
                repo_statuses[key] = {
                    "label": preset["label"], "path": preset["path"],
                    "work_dir": preset.get("work_dir", preset["path"]),
                    "branch": branch.strip() if rc == 0 else "unknown",
                    "dirty": bool(dirty.strip()) if rc2 == 0 else None,
                    "exists": True,
                }
            else:
                repo_statuses[key] = {"label": preset["label"], "path": preset["path"],
                                      "work_dir": preset.get("work_dir", preset["path"]), "exists": False}

        status = {
            "claude_code": {"available": claude_ok, "detail": claude_msg, "bin": CLAUDE_BIN, "auth_mode": "api_key" if ANTHROPIC_API_KEY else "oauth"},
            "ssh": {"available": ssh_code == 0, "host": f"{VM_SSH_USER}@{VM_SSH_HOST}"},
            "hosts": host_statuses,
            "repos": repo_statuses,
            "scan_dirs": LOCAL_ALLOWED_DIRS,
        }
        await self.reply(msg, result=status)


if __name__ == "__main__":
    run_agent(DevAgent())

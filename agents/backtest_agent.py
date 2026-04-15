"""
BacktestAgent — 量化回测沙箱引擎
职责: 接收策略代码 → 远程沙箱执行 → 返回标准化回测指标
执行: SSH 投递代码到 quant_runner@192.168.1.139 /opt/quant_sandbox
回退: 本地 subprocess (当 SSH 不可用时)
安全: 静态扫描 + 远程五层沙箱 + 超时
"""

import asyncio
import hashlib
import json
import logging
import os
import subprocess
import time
from datetime import date, datetime
from pathlib import Path

from agents.base import BaseAgent, AgentMessage, run_agent

logger = logging.getLogger("agent.backtest")

WORKSPACE = Path(os.getenv("BACKTEST_WORKSPACE",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "workspace", "strategies")))
LEDGER_DIR = Path(os.getenv("DECISION_LEDGER_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "decision_ledger")))

BACKTEST_TIMEOUT = int(os.getenv("BACKTEST_TIMEOUT", "1200"))

SSH_HOST = os.getenv("BRIDGE_SSH_HOST", "192.168.1.139")
SSH_USER = os.getenv("BRIDGE_SSH_USER", "quant_runner")
SSH_KEY = os.getenv("BRIDGE_SSH_KEY", "/Users/clawagent/.ssh/id_ed25519")
SANDBOX_PYTHON = os.getenv("BRIDGE_SANDBOX_PYTHON", "/opt/quant_sandbox/venv/bin/python")
SANDBOX_DIR = os.getenv("BRIDGE_SANDBOX_DIR", "/opt/quant_sandbox")

FORBIDDEN_IMPORTS = {
    "os.system", "subprocess", "shutil.rmtree", "shutil.move",
    "__import__", "eval(", "exec(", "compile(",
    "socket", "http.server", "ftplib", "smtplib",
    "ctypes", "pathlib", "importlib",
    "urllib", "http.client",
}

BANNED_PATTERNS = [
    "while True", "while 1:", "__import__",
    "open(", "os.popen", "os.remove",
]


class BacktestAgent(BaseAgent):
    name = "backtest"

    async def handle(self, msg: AgentMessage):
        action = msg.action
        params = msg.params

        if action == "run_backtest":
            await self._handle_run_backtest(msg, params)
        elif action == "validate_code":
            await self._handle_validate_code(msg, params)
        elif action == "quant_validate":
            await self._handle_quant_validate(msg, params)
        elif action == "list_cache":
            await self._handle_list_cache(msg)
        elif action == "save_ledger":
            await self._handle_save_ledger(msg, params)
        elif action == "list_ledger":
            await self._handle_list_ledger(msg)
        elif action == "list_strategies":
            await self._handle_list_strategies(msg)
        elif action == "get_strategy":
            await self._handle_get_strategy(msg, params)
        else:
            await self.reply(msg, error=f"Unknown action: {action}")

    # ── 量化研究质量验证 (quantitative-research 集成) ──

    def _quant_quality_check(self, metrics: dict, code: str = "", start_date: str = "", end_date: str = "") -> dict:
        """
        基于 quantitative-research skill 的 sharp_edges 和 validations，
        对回测结果进行质量评估和过拟合风险检测。
        """
        warnings = []
        risk_score = 0  # 0-10, 越高风险越大

        sharpe = metrics.get("sharpe", 0)
        cagr = metrics.get("cagr", 0)
        max_dd = abs(metrics.get("max_drawdown", 0))
        win_rate = metrics.get("win_rate", 0)
        trades = metrics.get("trades", 0)

        # 1. 过高 Sharpe 警告 (sharp_edge: 大多数 >3 的 Sharpe 都是过拟合)
        if sharpe > 3.0:
            warnings.append(f"⚠️ Sharpe={sharpe:.2f} 异常高，大概率过拟合或存在前视偏差")
            risk_score += 3
        elif sharpe > 2.0:
            warnings.append(f"⚡ Sharpe={sharpe:.2f} 偏高，建议 walk-forward 验证")
            risk_score += 1

        # 2. 交易次数过少 (统计不显著)
        if trades < 30:
            warnings.append(f"⚠️ 交易次数={trades} 过少，结果统计不���著 (建议≥30)")
            risk_score += 2
        elif trades < 100:
            warnings.append(f"⚡ 交易次数={trades}，统计置信度有限")
            risk_score += 1

        # 3. 回测时间太短
        if start_date and end_date:
            try:
                from datetime import datetime as _dt
                s = _dt.strptime(start_date[:10], "%Y-%m-%d")
                e = _dt.strptime(end_date[:10], "%Y-%m-%d")
                days = (e - s).days
                if days < 365:
                    warnings.append(f"⚠️ 回测周期={days}天 过短，建议≥2年以覆盖不同市场阶段")
                    risk_score += 2
                elif days < 730:
                    warnings.append(f"⚡ 回测周期={days}天，建议延长以验证策略鲁棒性")
                    risk_score += 1
            except Exception:
                pass

        # 4. CAGR 与回撤比 (risk-adjusted return)
        if max_dd > 0:
            calmar = cagr / max_dd if max_dd > 0 else 0
            if calmar < 0.5:
                warnings.append(f"⚠️ Calmar={calmar:.2f}，收益不足以补偿回撤风险")
                risk_score += 1

        # 5. 胜率异常
        if win_rate > 0.8:
            warnings.append(f"⚡ 胜率={win_rate:.0%} 异常高，检查是否有幸存者偏差")
            risk_score += 1

        # 6. 代码中的过拟合信号
        if code:
            code_lower = code.lower()
            # 参数过多 (>5 个硬编码数字可能是过拟合)
            import re as _re
            magic_numbers = _re.findall(r'(?<!=)\b\d+\.\d+\b', code)
            if len(magic_numbers) > 8:
                warnings.append(f"⚡ 代码含 {len(magic_numbers)} 个魔术数字，过拟合风险高")
                risk_score += 2
            # 条件过多
            if_count = code_lower.count("if ") + code_lower.count("elif ")
            if if_count > 15:
                warnings.append(f"⚡ 条件分支={if_count}，策略可能过度拟合历史模式")
                risk_score += 1

        # 综合评级
        if risk_score >= 5:
            grade = "D (高过拟合风险)"
        elif risk_score >= 3:
            grade = "C (需要进一步验证)"
        elif risk_score >= 1:
            grade = "B (基本合理，建议扩展验证)"
        else:
            grade = "A (指标健康)"

        return {
            "grade": grade,
            "risk_score": risk_score,
            "warnings": warnings,
            "recommendations": self._quant_recommendations(risk_score, warnings),
        }

    @staticmethod
    def _quant_recommendations(risk_score: int, warnings: list) -> list[str]:
        """基于风险评分生成改进建议"""
        recs = []
        if risk_score >= 3:
            recs.append("进行 walk-forward 验证 (2年训练+3月测试，滚动前进)")
        if risk_score >= 5:
            recs.append("检查是否存在前视偏差 (look-ahead bias)")
            recs.append("在不同市场阶段 (牛市/熊市/震荡) 分别验证")
        if any("交易次数" in w for w in warnings):
            recs.append("增加回测时间范围或扩大股票池以提高样本量")
        if any("魔术数字" in w for w in warnings):
            recs.append("将硬编���参数提取为可配置变量，用网格搜索验证稳健性")
        if not recs:
            recs.append("策略基本面合理，建议小仓位纸面交易验证")
        return recs

    async def _handle_quant_validate(self, msg: AgentMessage, params: dict):
        """量化研究质量验证 — 对已有回测结果进行深度审计"""
        metrics = params.get("metrics", {})
        code = params.get("code", "")
        start_date = params.get("start_date", "")
        end_date = params.get("end_date", "")

        if not metrics:
            await self.reply(msg, error="缺少 metrics 参数")
            return

        result = self._quant_quality_check(metrics, code, start_date, end_date)

        # 格式化输出
        lines = [f"🔬 量化质量评估: {result['grade']}", ""]
        if result["warnings"]:
            lines.append("**风险警告:**")
            lines.extend(f"  {w}" for w in result["warnings"])
            lines.append("")
        if result["recommendations"]:
            lines.append("**改进建议:**")
            lines.extend(f"  {i+1}. {r}" for i, r in enumerate(result["recommendations"]))

        await self.reply(msg, result={"text": "\n".join(lines), "validation": result})

    def _check_code_safety(self, code: str) -> tuple[bool, str]:
        for forbidden in FORBIDDEN_IMPORTS:
            if forbidden in code:
                return False, f"禁止使用: {forbidden}"
        for pattern in BANNED_PATTERNS:
            if pattern in code:
                return False, f"检测到危险模式: {pattern}"
        if code.count("while ") > 5:
            return False, "循环过多，可能存在无限循环"
        return True, ""

    _ssh_ok: bool | None = None
    _ssh_check_ts: float = 0
    _SSH_CACHE_TTL = 300

    def _ssh_available(self) -> bool:
        """快速探测 SSH 是否可连 (结果缓存 300s)"""
        now = time.time()
        if self._ssh_ok is not None and now - self._ssh_check_ts < self._SSH_CACHE_TTL:
            return self._ssh_ok
        try:
            result = subprocess.run(
                ["ssh", "-i", SSH_KEY, "-o", "ConnectTimeout=2",
                 "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes",
                 f"{SSH_USER}@{SSH_HOST}", "echo ok"],
                capture_output=True, text=True, timeout=4,
            )
            self._ssh_ok = result.returncode == 0 and "ok" in result.stdout
        except Exception:
            self._ssh_ok = False
        self._ssh_check_ts = now
        if not self._ssh_ok:
            logger.info("SSH to %s@%s unavailable, will use fallback", SSH_USER, SSH_HOST)
        return self._ssh_ok

    async def _run_remote(self, code: str, stocks: str, start_date: str, end_date: str, strategy_params: dict | None = None) -> dict:
        """SSH 投递代码到远程沙箱执行（支持多票 --stocks 参数）"""
        code_hash = hashlib.sha256(code.encode()).hexdigest()[:12]
        remote_file = f"{SANDBOX_DIR}/bt_{int(time.time())}_{code_hash}.py"

        ssh_base = [
            "ssh", "-i", SSH_KEY,
            "-o", "ConnectTimeout=10",
            "-o", "StrictHostKeyChecking=no",
            "-o", "BatchMode=yes",
            f"{SSH_USER}@{SSH_HOST}",
        ]

        is_multi = "," in stocks
        stock_arg = f"--stocks {stocks}" if is_multi else f"--stock {stocks}"
        params_arg = ""
        if strategy_params:
            params_json = json.dumps(strategy_params, ensure_ascii=False)
            params_arg = f" --params '{params_json}'"

        try:
            write_proc = await asyncio.to_thread(
                subprocess.run,
                ssh_base + [f"cat > {remote_file}"],
                input=code, capture_output=True, text=True, timeout=30,
            )
            if write_proc.returncode != 0:
                return {"status": "error", "message": f"代码投递失败: {write_proc.stderr[:500]}"}

            exec_cmd = f"timeout {BACKTEST_TIMEOUT} {SANDBOX_PYTHON} {remote_file} {stock_arg} --start {start_date} --end {end_date}{params_arg}"

            result = await asyncio.to_thread(
                subprocess.run,
                ssh_base + [exec_cmd],
                capture_output=True, text=True,
                timeout=BACKTEST_TIMEOUT + 30,
            )

            await asyncio.to_thread(
                subprocess.run,
                ssh_base + [f"rm -f {remote_file}"],
                capture_output=True, text=True, timeout=10,
            )

            if result.returncode != 0:
                err = result.stderr[-1000:] if result.stderr else "未知错误"
                return {"status": "error", "message": f"远程执行失败:\n{err}", "returncode": result.returncode}

            return self._parse_backtest_output(result.stdout, stocks, start_date, end_date)

        except subprocess.TimeoutExpired:
            try:
                await asyncio.to_thread(
                    subprocess.run, ssh_base + [f"rm -f {remote_file}"],
                    capture_output=True, text=True, timeout=10,
                )
            except Exception:
                pass
            return {"status": "timeout", "message": f"远程回测超时 (>{BACKTEST_TIMEOUT}s)"}
        except Exception as e:
            return {"status": "error", "message": f"SSH 执行异常: {e}"}

    async def _run_bridge_api_vectorbt(self, code: str, start_date: str, end_date: str, timeout: int = 60, strategy_params: dict | None = None) -> dict:
        """通过 Bridge API 执行 vectorbt 全市场回测（直连 ClickHouse，无需 --stock）"""
        try:
            from agents.bridge_client import get_bridge_client
            client = get_bridge_client()
            resp = await client.run_backtest(
                code=code, stock="",
                start_date=start_date, end_date=end_date,
                timeout=timeout, mode="vectorbt",
                strategy_params=strategy_params or None,
            )
            if resp is None:
                return {"status": "error", "message": "Bridge API vectorbt 回测接口不可用"}
            logger.info("Bridge API vectorbt response keys: %s", list(resp.keys()) if isinstance(resp, dict) else type(resp))
            return self._normalize_bridge_result(resp)
        except Exception as e:
            return {"status": "error", "message": f"Bridge API vectorbt 调用失败: {e}"}

    async def _run_bridge_api(self, code: str, stocks: str, start_date: str, end_date: str, strategy_params: dict | None = None) -> dict:
        """通过 Bridge API POST 执行回测"""
        try:
            from agents.bridge_client import get_bridge_client
            client = get_bridge_client()
            resp = await client.run_backtest(
                code=code, stock=stocks,
                start_date=start_date, end_date=end_date,
                strategy_params=strategy_params or None,
            )
            if resp is None:
                return {"status": "error", "message": "Bridge API 回测接口不可用"}
            logger.info("Bridge API backtest raw response keys: %s", list(resp.keys()) if isinstance(resp, dict) else type(resp))
            return self._normalize_bridge_result(resp)
        except Exception as e:
            return {"status": "error", "message": f"Bridge API 调用失败: {e}"}

    @staticmethod
    def _normalize_bridge_result(resp: dict) -> dict:
        """将 139 服务器的回测响应归一化为内部标准格式。

        139 API 返回格式 (见 api-manual 5.1):
          成功: {"success": true, "metrics": {...}, "code_hash": "...", "run_id": "..."}
          失败: {"success": false, "error": "...", "code_hash": "..."}
        """
        if not isinstance(resp, dict):
            return {"status": "error", "message": f"Bridge 返回非 dict: {str(resp)[:500]}"}

        stdout = resp.get("stdout", "") or resp.get("output", "")
        stderr = resp.get("stderr", "")

        if "success" in resp:
            if resp["success"]:
                metrics = resp.get("metrics", {})
                return {"status": "success", "metrics": metrics, "stdout": str(stdout)[:2000]}
            else:
                err = resp.get("error", "") or resp.get("detail", "") or resp.get("message", "执行失败")
                parts = [str(err)]
                if stderr:
                    parts.append(f"stderr:\n{stderr[-1500:]}")
                if stdout:
                    parts.append(f"stdout:\n{stdout[-1500:]}")
                return {"status": "error", "message": "\n".join(parts)}

        if "status" in resp and resp["status"] in ("success", "error", "timeout", "no_metrics"):
            if resp["status"] == "error":
                msg = resp.get("message", "")
                if stderr and stderr not in msg:
                    resp["message"] = f"{msg}\nstderr:\n{stderr[-1500:]}"
                elif stdout and stdout not in msg:
                    resp["message"] = f"{msg}\nstdout:\n{stdout[-1500:]}"
            return resp

        metrics = resp.get("metrics") or resp.get("data") or resp.get("result")
        error = resp.get("error") or resp.get("detail") or resp.get("message")

        if error and not metrics:
            return {"status": "error", "message": str(error), "stdout": str(stdout)[:2000]}

        if isinstance(metrics, dict) and any(k in metrics for k in ("cagr", "sharpe", "max_drawdown", "win_rate", "total_return")):
            return {"status": "success", "metrics": metrics, "stdout": str(stdout)[:2000]}

        return {
            "status": "error",
            "message": f"Bridge 返回未识别格式: {json.dumps(resp, ensure_ascii=False, default=str)[:800]}",
        }

    async def _run_local(self, code: str, stocks: str, start_date: str, end_date: str) -> dict:
        """本地 subprocess 回测 (最终回退)"""
        WORKSPACE.mkdir(parents=True, exist_ok=True)
        cache_dir = Path(os.getenv("HISTORY_CACHE_DIR",
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "history_cache")))
        cache_dir.mkdir(parents=True, exist_ok=True)

        prelude = _LOCAL_SANDBOX_PRELUDE.format(cache_dir=str(cache_dir))
        full_code = prelude + "\n\n# ── 用户策略代码 ──\n" + code
        full_code += f'\n\n# ── 自动注入参数 ──\nSTOCK = "{stocks}"\nSTART = "{start_date}"\nEND = "{end_date}"\n'

        strategy_file = WORKSPACE / f"bt_{int(time.time())}.py"
        strategy_file.write_text(full_code, encoding="utf-8")

        try:
            venv_python = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".venv", "bin", "python")
            python_cmd = venv_python if os.path.exists(venv_python) else "python3"

            result = await asyncio.to_thread(
                subprocess.run,
                [python_cmd, str(strategy_file)],
                capture_output=True, text=True,
                timeout=min(BACKTEST_TIMEOUT, 90),
                cwd=str(WORKSPACE),
            )

            if result.returncode != 0:
                err = result.stderr[-1000:] if result.stderr else "未知错误"
                return {"status": "error", "message": f"本地执行失败:\n{err}", "returncode": result.returncode}

            return self._parse_backtest_output(result.stdout, stocks, start_date, end_date)

        except subprocess.TimeoutExpired:
            return {"status": "timeout", "message": "本地回测超时"}
        except Exception as e:
            return {"status": "error", "message": f"本地执行异常: {e}"}
        finally:
            try:
                strategy_file.unlink(missing_ok=True)
            except Exception:
                pass

    def _parse_backtest_output(self, stdout: str, stock: str, start_date: str, end_date: str) -> dict:
        """解析回测 stdout，提取 BACKTEST_RESULT JSON 或最后一行 JSON"""
        metrics = None
        output_lines = []

        for line in stdout.split("\n"):
            stripped = line.strip()
            if stripped.startswith("BACKTEST_RESULT:"):
                try:
                    metrics = json.loads(stripped[16:])
                except json.JSONDecodeError:
                    pass
            else:
                output_lines.append(line)

        if not metrics:
            for line in reversed(stdout.strip().split("\n")):
                stripped = line.strip()
                if stripped.startswith("{") and stripped.endswith("}"):
                    try:
                        candidate = json.loads(stripped)
                        if isinstance(candidate, dict) and any(k in candidate for k in ("cagr", "total_return", "max_drawdown", "win_rate")):
                            metrics = candidate
                            break
                    except json.JSONDecodeError:
                        continue

        if metrics:
            return {
                "status": "success",
                "metrics": metrics,
                "stdout": "\n".join(output_lines[-30:]),
                "stock": stock,
                "period": f"{start_date} ~ {end_date}",
            }
        else:
            return {
                "status": "no_metrics",
                "message": "代码执行成功但未输出标准化指标",
                "stdout": stdout[-2000:],
            }

    async def _handle_run_backtest(self, msg: AgentMessage, params: dict):
        code = params.get("code", "")
        stocks = params.get("stocks", params.get("stock", "000001"))
        start_date = params.get("start_date", "2024-01-01")
        end_date = params.get("end_date", date.today().isoformat())
        mode = params.get("mode", "backtrader")
        strategy_params = params.get("strategy_params", {})

        if not code:
            await self.reply(msg, error="策略代码为空")
            return

        safe, reason = self._check_code_safety(code)
        if not safe:
            await self.reply(msg, error=f"安全检查未通过: {reason}")
            return

        code_hash = hashlib.sha256(code.encode()).hexdigest()

        result = None
        exec_mode = "unknown"

        if mode == "vectorbt":
            timeout_override = 60
            logger.info("vectorbt mode: sending to Bridge API (code_hash=%s)", code_hash[:12])
            exec_mode = "bridge_api_vectorbt"
            result = await self._run_bridge_api_vectorbt(code, start_date, end_date, timeout_override, strategy_params=strategy_params)
        elif self._ssh_available():
            exec_mode = "remote_ssh"
            logger.info("backtrader SSH mode (code_hash=%s, stocks=%d)", code_hash[:12], len(stocks.split(",")))
            result = await self._run_remote(code, stocks, start_date, end_date, strategy_params=strategy_params)
        else:
            logger.info("SSH unavailable, trying Bridge API for backtest")
            exec_mode = "bridge_api"
            result = await self._run_bridge_api(code, stocks, start_date, end_date, strategy_params=strategy_params)

            if result.get("status") == "error" and "不可用" in result.get("message", ""):
                exec_mode = "local"
                logger.info("Bridge API unavailable, falling back to local subprocess")
                result = await self._run_local(code, stocks, start_date, end_date)

        result["exec_mode"] = exec_mode
        result["code_hash"] = code_hash
        result["mode"] = mode

        # 自动量化质量检查 (quantitative-research 集成)
        if result.get("status") == "success" and result.get("metrics"):
            try:
                qc = self._quant_quality_check(
                    result["metrics"], code, start_date, end_date
                )
                result["quant_validation"] = qc
                if qc["warnings"]:
                    result["quality_warnings"] = qc["warnings"]
            except Exception as e:
                logger.debug("quant quality check failed: %s", e)

        if result.get("status") == "success":
            await self.reply(msg, result=result)
        elif result.get("status") in ("error", "timeout", "no_metrics"):
            await self.reply(msg, result=result)
        else:
            await self.reply(msg, error=result.get("message", "未知回测错误"))

    async def _handle_validate_code(self, msg: AgentMessage, params: dict):
        code = params.get("code", "")
        safe, reason = self._check_code_safety(code)
        try:
            compile(code, "<strategy>", "exec")
            syntax_ok = True
            syntax_err = ""
        except SyntaxError as e:
            syntax_ok = False
            syntax_err = str(e)

        await self.reply(msg, result={
            "safe": safe, "safety_reason": reason,
            "syntax_ok": syntax_ok, "syntax_error": syntax_err,
        })

    async def _handle_list_cache(self, msg: AgentMessage):
        try:
            from agents.bridge_client import get_bridge_client
            client = get_bridge_client()
            resp = await client.get_kline("", period="daily", limit=0)
            if resp:
                await self.reply(msg, result={"text": f"📦 Bridge 缓存: {resp}", "raw": resp})
                return
        except Exception:
            pass

        cache_dir = Path(os.getenv("HISTORY_CACHE_DIR",
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "history_cache")))
        cache_dir.mkdir(parents=True, exist_ok=True)
        files = list(cache_dir.glob("*.parquet"))
        stocks = [f.stem for f in files]
        total_mb = sum(f.stat().st_size for f in files) / 1024 / 1024
        await self.reply(msg, result={
            "text": f"📦 本地缓存: {len(stocks)} 只股票, {total_mb:.1f}MB\n{', '.join(stocks[:50])}",
            "count": len(stocks), "size_mb": round(total_mb, 1),
        })

    async def _handle_save_ledger(self, msg: AgentMessage, params: dict):
        title = params.get("title", "未命名策略")
        content = params.get("content", "")
        status = params.get("status", "PENDING")
        metrics = params.get("metrics", {})
        code = params.get("code", "")
        attempts = params.get("attempts", 1)
        rounds_data = params.get("rounds_data")

        try:
            from agents.bridge_client import get_bridge_client
            client = get_bridge_client()
            resp = await client.save_strategy(
                title=title,
                topic=params.get("topic", title),
                status=status,
                strategy_code=code,
                backtest_metrics=metrics,
                decision_report=content,
                attempts=attempts,
                rounds_data=rounds_data,
            )
            if resp and resp.get("id"):
                await self.reply(msg, result={
                    "text": f"📝 策略已归档到 ReachRich (ID: {resp['id']})",
                    "ledger_id": resp["id"],
                    "storage": "bridge",
                })
                return
        except Exception as e:
            logger.warning("Bridge ledger save failed, falling back to local: %s", e)

        LEDGER_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_title = "".join(c if c.isalnum() or c in "._-" else "_" for c in title)[:50]
        filename = f"{ts}_{safe_title}.md"

        metrics_text = ""
        if metrics:
            metrics_text = "\n".join(f"* **{k}**: {v}" for k, v in metrics.items())

        code_hash = hashlib.sha256(code.encode()).hexdigest() if code else ""

        md = f"""# 策略研发记录：{title}
**日期**: {datetime.now().strftime('%Y-%m-%d %H:%M')}
**状态**: {'🟢 已通过 (APPROVE)' if status == 'APPROVE' else '🔴 废弃 (REJECT)' if status == 'REJECT' else '🟡 待定 (PENDING)'}
**迭代次数**: {attempts} 轮
**代码哈希**: {code_hash[:16]}

## 分析过程
{content}

## 量化指标
{metrics_text if metrics_text else '无回测数据'}

## 策略源码
```python
{code if code else '# 无代码'}
```
"""
        ledger_path = LEDGER_DIR / filename
        ledger_path.write_text(md, encoding="utf-8")
        await self.reply(msg, result={
            "text": f"📝 决策账本已保存: {filename}",
            "path": str(ledger_path),
            "storage": "local",
        })

    async def _handle_list_ledger(self, msg: AgentMessage):
        try:
            from agents.bridge_client import get_bridge_client
            client = get_bridge_client()
            resp = await client.get_ledger()
            if resp and isinstance(resp, (list, dict)):
                items = resp if isinstance(resp, list) else resp.get("items", resp.get("results", resp.get("data", [])))
                if items:
                    lines = ["📝 决策账本 (Bridge):"]
                    for item in items[:20]:
                        s = item.get("status", "?")
                        icon = "🟢" if s == "APPROVE" else "🔴" if s == "REJECT" else "🟡"
                        lines.append(f"  {icon} [{item.get('id','')}] {item.get('title','')} ({item.get('created_at','')[:10]})")
                    await self.reply(msg, result={"text": "\n".join(lines), "source": "bridge"})
                    return
        except Exception:
            pass

        LEDGER_DIR.mkdir(parents=True, exist_ok=True)
        files = sorted(LEDGER_DIR.glob("*.md"), reverse=True)[:20]
        if not files:
            await self.reply(msg, result={"text": "📝 决策账本为空"})
            return
        lines = ["📝 决策账本 (本地, 最近20条):"]
        for f in files:
            first_line = f.read_text(encoding="utf-8").split("\n")[0]
            lines.append(f"  {f.stem}: {first_line}")
        await self.reply(msg, result={"text": "\n".join(lines), "source": "local"})

    _presets_cache: list = []
    _presets_cache_ts: float = 0
    _PRESETS_CACHE_TTL = 120

    async def _fetch_presets(self) -> list:
        """从 Bridge API 获取策略预设列表 (缓存 120s)"""
        now = time.time()
        if self._presets_cache and now - self._presets_cache_ts < self._PRESETS_CACHE_TTL:
            return self._presets_cache
        try:
            from agents.bridge_client import get_bridge_client
            client = get_bridge_client()
            resp = await client.get_presets()
            if resp and isinstance(resp, dict):
                presets = resp.get("presets", [])
                if presets:
                    self._presets_cache = presets
                    self._presets_cache_ts = now
                    return presets
        except Exception as e:
            logger.warning("Failed to fetch presets from Bridge: %s", e)
        return self._presets_cache

    @staticmethod
    def _preset_to_strategy(preset: dict) -> dict:
        """将 Bridge preset 映射为前端策略卡片格式"""
        payload = preset.get("payload", {})
        parameters = payload.get("parameters", {})
        param_summary = {}
        for k, v in parameters.items():
            if isinstance(v, dict) and "default" in v:
                param_summary[k] = v["default"]
            else:
                param_summary[k] = v

        filters = payload.get("filters", {})
        filter_count = len(filters.get("children", []))

        return {
            "id": str(preset.get("id", "")),
            "title": preset.get("name", ""),
            "slug": preset.get("slug", ""),
            "description": preset.get("description", ""),
            "category": preset.get("category", ""),
            "created_at": str(preset.get("created_at", ""))[:16],
            "parameters": param_summary,
            "filter_count": filter_count,
            "is_active": preset.get("is_active", True),
            "access_level": preset.get("access_level", 1),
            "source": "bridge",
            "type": "preset",
        }

    async def _handle_list_strategies(self, msg: AgentMessage):
        """返回结构化策略列表 (JSON) 供前端渲染"""
        strategies = []

        presets = await self._fetch_presets()
        for p in presets:
            strategies.append(self._preset_to_strategy(p))

        LEDGER_DIR.mkdir(parents=True, exist_ok=True)
        files = sorted(LEDGER_DIR.glob("*.md"), reverse=True)[:20]
        for f in files:
            parsed = self._parse_ledger_md(f)
            if parsed:
                strategies.append(parsed)

        source = "bridge" if presets else "local"
        await self.reply(msg, result={"strategies": strategies, "source": source})

    async def _handle_get_strategy(self, msg: AgentMessage, params: dict):
        """获取单个策略的完整详情"""
        strategy_id = params.get("id", "")
        if not strategy_id:
            await self.reply(msg, error="缺少策略 ID")
            return

        presets = await self._fetch_presets()
        for p in presets:
            if str(p.get("id")) == strategy_id or p.get("slug") == strategy_id:
                payload = p.get("payload", {})
                parameters = payload.get("parameters", {})
                filters = payload.get("filters", {})
                outputs = payload.get("outputs", {})

                filter_lines = []
                for child in filters.get("children", []):
                    left = child.get("left", {}).get("factor", "?")
                    op = child.get("operator", "?")
                    right = child.get("right", {})
                    if isinstance(right, dict):
                        right_val = right.get("param", right.get("factor", "?"))
                    else:
                        right_val = str(right)
                    filter_lines.append(f"  {left} {op} {right_val}")

                filter_desc = "\n".join(filter_lines) if filter_lines else "无"

                param_lines = []
                for k, v in parameters.items():
                    if isinstance(v, dict):
                        param_lines.append(f"  {k}: default={v.get('default')}, range=[{v.get('min')}, {v.get('max')}], type={v.get('type')}")
                    else:
                        param_lines.append(f"  {k}: {v}")
                param_desc = "\n".join(param_lines) if param_lines else "无"

                report = (
                    f"策略名称: {p.get('name')}\n"
                    f"分类: {p.get('category')}\n"
                    f"描述: {p.get('description')}\n\n"
                    f"筛选条件 ({len(filter_lines)} 个):\n{filter_desc}\n\n"
                    f"可调参数 ({len(parameters)} 个):\n{param_desc}\n\n"
                    f"输出: 最多 {outputs.get('limit', '?')} 只, 排序: {outputs.get('order_by', [])}\n"
                    f"股票池: {payload.get('universe', {}).get('mode', 'all')}, 排除: {payload.get('universe', {}).get('exclude', [])}\n"
                )

                await self.reply(msg, result={
                    "id": str(p.get("id")),
                    "title": p.get("name", ""),
                    "slug": p.get("slug", ""),
                    "description": p.get("description", ""),
                    "category": p.get("category", ""),
                    "parameters": parameters,
                    "filters": filters,
                    "outputs": outputs,
                    "universe": payload.get("universe", {}),
                    "report": report,
                    "created_at": str(p.get("created_at", ""))[:16],
                    "source": "bridge",
                    "type": "preset",
                })
                return

        LEDGER_DIR.mkdir(parents=True, exist_ok=True)
        for f in LEDGER_DIR.glob("*.md"):
            if strategy_id in f.stem:
                content = f.read_text(encoding="utf-8")
                code = self._extract_code_from_md(content)
                metrics = self._extract_metrics_from_md(content)
                status = "APPROVE" if "已通过" in content else ("REJECT" if "废弃" in content else "PENDING")
                first_line = content.split("\n")[0].replace("# 策略研发记录：", "").strip()
                await self.reply(msg, result={
                    "id": f.stem,
                    "title": first_line,
                    "description": first_line,
                    "status": status,
                    "code": code,
                    "metrics": metrics,
                    "report": content,
                    "created_at": f.stem[:15],
                    "source": "local",
                    "type": "ledger",
                })
                return

        await self.reply(msg, error=f"策略 {strategy_id} 未找到")

    def _parse_ledger_md(self, filepath: Path) -> dict | None:
        """解析本地 Markdown 账本文件为结构化数据"""
        try:
            content = filepath.read_text(encoding="utf-8")
            title = content.split("\n")[0].replace("# 策略研发记录：", "").replace("# ", "").strip()
            status = "APPROVE" if "已通过" in content else ("REJECT" if "废弃" in content else "PENDING")
            metrics = self._extract_metrics_from_md(content)
            has_code = "```python" in content
            return {
                "id": filepath.stem,
                "title": title,
                "topic": title,
                "status": status,
                "created_at": filepath.stem[:15].replace("_", " "),
                "metrics": metrics,
                "attempts": 0,
                "has_code": has_code,
                "source": "local",
            }
        except Exception:
            return None

    @staticmethod
    def _extract_code_from_md(content: str) -> str:
        """从 Markdown 中提取 python 代码块"""
        import re
        match = re.search(r"```python\s*\n(.*?)\n```", content, re.DOTALL)
        if match:
            code = match.group(1).strip()
            if code != "# 无代码":
                return code
        return ""

    @staticmethod
    def _extract_metrics_from_md(content: str) -> dict:
        """从 Markdown 量化指标段落提取指标"""
        import re
        metrics = {}
        section_match = re.search(r"## 量化指标\s*\n(.*?)(?=\n##|\Z)", content, re.DOTALL)
        if section_match:
            for line in section_match.group(1).split("\n"):
                m = re.match(r"\*\s*\*\*(\w+)\*\*:\s*(.*)", line.strip())
                if m:
                    key, val = m.group(1), m.group(2).strip()
                    try:
                        metrics[key] = float(val)
                    except ValueError:
                        metrics[key] = val
        return metrics


_LOCAL_SANDBOX_PRELUDE = '''
import sys, os
for mod in ["subprocess", "shutil", "socket", "http", "ftplib", "smtplib", "ctypes"]:
    sys.modules[mod] = None
os.system = None
os.popen = None
os.remove = None
os.rmdir = None

import json
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

CACHE_DIR = "{cache_dir}"

def load_stock_data(code, start=None, end=None):
    cache_file = os.path.join(CACHE_DIR, f"{{code}}.parquet")
    if os.path.exists(cache_file):
        df = pd.read_parquet(cache_file)
    else:
        try:
            import akshare as ak
            df = ak.stock_zh_a_hist(symbol=code, period="daily", adjust="qfq")
            os.makedirs(CACHE_DIR, exist_ok=True)
            df.to_parquet(cache_file)
        except Exception as e:
            print(json.dumps({{"status":"error","message":f"数据加载失败: {{e}}"}}))
            sys.exit(0)
    if "日期" in df.columns:
        df = df.rename(columns={{"日期":"date","开盘":"open","收盘":"close","最高":"high","最低":"low","成交量":"volume","涨跌幅":"pct_chg"}})
    df["date"] = pd.to_datetime(df["date"])
    if start:
        df = df[df["date"] >= start]
    if end:
        df = df[df["date"] <= end]
    return df.reset_index(drop=True)

def output_metrics(metrics):
    print("BACKTEST_RESULT:" + json.dumps(metrics, ensure_ascii=False, default=str))
'''


if __name__ == "__main__":
    run_agent(BacktestAgent())

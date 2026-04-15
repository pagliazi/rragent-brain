"""
IntradayAgent — 盘中实时监控 Agent

职责:
1. 接收 orchestrator 的 intraday_scan / intraday_status 命令
2. 交易时段自动启动定时扫描循环
3. DolphinDB 实时数据 → 信号检测 → Telegram/飞书推送
4. Redis 维护目标股票池、信号记录、扫描日志

数据架构:
  盘后: vectorbt + ClickHouse 全市场回测 → Redis rragent:daily_pool
  盘中: DolphinDB 1.17亿行实时数据 → Bridge API intraday/scan → 信号推送
"""
from __future__ import annotations


import asyncio
import json
import logging
import os
import time

from agents.base import BaseAgent, AgentMessage, run_agent

logger = logging.getLogger("agent.intraday")

AUTO_SCAN_INTERVAL = int(os.getenv("INTRADAY_SCAN_INTERVAL", "120"))
AUTO_SCAN_ENABLED = os.getenv("INTRADAY_AUTO_SCAN", "1") == "1"


class IntradayAgent(BaseAgent):
    name = "intraday"

    def __init__(self):
        super().__init__()
        self._auto_scan_task: asyncio.Task | None = None
        self._monitoring = False
        self._last_scan_result: dict = {}

    async def handle(self, msg: AgentMessage):
        action = msg.action
        params = msg.params

        if action == "scan":
            result = await self._do_scan(
                stock_pool=params.get("stock_pool", []),
                strategy_logic=params.get("strategy_logic", ""),
            )
            await self.reply(msg, result=result)

        elif action == "get_status":
            result = await self._get_status()
            await self.reply(msg, result=result)

        elif action == "get_pool":
            result = await self._get_pool()
            await self.reply(msg, result=result)

        elif action == "start_monitor":
            result = await self._start_auto_monitor(
                strategy_logic=params.get("strategy_logic", ""),
                interval=params.get("interval", AUTO_SCAN_INTERVAL),
            )
            await self.reply(msg, result=result)

        elif action == "stop_monitor":
            result = self._stop_auto_monitor()
            await self.reply(msg, result=result)

        else:
            await self.reply(msg, error=f"Unknown action: {action}")

    async def _do_scan(self, stock_pool: list[str] = None, strategy_logic: str = "") -> dict:
        """执行单次盘中扫描"""
        from agents.market_time import is_trading_hours, get_market_phase
        from agents.intraday_pipeline import run_intraday_scan, get_daily_pool
        import redis.asyncio as aioredis

        if not is_trading_hours():
            phase = get_market_phase()
            return {"text": f"当前非交易时段 ({phase.value})，盘中扫描仅在 09:30~15:00 运行", "signals": []}

        if not stock_pool:
            r = await self.get_redis()
            pool_data = await get_daily_pool(r)
            stock_pool = pool_data.get("stocks", [])

        if not stock_pool:
            return {"text": "⚠️ 目标股票池为空，请先执行盘后选股 (/intraday_select)", "signals": []}

        result = await run_intraday_scan(
            self, stock_pool, strategy_logic or "检测目标股票池盘中入场信号",
            notify_fn=self._push_signal,
        )

        self._last_scan_result = result
        signals = result.get("signals", [])
        scanned = result.get("scanned", 0)

        if signals:
            text = f"⚡ 盘中扫描完成: {len(signals)} 只信号 / {scanned} 只扫描\n"
            for sig in signals[:10]:
                text += f"  🎯 {sig.get('ts_code', '?')} @ {sig.get('price', '?')} [{sig.get('reason', '')}]\n"
        else:
            text = f"扫描完成: {scanned} 只股票，暂无入场信号"

        return {"text": text, "signals": signals, "scanned": scanned}

    async def _get_status(self) -> dict:
        """获取盘中监控完整状态"""
        from agents.intraday_pipeline import get_intraday_status
        r = await self.get_redis()
        status = await get_intraday_status(r)
        status["monitoring"] = self._monitoring
        status["auto_scan_interval"] = AUTO_SCAN_INTERVAL

        from agents.market_time import get_market_phase, is_trading_hours
        status["market_phase"] = get_market_phase().value
        status["is_trading_hours"] = is_trading_hours()

        text_parts = [f"📡 盘中监控状态"]
        text_parts.append(f"  监控: {'🟢 运行中' if self._monitoring else '🔴 未启动'}")
        text_parts.append(f"  市场: {status['market_phase']} ({'交易中' if status['is_trading_hours'] else '非交易'})")
        text_parts.append(f"  目标池: {status['pool']['count']} 只 ({status['pool']['strategy'] or '无策略'})")
        text_parts.append(f"  间隔: {AUTO_SCAN_INTERVAL}s")

        if status['recent_signals']:
            text_parts.append(f"  最近信号: {len(status['recent_signals'])} 条")

        status["text"] = "\n".join(text_parts)
        return status

    async def _get_pool(self) -> dict:
        """获取当前目标股票池"""
        from agents.intraday_pipeline import get_daily_pool
        r = await self.get_redis()
        pool = await get_daily_pool(r)

        if not pool.get("stocks"):
            return {"text": "目标股票池为空，请先执行盘后选股"}

        stocks = pool["stocks"]
        text = f"📋 目标股票池 ({len(stocks)} 只)\n"
        text += f"策略: {pool.get('strategy_name', '-')}\n"
        text += f"日期: {pool.get('date', '-')}\n"
        text += f"股票: {', '.join(stocks[:30])}"
        if len(stocks) > 30:
            text += f"... +{len(stocks) - 30} 只"

        return {"text": text, "pool": pool}

    async def _start_auto_monitor(self, strategy_logic: str = "", interval: int = AUTO_SCAN_INTERVAL) -> dict:
        """启动自动定时扫描"""
        from agents.market_time import is_trading_hours

        if self._monitoring:
            return {"text": "盘中监控已在运行中"}

        if not is_trading_hours():
            from agents.market_time import get_market_phase
            phase = get_market_phase()
            return {"text": f"当前非交易时段 ({phase.value})，无法启动盘中监控"}

        self._monitoring = True
        self._auto_scan_task = asyncio.create_task(
            self._auto_scan_loop(strategy_logic, interval)
        )
        return {"text": f"🟢 盘中自动监控已启动 (间隔 {interval}s)"}

    def _stop_auto_monitor(self) -> dict:
        """停止自动定时扫描"""
        if not self._monitoring:
            return {"text": "盘中监控未在运行"}

        self._monitoring = False
        if self._auto_scan_task and not self._auto_scan_task.done():
            self._auto_scan_task.cancel()
        return {"text": "🔴 盘中自动监控已停止"}

    async def _auto_scan_loop(self, strategy_logic: str, interval: int):
        """自动扫描循环：交易时段内每 interval 秒扫描一次"""
        from agents.market_time import is_trading_hours
        logger.info("Auto-scan loop started (interval=%ds)", interval)

        scan_count = 0
        try:
            while self._monitoring:
                if not is_trading_hours():
                    logger.info("Trading hours ended, stopping auto-scan (total scans: %d)", scan_count)
                    break

                scan_count += 1
                logger.info("Auto-scan #%d", scan_count)

                try:
                    await self._do_scan(strategy_logic=strategy_logic)
                except Exception as e:
                    logger.error("Auto-scan error: %s", e)

                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            logger.info("Auto-scan loop cancelled")
        finally:
            self._monitoring = False
            logger.info("Auto-scan loop ended (total scans: %d)", scan_count)

    async def _push_signal(self, text: str):
        """推送信号到通知渠道"""
        try:
            r = await self.get_redis()
            payload = json.dumps({
                "type": "intraday_signal",
                "text": text,
                "timestamp": time.time(),
                "source": "intraday",
            }, ensure_ascii=False)
            await r.publish("rragent:notifications", payload)
        except Exception as e:
            logger.error("Signal push failed: %s", e)

    async def start(self):
        """重写 start: 除了监听消息和心跳，交易时段自动启动扫描"""
        self._running = True
        self.logger.info(f"Agent [{self.name}] starting (pid={os.getpid()})")

        tasks = [
            self._listen(),
            self._heartbeat(),
        ]

        if AUTO_SCAN_ENABLED:
            tasks.append(self._trading_hours_watcher())

        await asyncio.gather(*tasks)

    async def _trading_hours_watcher(self):
        """监视交易时段：进入交易时段自动启动监控，离开时段自动停止"""
        from agents.market_time import is_trading_hours
        logger.info("Trading hours watcher started")

        while self._running:
            try:
                if is_trading_hours() and not self._monitoring:
                    from agents.intraday_pipeline import get_daily_pool
                    r = await self.get_redis()
                    pool = await get_daily_pool(r)
                    if pool.get("stocks"):
                        logger.info("Trading hours detected + pool available, starting auto-monitor")
                        self._monitoring = True
                        self._auto_scan_task = asyncio.create_task(
                            self._auto_scan_loop("", AUTO_SCAN_INTERVAL)
                        )
                    else:
                        logger.debug("Trading hours but no pool, skipping auto-monitor")

                elif not is_trading_hours() and self._monitoring:
                    logger.info("Trading hours ended, stopping auto-monitor")
                    self._stop_auto_monitor()
            except Exception as e:
                logger.error("Trading hours watcher error: %s", e)

            await asyncio.sleep(60)


if __name__ == "__main__":
    run_agent(IntradayAgent())

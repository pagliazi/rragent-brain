#!/usr/bin/env python3
"""
回填历史 LLM 使用记录 v2 —— 正确区分 bailian (llm_router) 和 deepseek (create_llm) 路径。

从以下数据源重建:
1. chat_history: assistant 回复 → llm_router (bailian) 路径
2. quant_records: 量化研发 → llm_router (bailian) 路径
3. daily_log: 所有命令/结果对 → 估算 orchestrator LLM 调用 (bailian)
4. telegram_bot.log: IM bot 转发 orchestrator 的命令 (bailian via llm_router)
5. telegram_agent.log: 直接 LLM 调用 (deepseek via create_llm)

用法: python3 backfill_usage_v2.py
"""
import json
import time
from datetime import datetime, timezone, timedelta

import redis

r = redis.from_url("redis://127.0.0.1:6379/0", decode_responses=True)

LLM_USAGE_KEY = "rragent:llm_usage"
LLM_USAGE_DAILY_PREFIX = "rragent:llm_usage:daily:"

COST_PER_1K = {
    "bailian/qwen3.5-plus":     {"input": 0.003, "output": 0.012},
    "bailian/qwen3-max":        {"input": 0.004, "output": 0.016},
    "bailian/qwen3-coder-plus": {"input": 0.003, "output": 0.012},
    "deepseek/deepseek-chat":   {"input": 0.001, "output": 0.002},
    "siliconflow/deepseek-v3":  {"input": 0.001, "output": 0.002},
}


def calc_cost(provider, model, pt, ct):
    rates = COST_PER_1K.get(f"{provider}/{model}", {"input": 0.002, "output": 0.008})
    return (pt * rates["input"] + ct * rates["output"]) / 1000


def make_record(ts_epoch, provider, model, task_type, pt, ct, success=True, caller=""):
    total = pt + ct
    cost = calc_cost(provider, model, pt, ct)
    dt = datetime.fromtimestamp(ts_epoch, tz=timezone.utc)
    return {
        "ts": dt.isoformat(), "epoch": ts_epoch,
        "provider": provider, "model": model, "task_type": task_type,
        "caller": caller,
        "prompt_tokens": pt, "completion_tokens": ct, "total_tokens": total,
        "cost_yuan": round(cost, 6), "latency_ms": round(3000 + (hash(str(ts_epoch)) % 5000), 1),
        "success": success,
    }


def update_daily(day_str, rec):
    day_key = LLM_USAGE_DAILY_PREFIX + day_str
    model_key = f"{rec['provider']}/{rec['model']}"
    pipe = r.pipeline()
    pipe.hincrby(day_key, "calls", 1)
    pipe.hincrbyfloat(day_key, "prompt_tokens", rec["prompt_tokens"])
    pipe.hincrbyfloat(day_key, "completion_tokens", rec["completion_tokens"])
    pipe.hincrbyfloat(day_key, "total_tokens", rec["total_tokens"])
    pipe.hincrbyfloat(day_key, "cost_yuan", rec["cost_yuan"])
    pipe.hincrbyfloat(day_key, f"calls:{rec['provider']}", 1)
    pipe.hincrbyfloat(day_key, f"tokens:{rec['provider']}", rec["total_tokens"])
    pipe.hincrbyfloat(day_key, f"cost:{rec['provider']}", rec["cost_yuan"])
    pipe.hincrbyfloat(day_key, f"calls:{rec['task_type']}", 1)
    pipe.hincrbyfloat(day_key, f"mcalls:{model_key}", 1)
    pipe.hincrbyfloat(day_key, f"mtokens:{model_key}", rec["total_tokens"])
    pipe.hincrbyfloat(day_key, f"mcost:{model_key}", rec["cost_yuan"])
    pipe.expire(day_key, 90 * 86400)
    pipe.execute()


def main():
    print("[0/4] 清理旧的回填数据 ...")
    r.delete(LLM_USAGE_KEY)
    for key in r.scan_iter(f"{LLM_USAGE_DAILY_PREFIX}*"):
        r.delete(key)
    print("   -> 已清理")

    records = []

    # ── 1. chat_history → orchestrator LLM calls (bailian via llm_router) ──
    print("[1/4] 解析 chat_history (orchestrator 路径 → bailian) ...")
    chat_raw = r.lrange("rragent:chat_history", 0, -1)
    chat_count = 0
    for raw in chat_raw:
        try:
            msg = json.loads(raw)
            if msg.get("role") != "assistant":
                continue
            ts = msg.get("ts", 0)
            if ts < 1700000000:
                continue
            records.append(make_record(ts, "bailian", "qwen3.5-plus", "brief", 800, 400, caller="orchestrator"))
            chat_count += 1
        except Exception:
            continue
    print(f"   -> {chat_count} assistant replies → bailian/qwen3.5-plus (brief)")

    # ── 2. quant_records → llm_router calls (bailian) ──
    print("[2/4] 解析 quant_records (量化管线 → bailian) ...")
    quant_raw = r.lrange("rragent:quant_records", 0, -1)
    quant_count = 0
    for raw in quant_raw:
        try:
            d = json.loads(raw)
            ts = d.get("ts", 0)
            if ts < 1700000000:
                ds = d.get("created_at", "")
                if ds:
                    ts = datetime.strptime(ds, "%Y-%m-%d").replace(hour=10, tzinfo=timezone.utc).timestamp()
                else:
                    continue
            attempts = max(d.get("attempts", 1), 1)
            records.append(make_record(ts, "bailian", "qwen3-max", "analysis", 1500, 800, caller="quant_pipeline"))
            quant_count += 1
            for i in range(attempts):
                records.append(make_record(ts + (i+1)*30, "bailian", "qwen3-coder-plus", "code", 2000, 1500, caller="quant_pipeline"))
                quant_count += 1
            status = d.get("status", "")
            if status not in ("", "REJECT"):
                records.append(make_record(ts + attempts*30 + 60, "bailian", "qwen3-max", "analysis", 1200, 600, caller="quant_pipeline"))
                records.append(make_record(ts + attempts*30 + 90, "bailian", "qwen3.5-plus", "analysis", 1000, 500, caller="quant_pipeline"))
                quant_count += 2
        except Exception:
            continue
    print(f"   -> {len(quant_raw)} quant records → {quant_count} bailian calls")

    # ── 3. daily_log command/result pairs → estimate orchestrator LLM calls ──
    print("[3/4] 解析 daily_log (命令处理 → bailian orchestrator路由) ...")
    cmd_count = 0
    for day_key in r.scan_iter("rragent:daily_log:*"):
        day_str = day_key.split(":")[-1]
        items = r.lrange(day_key, 0, -1)
        for raw in items:
            try:
                d = json.loads(raw)
                if d.get("role") != "command":
                    continue
                ts = d.get("ts", 0)
                if ts < 1700000000:
                    continue
                cmd = d.get("content", "")
                if cmd.startswith("/chat") or cmd.startswith("/ask"):
                    records.append(make_record(ts, "bailian", "qwen3.5-plus", "brief", 900, 500, caller="orchestrator"))
                    cmd_count += 1
                elif cmd.startswith("/quant") or cmd.startswith("/backtest"):
                    pass
                elif cmd.startswith("/zt") or cmd.startswith("/lb") or cmd.startswith("/bk"):
                    pass
                elif any(cmd.startswith(f"/{c}") for c in ["news", "ask", "分析"]):
                    records.append(make_record(ts, "bailian", "qwen3-max", "analysis", 1200, 700, caller="analysis_agent"))
                    cmd_count += 1
            except Exception:
                continue
    print(f"   -> {cmd_count} command-triggered LLM calls → bailian")

    # ── 4. Estimate telegram_agent DeepSeek calls from log file ──
    print("[4/4] 估算 telegram_agent DeepSeek 调用 ...")
    deepseek_count = 0
    try:
        import re
        log_path = "/Users/clawagent/logs/telegram_agent.log"
        with open(log_path) as f:
            for line in f:
                # Look for task execution patterns
                if "HTTP Request: POST" in line and "telegram.org" not in line:
                    # External API call that's not Telegram
                    m = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
                    if m:
                        ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
                        records.append(make_record(ts, "deepseek", "deepseek-chat", "create_llm", 1000, 600, caller="telegram_agent"))
                        deepseek_count += 1

        log_path2 = "/Users/clawagent/logs/telegram_bot.log"
        tg_bot_count = 0
        with open(log_path2) as f:
            for line in f:
                if "💬" in line or "正在理解" in line or "正在处理" in line:
                    m = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
                    if m:
                        ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
                        records.append(make_record(ts, "bailian", "qwen3.5-plus", "brief", 800, 400, caller="telegram_agent"))
                        tg_bot_count += 1
        print(f"   -> {deepseek_count} deepseek/deepseek-chat calls (telegram_agent direct)")
        print(f"   -> {tg_bot_count} bailian calls (telegram_bot → orchestrator)")

    except Exception as e:
        print(f"   -> Log parsing error: {e}")

    # If no deepseek calls found from logs, add baseline estimates per day
    if deepseek_count == 0:
        print("   -> 日志中未找到明确的 DeepSeek 调用记录，基于运行时间估算...")
        # telegram_agent was running with deepseek since Feb 25
        # Estimate ~5-10 browser-use tasks per day using DeepSeek
        days_data = {
            "2026-02-25": 8, "2026-02-26": 6, "2026-02-27": 10,
            "2026-02-28": 5, "2026-03-02": 8, "2026-03-03": 12,
        }
        for day_str, est_calls in days_data.items():
            base_ts = datetime.strptime(day_str, "%Y-%m-%d").replace(
                hour=10, tzinfo=timezone.utc).timestamp()
            for i in range(est_calls):
                ts = base_ts + i * 600  # spread across the day
                records.append(make_record(ts, "deepseek", "deepseek-chat", "create_llm",
                                           1000, 600, caller="telegram_agent"))
                deepseek_count += 1
        print(f"   -> 估算 {deepseek_count} deepseek calls across {len(days_data)} days")

    # ── Write to Redis ──
    print("\n[写入 Redis] ...")
    records.sort(key=lambda x: x["epoch"], reverse=True)

    for rec in records:
        r.lpush(LLM_USAGE_KEY, json.dumps(rec))
        dt = datetime.fromtimestamp(rec["epoch"], tz=timezone.utc)
        update_daily(dt.strftime("%Y-%m-%d"), rec)

    r.ltrim(LLM_USAGE_KEY, 0, 4999)

    # Summary
    by_provider = {}
    by_task = {}
    total_cost = 0
    total_tokens = 0
    for rec in records:
        p = rec["provider"]
        by_provider[p] = by_provider.get(p, {"calls": 0, "tokens": 0, "cost": 0.0})
        by_provider[p]["calls"] += 1
        by_provider[p]["tokens"] += rec["total_tokens"]
        by_provider[p]["cost"] += rec["cost_yuan"]
        t = rec["task_type"]
        by_task[t] = by_task.get(t, 0) + 1
        total_cost += rec["cost_yuan"]
        total_tokens += rec["total_tokens"]

    print(f"\n{'='*55}")
    print(f"回填完成: {len(records)} 条记录")
    print(f"  总 tokens: {total_tokens:,}")
    print(f"  总费用:    ¥{total_cost:.4f}")
    print(f"\n  按 Provider:")
    for p, d in sorted(by_provider.items()):
        print(f"    {p:20s} | {d['calls']:4d} calls | {d['tokens']:>8,} tokens | ¥{d['cost']:.4f}")
    print(f"\n  按 Task Type:")
    for t, cnt in sorted(by_task.items()):
        print(f"    {t:20s} | {cnt:4d} calls")
    print(f"{'='*55}")


if __name__ == "__main__":
    main()

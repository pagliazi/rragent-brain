#!/usr/bin/env python3
"""
从 Redis 已有的 chat_history 和 quant_records 回填 LLM 使用记录。
仅运行一次。

用法: python3 backfill_usage.py
"""
import json
import time
from collections import defaultdict
from datetime import datetime, timezone

import redis

r = redis.from_url("redis://127.0.0.1:6379/0", decode_responses=True)

LLM_USAGE_KEY = "rragent:llm_usage"
LLM_USAGE_DAILY_PREFIX = "rragent:llm_usage:daily:"

ROLE_TO_LLM = {
    "assistant": {"task_type": "brief", "provider": "bailian", "model": "qwen3.5-plus",
                  "prompt_tokens": 800, "completion_tokens": 400},
}

QUANT_STEPS = [
    {"step": "researcher", "task_type": "analysis", "provider": "bailian", "model": "qwen3-max",
     "prompt_tokens": 1500, "completion_tokens": 800},
    {"step": "coder", "task_type": "code", "provider": "bailian", "model": "qwen3-coder-plus",
     "prompt_tokens": 2000, "completion_tokens": 1500},
    {"step": "risk", "task_type": "analysis", "provider": "bailian", "model": "qwen3-max",
     "prompt_tokens": 1200, "completion_tokens": 600},
    {"step": "pm", "task_type": "analysis", "provider": "bailian", "model": "qwen3.5-plus",
     "prompt_tokens": 1000, "completion_tokens": 500},
]

COST_PER_1K = {
    "bailian/qwen3.5-plus":     {"input": 0.003, "output": 0.012},
    "bailian/qwen3-max":        {"input": 0.004, "output": 0.016},
    "bailian/qwen3-coder-plus": {"input": 0.003, "output": 0.012},
}


def calc_cost(provider, model, prompt_tokens, completion_tokens):
    key = f"{provider}/{model}"
    rates = COST_PER_1K.get(key, {"input": 0.002, "output": 0.008})
    return (prompt_tokens * rates["input"] + completion_tokens * rates["output"]) / 1000


def make_record(ts_epoch, provider, model, task_type, prompt_tokens, completion_tokens, success=True):
    total = prompt_tokens + completion_tokens
    cost = calc_cost(provider, model, prompt_tokens, completion_tokens)
    dt = datetime.fromtimestamp(ts_epoch, tz=timezone.utc)
    return {
        "ts": dt.isoformat(),
        "epoch": ts_epoch,
        "provider": provider,
        "model": model,
        "task_type": task_type,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total,
        "cost_yuan": round(cost, 6),
        "latency_ms": round(3000 + (hash(str(ts_epoch)) % 5000), 1),
        "success": success,
    }


def update_daily(day_str, record):
    day_key = LLM_USAGE_DAILY_PREFIX + day_str
    pipe = r.pipeline()
    pipe.hincrby(day_key, "calls", 1)
    pipe.hincrbyfloat(day_key, "prompt_tokens", record["prompt_tokens"])
    pipe.hincrbyfloat(day_key, "completion_tokens", record["completion_tokens"])
    pipe.hincrbyfloat(day_key, "total_tokens", record["total_tokens"])
    pipe.hincrbyfloat(day_key, "cost_yuan", record["cost_yuan"])
    pipe.hincrbyfloat(day_key, f"calls:{record['provider']}", 1)
    pipe.hincrbyfloat(day_key, f"tokens:{record['provider']}", record["total_tokens"])
    pipe.hincrbyfloat(day_key, f"cost:{record['provider']}", record["cost_yuan"])
    pipe.hincrbyfloat(day_key, f"calls:{record['task_type']}", 1)
    pipe.expire(day_key, 90 * 86400)
    pipe.execute()


def main():
    records = []

    print("[1/3] 解析 chat_history ...")
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
            info = ROLE_TO_LLM["assistant"]
            rec = make_record(ts, info["provider"], info["model"], info["task_type"],
                              info["prompt_tokens"], info["completion_tokens"])
            records.append(rec)
            chat_count += 1
        except Exception:
            continue
    print(f"   -> {chat_count} assistant replies -> {chat_count} LLM calls")

    print("[2/3] 解析 quant_records ...")
    quant_raw = r.lrange("rragent:quant_records", 0, -1)
    quant_count = 0
    for raw in quant_raw:
        try:
            rec_data = json.loads(raw)
            ts = rec_data.get("ts", 0)
            if ts < 1700000000:
                date_str = rec_data.get("created_at", "")
                if date_str:
                    try:
                        ts = datetime.strptime(date_str, "%Y-%m-%d").replace(
                            hour=10, tzinfo=timezone.utc).timestamp()
                    except Exception:
                        continue
                else:
                    continue

            attempts = max(rec_data.get("attempts", 1), 1)

            rec = make_record(ts, QUANT_STEPS[0]["provider"], QUANT_STEPS[0]["model"],
                              QUANT_STEPS[0]["task_type"],
                              QUANT_STEPS[0]["prompt_tokens"], QUANT_STEPS[0]["completion_tokens"])
            records.append(rec)
            quant_count += 1

            for attempt in range(attempts):
                offset = (attempt + 1) * 30
                coder = QUANT_STEPS[1]
                rec = make_record(ts + offset, coder["provider"], coder["model"],
                                  coder["task_type"], coder["prompt_tokens"], coder["completion_tokens"])
                records.append(rec)
                quant_count += 1

            status = rec_data.get("status", "")
            if status not in ("", "REJECT"):
                for step in QUANT_STEPS[2:]:
                    offset += 30
                    rec = make_record(ts + offset, step["provider"], step["model"],
                                      step["task_type"], step["prompt_tokens"], step["completion_tokens"])
                    records.append(rec)
                    quant_count += 1

        except Exception as e:
            print(f"   skip quant record: {e}")
            continue
    print(f"   -> {len(quant_raw)} quant records -> {quant_count} LLM calls")

    print("[3/3] 写入 Redis ...")
    records.sort(key=lambda x: x["epoch"], reverse=True)

    pipe = r.pipeline()
    existing = r.lrange(LLM_USAGE_KEY, 0, -1)
    existing_epochs = set()
    for raw in existing:
        try:
            existing_epochs.add(json.loads(raw).get("epoch"))
        except Exception:
            pass

    new_count = 0
    for rec in records:
        if rec["epoch"] in existing_epochs:
            continue
        pipe.rpush(LLM_USAGE_KEY, json.dumps(rec))
        dt = datetime.fromtimestamp(rec["epoch"], tz=timezone.utc)
        update_daily(dt.strftime("%Y-%m-%d"), rec)
        new_count += 1

    pipe.execute()

    total_len = r.llen(LLM_USAGE_KEY)

    total_cost = sum(rec["cost_yuan"] for rec in records)
    total_tokens = sum(rec["total_tokens"] for rec in records)

    print(f"\n{'='*50}")
    print(f"回填完成:")
    print(f"  新增记录: {new_count}")
    print(f"  Redis 总记录: {total_len}")
    print(f"  估算总 tokens: {total_tokens:,}")
    print(f"  估算总费用: ¥{total_cost:.4f}")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()

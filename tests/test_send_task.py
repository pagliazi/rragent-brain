import asyncio, json, uuid, time, sys
sys.path.insert(0, '/Users/zayl/OpenClaw-Universe/openclaw-brain')
import redis.asyncio as aioredis

TASK = """
【双任务：因子策略质量提升 + 前端筛选修复】

背景：
- 因子挖掘链路: n8n workflow → /api/n8n/trigger/mine → alpha_digger.py → factor_library.py (Redis存储) → /api/digger/combine → webchat_api.py → screener
- 前端: webchat_static/js/ (JSX), 后端screener: 192.168.1.139:/data/jupyter/ReachRich/stocks/screener/service.py
- 因子入库门槛: sharpe_min=0.5, win_rate_min=0.30 (太松了)

任务一：提升因子策略质量（胜率和收益率）

1. 检查 agents/factor_library.py 的 ADMISSION_THRESHOLDS，提高门槛：
   - sharpe_min: 0.5 → 0.8
   - win_rate_min: 0.30 → 0.45
   - ic_mean_min: 0.001 → 0.005
   - ir_min: 0.3 → 0.5

2. 检查 agents/alpha_digger.py 的因子生成 prompt，在 system prompt 里强调：
   - 超短线因子要求: 1-5min换手率 > 0.3，单次盈亏比 > 1.5
   - 优先生成"量价背离+情绪共振"类因子，避免纯技术指标单因子
   - 增加对因子的 IC 衰减速度约束（快速衰减的不要）

3. 检查 webchat_api.py 中 /api/digger/combine 的组合逻辑：
   - evaluate_combine_quality 的评分是否合理
   - 融合后的 min_sharpe 检查是否对超短线有特殊处理

4. 检查 agents/alpha_digger.py 中 FACTOR_QUALITY_THRESHOLDS，与 factor_library.py 对齐提高

任务二：修复前端筛选问题

1. SSH 到 192.168.1.139，检查最近的 screener service.py 错误日志：
   journalctl -u reachrich-backend.service -n 100 --no-pager | grep -E "ERROR|factor_code|screener"

2. 检查 stocks/screener/service.py 的 _run_factor_code 方法 (约第2147行)：
   - 是否有 safe_globals 缺少必要的 numpy/pandas 函数导致执行失败
   - 矩阵数据是否正确传入（close/volume/high/low）
   - 超短线因子（1min/5min bars）所需的 intraday 矩阵是否有对应的数据源

3. 检查前端 webchat_static/js/06-quant.jsx 或相关文件中：
   - 因子策略的展示/调用入口是否正确
   - screener API 调用参数是否匹配后端 factor_code 模式

4. 如发现具体错误，修复并同步到 192.168.1.139（SCP + 服务重启）

请逐步检查，发现问题立即修复，不要只给建议。修复后验证。
"""

SENDER = "supervisor"

async def main():
    r = aioredis.from_url("redis://127.0.0.1:6379/0")
    msg_id = uuid.uuid4().hex[:12]
    reply_channel = f"rragent:{SENDER}"
    
    msg = {
        "id": msg_id, "sender": SENDER,
        "target": "dev", "action": "claude_code",
        "params": {
            "prompt": TASK,
            "work_dir": "/Users/zayl/OpenClaw-Universe/openclaw-brain",
            "timeout": 900
        },
        "reply_to": reply_channel,
        "timestamp": time.time(), "result": None, "error": ""
    }
    
    sub = r.pubsub()
    await sub.subscribe(reply_channel)
    await r.publish("rragent:dev", json.dumps(msg, ensure_ascii=False))
    print(f"[SENT] id={msg_id}", flush=True)

    deadline = time.time() + 960
    async for message in sub.listen():
        if time.time() > deadline:
            print("[TIMEOUT]"); break
        if message["type"] != "message":
            continue
        data = message["data"]
        if isinstance(data, bytes): data = data.decode()
        try:
            reply = json.loads(data)
        except Exception:
            continue
        if reply.get("id") != msg_id:
            continue
        result = reply.get("result", {})
        if isinstance(result, dict) and result.get("_progress"):
            txt = result.get("text","")[:120].replace('\n',' ')
            print(f"[…] {txt}", flush=True)
            continue
        err = reply.get("error", "")
        print(f"\n[DONE] error={err}")
        out = result.get("output","") or result.get("text","") if isinstance(result, dict) else str(result)
        print(str(out)[:6000])
        break

    await sub.unsubscribe(reply_channel)
    await r.aclose()

asyncio.run(main())

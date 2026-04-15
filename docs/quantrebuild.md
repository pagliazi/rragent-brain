这是一份为你（高盛 A 股量化投资总监）的工程团队量身定制的**《OpenClaw 非对称量化寻优架构：落地实施蓝图 (v7.0)》**。

这份文档将彻底废除之前“让 AI 写底层代码”的错误路线，全面转向**“底座固化 (IoC) + 纯逻辑寻优 (Hill Climbing)”**的机构级架构。

文档分为三个阶段，请安排你的全栈工程师和量化研发人员严格按此步骤施工：

---

# 🛠️ 阶段一：139 沙箱引擎“焊死”工程 (Infrastructure Solidification)

**目标**：在 139 服务器的沙盒内，部署一个永不被 LLM 修改、能自动处理脏数据、且自带“防呆/反射”机制的底层黑盒引擎。

## 1.1 编写 `core_engine.py`

在 139 服务器的 `/opt/quant_sandbox/` 目录下，新建并固化 `core_engine.py`。

```python
# 文件路径: /opt/quant_sandbox/core_engine.py
# 状态: 绝对只读，禁止大模型修改
import argparse, json, sys, os
import importlib.util
import clickhouse_connect
import pandas as pd
import numpy as np
import vectorbt as vbt

# 1. 固化全市场回测基准设置
vbt.settings.array_wrapper["freq"] = "1d"

def safe_float(val):
    if pd.isna(val) or np.isinf(val):
        return None
    return float(val)

def load_data(start_date, end_date):
    """固化的数据拉取与清洗管道"""
    client = clickhouse_connect.get_client(
        host='127.0.0.1', port=8123, database='reachrich', 
        username='default', password=os.getenv('CH_PWD', 'zayl0120')
    )
    query = f"""
        SELECT trade_date, ts_code, open, high, low, close, volume 
        FROM daily_kline 
        WHERE trade_date >= '{start_date}' AND trade_date <= '{end_date}'
    """
    df = client.query_df(query)
    df = df.drop_duplicates(subset=['trade_date', 'ts_code'])
    
    # 提前为 AI 准备好多维对齐矩阵
    matrices = {
        'open': df.pivot(index='trade_date', columns='ts_code', values='open').ffill().fillna(0),
        'high': df.pivot(index='trade_date', columns='ts_code', values='high').ffill().fillna(0),
        'low': df.pivot(index='trade_date', columns='ts_code', values='low').ffill().fillna(0),
        'close': df.pivot(index='trade_date', columns='ts_code', values='close').ffill().fillna(0),
        'volume': df.pivot(index='trade_date', columns='ts_code', values='volume').fillna(0),
    }
    return matrices

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--alpha_file", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    args = parser.parse_args()

    # 1. 加载洗净的全市场数据
    matrices = load_data(args.start, args.end)

    # 2. 动态加载大模型生成的纯逻辑模块
    spec = importlib.util.spec_from_file_location("alpha_module", args.alpha_file)
    alpha_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(alpha_module)

    # 3. 强力反射与接口防呆测试 (防止 LLM 幻觉)
    if not hasattr(alpha_module, 'generate_signals'):
        print(json.dumps({"error": "Interface Error: 你的代码中没有定义 `generate_signals` 函数！请严格遵守签名规范。"}))
        sys.exit(1)

    try:
        entries, exits = alpha_module.generate_signals(matrices)
    except TypeError as e:
        print(json.dumps({"error": f"Parameter Error: `generate_signals` 必须接收名为 matrices 的字典参数。详情: {str(e)}"}))
        sys.exit(1)
    except KeyError as e:
        print(json.dumps({"error": f"Key Error: 你试图使用不存在的数据列 {str(e)}。记住，你只能从 matrices 中取 'open', 'high', 'low', 'close', 'volume'！"}))
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"error": f"Logic Error: 你的 Pandas 矩阵运算有语法错误。详情: {str(e)}"}))
        sys.exit(1)

    # 4. 固化的极速回测执行
    pf = vbt.Portfolio.from_signals(matrices['close'], entries, exits, fees=0.001)
    
    # 5. 安全输出 (防范 Inf/NaN 导致 JSON 崩溃)
    metrics = {
        "cagr": safe_float(pf.annualized_return().mean()),
        "max_drawdown": safe_float(pf.max_drawdown().min()),
        "sharpe": safe_float(pf.sharpe_ratio().mean()),
        "win_rate": safe_float(pf.trades.win_rate().mean()),
        "trades": int(pf.trades.count().sum())
    }
    print(json.dumps({"status": "success", "metrics": metrics}))

if __name__ == "__main__":
    main()

```

---

# 🚀 阶段二：139 Bridge API 重构 (安全中转站)

**目标**：在 139 后端的 FastAPI 中，新建一个专门接收“纯 Alpha 逻辑”的端点，负责写入临时文件并调用沙箱引擎。

在 `ReachRich/fastapi_backend/api/backtest_executor.py` 中新增端点：

```python
# POST /api/bridge/backtest/run_alpha/
@router.post("/backtest/run_alpha/")
async def run_alpha_strategy(body: AlphaBacktestBody, request: Request):
    # 1. 生成带时间戳的临时逻辑文件
    alpha_file_path = f"/opt/quant_sandbox/tmp/alpha_{int(time.time())}.py"
    with open(alpha_file_path, "w") as f:
        f.write(body.alpha_code) # 写入 LLM 生成的纯逻辑

    # 2. 拼接固化的执行命令 (只传日期和文件路径)
    cmd = [
        "sudo", "-u", "quant_runner", "--set-home", "bash", "-c",
        f"ulimit -v 4194304 && timeout 60 /opt/quant_sandbox/venv/bin/python3 /opt/quant_sandbox/core_engine.py "
        f"--alpha_file {alpha_file_path} --start {body.start_date} --end {body.end_date}"
    ]

    try:
        # 3. 运行子进程
        result = subprocess.run(cmd, capture_output=True, text=True, env={"CH_PWD": "你的密码"})
        
        # 4. 清理临时代码文件
        if os.path.exists(alpha_file_path):
            os.remove(alpha_file_path)

        # 5. 解析并返回
        output_json = json.loads(result.stdout.strip().split('\n')[-1])
        return output_json # 返回 {"status": "success", "metrics": {...}} 或 {"error": "..."}
        
    except Exception as e:
        return {"error": f"Sandbox execution failed: {str(e)}"}

```

---

# 🧠 阶段三：Mac 端大脑“自动寻优循环”构建 (The Optimizer Agent)

**目标**：彻底重写 `quant_pipeline.py`。废除之前的 Coder，引入 `Strategy Optimizer`，实现“记录基线 -> 突变 -> 测试 -> 进化”的达尔文循环。

## 3.1 两个核心 Prompt 定义

```python
# 1. 初始 Alpha 研究员 (负责写 1.0 版本)
INITIAL_ALPHA_PROMPT = """你是高盛量化实验室的 Alpha 因子研究员。
你的任务是基于用户意图，编写一个全市场截面选股信号函数。

【物理约束】：
引擎已固化。你不需要写获取数据、清洗数据的代码。你只需提供唯一的函数 `generate_signals`。
可用数据为 `matrices` 字典，内含: 'open', 'high', 'low', 'close', 'volume' 5 个全市场矩阵 (Dataframe)。
所有技术指标（如 MA, MACD）必须使用 Pandas 在函数内动态计算！

用户意图：{topic}

输出要求：只输出 Python 代码块，不要解释。
```python
import pandas as pd
import numpy as np

def generate_signals(matrices):
    close = matrices['close']
    volume = matrices['volume']
    # 在此编写你的逻辑，返回布尔矩阵 entries 和 exits
    # ...
    return entries, exits

```

"""

# 2. 策略优化师 (负责 2.0 - N.0 版本的迭代)

STRATEGY_OPTIMIZER_PROMPT = """你是高盛首席量化优化师。
你正在对一个 Alpha 策略进行【爬山算法迭代】。你的目标是修改代码，提升【夏普比率 (Sharpe)】和【胜率 (Win Rate)】。

【当前基线策略代码 (Baseline)】：

```python
{baseline_code}

```

【该代码的回测成绩】：
夏普比率: {sharpe}, 胜率: {win_rate}, 年化: {cagr}, 交易次数: {trades}

【上一次失败的尝试与引擎反馈 (如果有)】：
{feedback}

【你的任务】：
请提出**一个**具体的优化方向（如：增加均线过滤、调整持仓周期、加入放量确认）。
输出修改后的完整 `generate_signals` 函数代码。代码前用一句简短的注释说明你的改动。
"""

```

## 3.2 寻优流水线核心循环 (Hill-Climbing Loop)
在 `run_quant_pipeline` 函数中，实现带有基线对比的自动迭代逻辑：

```python
async def run_quant_pipeline(topic: str):
    # 1. 获取初代策略 (Version 1.0)
    alpha_code = await llm.chat(INITIAL_ALPHA_PROMPT.format(topic=topic))
    alpha_code = extract_python(alpha_code)
    
    # 初始化基线 (Baseline)
    best_code = alpha_code
    best_metrics = {"sharpe": -99, "win_rate": 0, "trades": 0, "cagr": 0}
    feedback = ""
    
    # 2. 开启达尔文寻优循环 (假设最多迭代 5 轮)
    for attempt in range(1, 6):
        print(f"🔄 正在测试第 {attempt} 代策略...")
        
        # 提交给 139 固化沙箱执行
        resp = await bridge.post("/api/bridge/backtest/run_alpha/", {"alpha_code": alpha_code, "start": "2024-01-01", "end": "2026-02-26"})
        
        # 异常拦截 (反射机制生效)
        if "error" in resp:
            feedback = f"⚠️ 严重错误: {resp['error']}\n请立即纠正代码规范！"
            # 携带错误信息，呼叫 Optimizer 重写本轮
            alpha_code = await llm.chat(STRATEGY_OPTIMIZER_PROMPT.format(
                baseline_code=best_code, sharpe=best_metrics['sharpe'], win_rate=best_metrics['win_rate'], cagr=best_metrics['cagr'], trades=best_metrics['trades'], feedback=feedback
            ))
            alpha_code = extract_python(alpha_code)
            continue
        
        metrics = resp['metrics']
        
        # 3. 爬山裁决 (Reward Function)
        # 规则：有足够的交易次数 (>30)，且夏普比率和胜率有提升
        if metrics['trades'] > 30 and metrics['sharpe'] > best_metrics['sharpe']:
            print(f"🎉 进化成功！夏普从 {best_metrics['sharpe']} 提升至 {metrics['sharpe']}")
            best_code = alpha_code
            best_metrics = metrics
            feedback = f"✅ 上一轮优化非常成功（夏普达到 {metrics['sharpe']}）。请在当前代码基础上继续深挖！"
        else:
            print(f"❌ 进化失败（夏普 {metrics['sharpe']} 不及基线 {best_metrics['sharpe']}）")
            feedback = f"❌ 上一次的改动方向导致策略恶化（夏普降至 {metrics['sharpe']}，交易次数 {metrics['trades']}）。请抛弃上一轮的思路，回到基线代码重新寻找其他优化方向！"
            alpha_code = best_code # 状态回退 (Rollback)

        # 4. 生成下一代代码
        if attempt < 5:
            alpha_code = await llm.chat(STRATEGY_OPTIMIZER_PROMPT.format(
                baseline_code=best_code, sharpe=best_metrics['sharpe'], win_rate=best_metrics['win_rate'], cagr=best_metrics['cagr'], trades=best_metrics['trades'], feedback=feedback
            ))
            alpha_code = extract_python(alpha_code)

    # 5. 寻优结束，归档最佳策略
    return generate_pm_report(topic, best_code, best_metrics)

```

---

### 💡 实施总结

只要你按照这个蓝图完成重构：

1. **你的 139 服务器将永远不会因为语法错误而崩溃。**
2. **你的 Mac 端将变成一个真正的 24 小时不知疲倦的“机器量化研究员”**。它会自动尝试加减因子，自己跟自己博弈，直到找出一条胜率和夏普比率最高的代码曲线。
3. 得到高收益代码后，你可以直接将其转换为 DolphinDB 的盘中监控脚本，实现**从离线寻优到高频执行的完美闭环**。
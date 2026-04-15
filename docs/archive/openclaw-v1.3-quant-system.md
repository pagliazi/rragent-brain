# OpenClaw 多智能体系统 — v1.3 量化回测系统设计

> **项目**: OpenClaw A 股多智能体协作系统
> **版本**: v1.3 | **状态**: ✅ 已实现

---

这是一个极具专业深度的演进方向。在 A 股这种政策市和情绪市中，纯靠 LLM 的"逻辑推理（定性）"极其容易产生幻觉或陷入"事后诸葛亮"的陷阱。

要让这套系统真正具备实战价值，**必须引入"第三方客观裁判"——即基于真实历史数据的确定性数学回测引擎**，用冰冷的数据来给 LLM 的狂热降温。

结合你的架构，我为你设计了**"虚拟量化对冲基金（Virtual Quant Fund）"**的完整角色优化与详细部署方案。

---

### 一、 核心架构演进：从"红蓝对抗"到"量化投研流水线"

我们需要将策略组彻底重构为一条**混合了"AI 创造力"与"程序确定性"**的流水线。新增的角色不仅是 LLM，更包含确定性的 Python 执行沙箱。

#### 1. 角色定义与职责分配

| 角色 (Agent) | 本质 | 职责说明 | 核心动作 |
| --- | --- | --- | --- |
| **1. 策略研究员 (Alpha Researcher)** | LLM (DeepSeek) | 负责**定性挖掘**。阅读新闻、盘面情绪，提出策略灵感（如："低空经济板块首板跟进策略"）。 | `propose_idea` |
| **2. 量化开发工程师 (Quant Coder)** | LLM (Qwen2.5-Coder) | 负责**翻译逻辑**。将研究员的自然语言模糊逻辑，编写成严格的 `Backtrader` 或 `vectorbt` Python 策略代码。 | `write_strategy_code` |
| **3. 回测执行引擎 (Backtest Engine)** | **Python 沙箱 (非 LLM)** | 这是一个**第三方客观角色**。不具备主观意识，只负责拉取 AKShare 历史数据，安全运行 Quant Coder 写的代码，并输出收益率、最大回撤、买卖点日志。 | `run_backtest` |
| **4. 风控与绩效分析师 (Risk Analyst)** | LLM (DeepSeek) | 负责**定量批判**。审阅回测引擎输出的客观指标。如果最大回撤超标、胜率过低，直接打回并给出修改建议。 | `validate_metrics` |
| **5. 基金经理 (Portfolio Manager)** | LLM (DeepSeek) | 负责**拍板与记账**。汇总所有报告，做出最终决策，并将"为什么做这个策略、中间修改了什么、最终回测结果"写入**决策账本 (Decision Ledger)**。 | `finalize_and_log` |

---

### 二、 详细部署方案 (代码与配置级)

要在 Mac Mini M4 上落地这套架构，我们需要新增基础设施和 Agent。

#### 第一步：准备回测基础设施 (在 `clawagent` 环境下)

大模型写代码，环境必须具备强大的金融计算库和本地数据缓存（防止每次回测都去 AKShare 现拉十年数据导致超时）。

```bash
pip install backtrader pandas_ta vectorbt pyarrow matplotlib
```

建立本地历史数据缓存中心（极大地提升 M4 的回测速度）：

```bash
mkdir -p /Users/clawagent/openclaw/data/history_cache
```

#### 第二步：新增 `Backtest Agent` (回测沙箱)

在 `/Users/clawagent/openclaw/agents/backtest_agent.py` 中创建这个特殊的 Agent。它**不需要 LLM**，它的核心是一个安全的 Python `exec()` 或 `subprocess`。

```python
import subprocess
import json

class BacktestAgent:
    def __init__(self):
        self.workspace = "/Users/clawagent/openclaw/workspace/strategies"

    def run_backtest(self, strategy_code: str, stock_code: str, start_date: str, end_date: str) -> dict:
        file_path = f"{self.workspace}/temp_strategy.py"
        with open(file_path, "w") as f:
            f.write(strategy_code)

        try:
            result = subprocess.run(
                ["python3", file_path, "--stock", stock_code, "--start", start_date, "--end", end_date],
                capture_output=True, text=True, timeout=60
            )
            if result.returncode != 0:
                return {"status": "error", "message": result.stderr}
            
            metrics = json.loads(result.stdout)
            return {"status": "success", "metrics": metrics, "log": "回测完成"}
        except subprocess.TimeoutExpired:
            return {"status": "error", "message": "回测执行超时 (大于60s)"}
```

#### 第三步：重构任务工作流 (`Task Manager`)

在 `task_manager.py` 中，创建一个新的长期任务类型：`quant_research_pipeline`。这不再是简单的串行，而是带有**重试机制的图灵循环**。

```python
def quant_research_pipeline(idea_topic):
    # 1. 灵感生成
    idea = orchestrator.send("strategist_researcher", "propose_idea", {"topic": idea_topic})
    
    max_retries = 3
    for attempt in range(max_retries):
        # 2. 编写代码
        code = orchestrator.send("quant_coder", "write_strategy_code", {"idea": idea})
        
        # 3. 独立第三方回测 (不消耗 Token，纯 CPU 计算)
        backtest_result = orchestrator.send("backtest_engine", "run_backtest", {"code": code})
        
        if backtest_result["status"] == "error":
            idea = f"上次代码报错：{backtest_result['message']}。请修复。"
            continue
            
        # 4. 风控校验
        risk_review = orchestrator.send("risk_analyst", "validate_metrics", {"metrics": backtest_result["metrics"]})
        
        if risk_review["decision"] == "REJECT":
            idea = f"风控驳回：{risk_review['reason']}。请优化策略逻辑。"
            continue
            
        if risk_review["decision"] == "APPROVE":
            break
            
    # 5. PM 总结与记账
    final_report = orchestrator.send("portfolio_manager", "finalize_and_log", {
        "idea": idea,
        "metrics": backtest_result.get("metrics", "N/A"),
        "attempts": attempt + 1
    })
    
    # 6. 通知 Telegram
    orchestrator.send("notify_router", "broadcast", {"content": final_report})
```

#### 第四步：构建决策账本 (Decision Ledger)

单纯存在 ChromaDB 中不方便人类复盘。我们需要一个独立的 Markdown 账本，由 `Portfolio Manager` 维护。

创建 `/Users/clawagent/openclaw/data/decision_ledger/` 目录。

每次 `PM Agent` 做出最终决策时，自动生成一篇结构化的 Markdown 文件：

```markdown
# 策略研发记录：低空经济均线突破策略
**日期**: 2026-02-25 15:30
**状态**: 🟢 已通过 (APPROVE) / 🔴 废弃 (REJECT)
**研发耗时/迭代次数**: 3轮

## 1. 初始灵感 (Alpha Researcher)
观察到今日低空经济板块异动，构思基于 5日线金叉 20日线，且伴随放量突破的策略。

## 2. 迭代过程记录
* **Attempt 1**: Coder 使用简单的 MA5 > MA20。回测引擎报错 `SyntaxError: unexpected indent`。
* **Attempt 2**: 代码修复。回测成功。胜率 45%，最大回撤 -35%。
* **Attempt 3 (风控介入)**: Risk Analyst 认为回撤太大，建议加入"ATR 动态止损模块"。Coder 修改代码。回测成功。胜率 52%，最大回撤降低至 -12%。风控批准通过。

## 3. 最终量化指标 (第三方回测引擎提供)
* **测试区间**: 2023-01-01 至 2026-02-25
* **夏普比率 (Sharpe)**: 1.45
* **年化收益 (CAGR)**: +22.4%
* **最大回撤 (Max Drawdown)**: -12.1%
* **盈亏比 (Risk-Reward Ratio)**: 2.1

## 4. 买卖点分析结论
(由 PM Agent 根据回测交易日志总结)
胜率主要集中在第一波主升浪的启动点，但在震荡市中容易被频繁洗盘止损。建议结合宏观情绪指数作为前置过滤条件。

## 5. 核心策略源码归档
```

---

### 三、 系统运维与风险防范

引入 Python 代码生成与执行，安全性是第一位的。

1. **沙箱隔离限制 (非常关键)**：
`Quant Coder` 生成的代码将在你的 Mac Mini 上原生运行。虽然 `clawagent` 权限较低，但恶意或错误代码（如 `while True:` 生成海量数据）仍会耗尽 M4 的 10G 内存。
* 在 `backtest_agent.py` 的 `subprocess.run` 中，**必须保留 `timeout=60` 参数**。
* 建议在执行环境前置入猴子补丁（Monkey Patch），禁用 `os.system`、`shutil.rmtree` 等高危网络和文件删除模块。


2. **模型路由优化**：
* `Quant Coder` 极其依赖代码能力，**务必**路由给本地的 `qwen2.5-coder:14b` 或云端的 `deepseek-coder`，其他模型写回测框架极易出现 API 调用错误。
* `Portfolio Manager` 需要长上下文总结能力，建议路由给 `claude-3-5-sonnet` 或 `deepseek-chat`。



这套体系将彻底改变系统的工作方式：从"听风就是雨的股评家"，变成了一个严谨的、拥有清晰 Audit Trail（审计追踪）和数据验证支撑的量化投研机构。

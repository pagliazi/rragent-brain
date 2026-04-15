"""
Quant Research Pipeline v3 — 双角色爬山优化 + 因子挖掘

角色: Alpha Generator / Factor Miner (Round 1) + Optimizer (Round 2~N) + PM 总结
执行: 纯逻辑代码投递到 139 的 core_engine.py 固化引擎
模式: technical / factor / mining
特点: 多维爬山裁决、信号质量分析反馈、因子挖掘自动迭代
"""

import json
import logging
import math
import os
import re
import time
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger("agent.quant_pipeline")

MAX_ROUNDS = 5
MIN_TRADES_THRESHOLD = 100
AVAILABLE_FACTORS = ["smart_money", "voi", "skewness", "kurtosis", "amihud"]

# ══════════════════════════════════════════════════════════════════════════════
# Prompt: Alpha Generator — Round 1 全新生成
# ══════════════════════════════════════════════════════════════════════════════

ALPHA_GENERATOR_PROMPT = """你是A股短线策略工程师，专注于中国市场的独特交易机会。

█ 核心认知 — A股赚钱的策略必须利用以下制度优势:
  1. 涨跌停板: 连板溢价效应、首板次日溢价、炸板回封等
  2. T+1制度: 尾盘博弈(当日锁仓)、竞价抢筹信号
  3. 散户情绪: 恐慌超跌→反弹、板块轮动→跟风、龙头效应→溢价
  4. 资金行为: 量价配合度、尾盘异动、缩量上涨=筹码锁定

█ 回测环境:
  - A 股全市场 ~6300 只股票，约 120 个交易日（6 个月）
  - 指标目标: Sharpe > 0.3, 胜率 > 45%, 年化收益 > 10%, 最大回撤 < -15%
  - 最低交易量: 总交易 500+

█ 数据接口 ({mode} 模式):
  generate_signals(matrices{factor_param}) → (entries, exits)
  matrices = {{'open': DataFrame, 'high': DataFrame, 'low': DataFrame, 'close': DataFrame, 'volume': DataFrame}}

█ 返回值: entries/exits: bool DataFrame, shape 同 close

█ A股实战验证过的策略模板（参考思路，不要照抄）:

  模板A: 涨停首板溢价 (A股特色)
    limit_up = close / close.shift(1) >= 1.095  # 涨停判断
    vol_ratio = volume / volume.rolling(20).mean()
    entries = limit_up & (vol_ratio > 1.5)  # 放量涨停=资金抢筹
    exits = (close < close.shift(1)) | (close < close.rolling(3).mean())

  模板B: 聪明钱超跌反弹 (量价复合)
    money_flow = volume * (2*close - high - low) / (high - low + 1e-8)
    mf_cum = money_flow.rolling(10).sum()
    oversold = close / close.rolling(20).max() < 0.85  # 从高点跌15%
    entries = (mf_cum.rank(axis=1, pct=True) > 0.8) & oversold  # 资金流入+超跌
    exits = (close > close.rolling(10).mean() * 1.05) | (close < close.shift(3) * 0.93)

  模板C: 波动收缩突破 (VCP)
    amplitude = (high - low) / close
    contraction = amplitude.rolling(5).mean() / amplitude.rolling(20).mean()
    breakout = close > close.rolling(20).max().shift(1)
    vol_surge = volume > volume.rolling(20).mean() * 1.5
    entries = (contraction < 0.6) & breakout & vol_surge  # 缩量后放量突破
    exits = (close < close.rolling(5).mean()) | (close < close.shift(5) * 0.93)

█ 设计原则:
  - 用 2~3 个条件 `&` / `|` 连接，利用A股独特规律
  - **rank(axis=1, pct=True) 做截面排序**，比绝对阈值更鲁棒
  - 必须有止损退出条件，止损不低于5%
  - .fillna(False) 处理所有 NaN
  - **严禁 `and` / `or`，必须用 `&` / `|`**

█ 严禁: import 除 numpy/pandas 外的模块；reshape操作；数据库/文件IO/网络

█ 策略需求: {topic}
█ 模式: {mode}

只输出代码:

```python
import numpy as np
import pandas as pd

def generate_signals(matrices{factor_param}):
    close = matrices['close']
    open_ = matrices['open']
    high = matrices['high']
    low = matrices['low']
    volume = matrices['volume']
    # A股实战策略逻辑
    return entries, exits
```"""


# ══════════════════════════════════════════════════════════════════════════════
# Prompt: Strategy Optimizer — Round 2+ 爬山优化
# ══════════════════════════════════════════════════════════════════════════════

STRATEGY_OPTIMIZER_PROMPT = """你是 OpenClaw 量化策略优化专家。目标: 在基线基础上提升 Sharpe > 0, 提升胜率。

█ 基线代码:
```python
{baseline_code}
```

█ 当前回测指标:
{baseline_metrics}

█ 诊断反馈:
{feedback}

█ 优化决策矩阵（按当前指标选择策略）:

  trades < 500:
    → 放宽入场: 减少条件到 2 个，用 rank(pct=True) > 0.8 代替绝对阈值
    → 缩短 rolling 窗口 (20→10→5)

  trades >= 500 但 sharpe < 0:
    → 核心问题: 入场信号质量差，赚钱的少亏钱的多
    → 方案1: 加**趋势过滤** (close > close.rolling(20).mean()) 避免逆势
    → 方案2: 加**动量确认** (pct_change(5) > 0) 只做上涨股
    → 方案3: 收紧出场——加止损 (close < entry * 0.95) 或时间止损 (shift)

  sharpe 在 0~0.3:
    → 微调入场精度: 加一个截面排序过滤 (rank > 0.85)
    → 优化出场时机: 止盈 (close > entry * 1.1) + 止损 (close < entry * 0.95)

  sharpe > 0.3 但 max_drawdown > 15%:
    → 加仓位控制: 限制同时入场的股票数 (每日 entries.sum(axis=1) 限制)
    → 分散入场: 不要集中在单日

  上轮退化/回滚:
    → 不要重复失败方向！换一个完全不同的指标组合
    → 如之前用 MA 交叉，改用 RSI 超买超卖；之前用突破，改用均值回复

█ 核心技巧:
  - 用 rank(axis=1, pct=True) 做截面排序比绝对数值鲁棒
  - 出场必须有止损逻辑（限制单笔亏损）
  - 入场条件不超过 3 个
  - **严禁 `and`/`or`，必须用 `&`/`|`**

█ 环境: A 股 ~6300 只股票 × ~120 个交易日
█ 模式: {mode}

只输出改进后代码:

```python
import numpy as np
import pandas as pd

def generate_signals(matrices{factor_param}):
    close = matrices['close']
    # ... 改进后的逻辑
    return entries, exits
```"""


# ══════════════════════════════════════════════════════════════════════════════
# Prompt: PM 总结报告
# ══════════════════════════════════════════════════════════════════════════════

PM_PROMPT = """你是 OpenClaw 的基金经理 (Portfolio Manager)。

请撰写量化策略研发的决策总结报告。

策略名称: {name}
研发主题: {topic}
迭代轮次: {rounds}
最终状态: {status}

过程记录:
{process_log}

最终回测指标:
{metrics}

请按以下结构输出（每节必须有）：

## 决策
开头用 emoji：✅通过 / ⚠️需优化 / ❌不采纳

## 策略评估
100 字以内，概述逻辑、引用关键指标数字、主要风险。

## 优化方向
无论通过与否，给出 2~4 条可执行的**下一步优化建议**，每条一行：
- 具体方向（如「加入止损逻辑」「减少入场条件到 2 个」「换用 RSI 替代 MA 交叉」）
- 预期改善的指标（如「预计降低 max_drawdown」「提高 sharpe」）
- 如果 sharpe<0，重点分析原因（过度交易? 逆势? 滑点?）并给出对策

## 迭代参考
用 JSON 格式给出建议的下一轮研发参数:
```json
{{"topic": "改进主题描述", "focus": "优化重点关键词", "constraints": "约束条件"}}
```"""


# ══════════════════════════════════════════════════════════════════════════════
# Prompt: Factor Mining — 因子挖掘
# ══════════════════════════════════════════════════════════════════════════════

FACTOR_GENERATOR_PROMPT = """你是A股量化因子工程师，专注于挖掘A股市场的独特alpha来源。

█ 数据接口 — generate_factor(matrices):
  matrices = {{
    'open': DataFrame (index=trade_date ~120行, columns=ts_code ~6300列, float64),
    'high': DataFrame, 'low': DataFrame,
    'close': DataFrame, 'volume': DataFrame,
  }}

█ 返回值:
  return factor_score  # DataFrame, shape 同 close, 连续数值
  引擎会自动做截面排序: 排名前 5% 入场，低于 80% 出场

█ 因子设计思路: {topic}

█ A股有效因子类型 (优先使用):
  - 资金流向: volume*(2*close-high-low)/(high-low+1e-8) 的累积→主力资金方向
  - 筹码结构: 缩量上涨=筹码锁定, close.pct_change()/(volume/volume.rolling(20).mean()+1e-8)
  - 涨停效应: close/close.shift(1)>=1.095 判断涨停, 涨停日的volume特征
  - 竞价强度: (open-close.shift(1))/close.shift(1) 开盘跳空蕴含的多空信息
  - 情绪周期: 截面涨跌停家数比、市场宽度(站上均线比例)作为择时因子
  - 板块轮动: 个股rank的时序变化速度→捕捉资金轮动方向
  - 突破质量: 创N日新高+放量+收盘在日内高位→有效突破概率高

█ 避免的低效因子:
  ✗ 纯波动率(close.pct_change().rolling().std())
  ✗ 纯动量(close.pct_change(N))
  ✗ 简单Z-score((close-mean)/std)

█ 规范:
  - 只用 numpy, pandas
  - 必须组合至少2个数据维度(价格+成交量+形态)
  - factor_score 中 NaN 会被忽略，有效值越多越好

只输出代码:

```python
import numpy as np
import pandas as pd

def generate_factor(matrices):
    close = matrices['close']
    open_ = matrices['open']
    high = matrices['high']
    low = matrices['low']
    volume = matrices['volume']
    # A股特色因子计算
    return factor_score
```"""

FACTOR_OPTIMIZER_PROMPT = """你是 OpenClaw 因子挖掘优化专家。在基线因子上做改进。

█ 基线代码:
```python
{baseline_code}
```

█ 因子评估指标:
{baseline_metrics}

█ 反馈:
{feedback}

█ 优化方向:
  - IC (信息系数) < 0.02 → 因子区分度太弱，换计算方式或组合多个子因子
  - IC_IR < 0.3 → 因子不稳定，考虑加平滑(rolling mean)或时间衰减加权
  - sharpe < 0 → 因子方向可能反了，尝试取负值
  - trades < 20 → entry_pct 过高或因子有效值太少，检查 NaN 比例

只输出改进后代码:

```python
import numpy as np
import pandas as pd

def generate_factor(matrices):
    close = matrices['close']
    # ... 改进
    return factor_score
```"""


# ══════════════════════════════════════════════════════════════════════════════
# 辅助函数
# ══════════════════════════════════════════════════════════════════════════════

_REQUIRED_IMPORTS = "import numpy as np\nimport pandas as pd\n\n"


def _ensure_imports(code: str) -> str:
    """确保代码包含 numpy/pandas import（LLM 经常遗漏）。"""
    has_np = re.search(r"^\s*import\s+numpy", code, re.MULTILINE)
    has_pd = re.search(r"^\s*import\s+pandas", code, re.MULTILINE)
    if has_np and has_pd:
        return code
    prefix = ""
    if not has_np:
        prefix += "import numpy as np\n"
    if not has_pd:
        prefix += "import pandas as pd\n"
    return prefix + "\n" + code


_DF_CONTEXT_MARKERS = (
    ".shift(", ".rolling(", ".fillna(", ".mean(", ".std(", ".max(", ".min(",
    ".pct_change(", ".diff(", ".rank(", ".cumsum(", ".cumprod(",
    "matrices[", "close", "open_", "high", "low", "volume",
    "entries", "exits", "factor_dfs",
)


def _fix_dataframe_operators(code: str) -> str:
    """将 LLM 常犯的 Python and/or 替换为 DataFrame 元素级 &/|。

    只对包含 DataFrame 上下文标记的行做替换，避免误改普通 Python 逻辑。
    对于 generate_signals/generate_factor 内的赋值行和 return 行，
    DataFrame 布尔运算永远应该用 &/| 而非 and/or。
    """
    lines = code.split("\n")
    fixed = []
    for line in lines:
        if (" and " in line or " or " in line) and any(m in line for m in _DF_CONTEXT_MARKERS):
            stripped = line.lstrip()
            # Skip 'if'/'elif'/'while' guard clauses (scalar bool context)
            if not stripped.startswith(("if ", "elif ", "while ")):
                line = re.sub(r"\band\b", "&", line)
                line = re.sub(r"\bor\b",  "|", line)
        fixed.append(line)
    return "\n".join(fixed)


def _sanitize_code(code: str) -> str:
    """Apply all post-processing fixes to LLM-generated code."""
    code = _ensure_imports(code)
    code = _fix_dataframe_operators(code)
    return code


def _extract_generate_signals(llm_reply: str) -> str:
    """从 LLM 回复中提取 generate_signals 函数代码。"""
    code_match = re.search(r"```python\s*(.*?)\s*```", llm_reply, re.DOTALL)
    code = code_match.group(1) if code_match else llm_reply

    fn_match = re.search(
        r"((?:import\s+\w+\s*\n)*\s*def\s+generate_signals\(.*?\n(?:[ \t]+.*(?:\n|$))*)",
        code,
    )
    if fn_match:
        return _sanitize_code(fn_match.group(1).rstrip())

    if "def generate_signals" in code:
        return _sanitize_code(code.strip())

    return _sanitize_code(code.strip())


def _validate_alpha_code(code: str, fn_name: str = "generate_signals") -> list[str]:
    """轻量级校验 — 捕获明显错误，避免浪费沙箱调用。"""
    issues = []
    if f"def {fn_name}" not in code:
        issues.append(f"缺少 def {fn_name} 函数定义")
    if "return" not in code:
        issues.append("函数缺少 return 语句")

    banned = re.findall(
        r"\b(clickhouse_connect|dolphindb|query_df|open\s*\(|exec\s*\(|eval\s*\(|__import__)\b",
        code, re.IGNORECASE,
    )
    if banned:
        issues.append(f"禁止的操作: {banned}")

    if re.search(r"\.(stack|unstack|melt|pivot|pivot_table)\s*\(", code):
        issues.append("禁止使用 stack/unstack/melt/pivot")

    if re.search(r"\bimport\s+(os|sys|subprocess|socket|shutil|pathlib|vectorbt)\b", code):
        issues.append("禁止导入系统/回测框架模块")

    # Detect residual `and`/`or` that _fix_dataframe_operators might have missed
    for i, line in enumerate(code.split("\n"), 1):
        if (" and " in line or " or " in line) and any(m in line for m in _DF_CONTEXT_MARKERS):
            issues.append(f"第{i}行: DataFrame 布尔运算使用了 and/or (应用 &/|)")
            break

    return issues


def _composite_score(m: dict) -> float:
    """多维综合评分。

    权重: Sharpe 50% + 胜率 20% + 收益 15% + 回撤惩罚 15%
    交易量不足直接给低分（但不是 -999，允许低交易量策略参与竞争）。
    """
    sharpe = m.get("sharpe") or 0
    wr = m.get("win_rate") or 0
    dd = abs(m.get("max_drawdown") or 0)
    trades = m.get("trades") or 0
    ann_ret = m.get("annualized_return") or m.get("total_return") or 0

    if trades < 10:
        return -999

    trade_factor = min(1.0, trades / MIN_TRADES_THRESHOLD)
    wr_norm = wr / 100 if wr > 1 else wr
    ret_norm = min(ann_ret / 50, 1.0) if ann_ret > 0 else ann_ret / 50
    dd_penalty = max(0, dd - 10) * 0.01

    score = (sharpe * 0.50 + wr_norm * 0.20 + ret_norm * 0.15 - dd_penalty * 0.15) * trade_factor
    return score


def _hill_climb_decision(new_metrics: dict, best_metrics: dict) -> tuple[bool, str]:
    """多维爬山裁决 — 宽容模式，只要有改进就接受。"""
    new_trades = new_metrics.get("trades", 0) or 0
    new_sharpe = new_metrics.get("sharpe") or -99
    new_dd = abs(new_metrics.get("max_drawdown") or 0)
    new_wr = new_metrics.get("win_rate") or 0

    if new_trades < 10:
        return False, f"交易次数过少: {new_trades} < 10"

    if new_dd > 60:
        return False, f"回撤过大: {new_dd:.1f}% > 60%"

    new_score = _composite_score(new_metrics)
    best_score = _composite_score(best_metrics)

    parts = [f"sharpe {new_sharpe:.3f}"]
    if new_wr:
        parts.append(f"胜率 {new_wr:.1f}%")
    if new_dd:
        parts.append(f"回撤 {new_dd:.1f}%")
    parts.append(f"trades {new_trades}")

    if best_score <= -999:
        return True, f"首次有效结果: {' | '.join(parts)} (score {new_score:.3f})"

    if new_score > best_score:
        return True, f"进化成功: {' | '.join(parts)} (score {new_score:.3f} > {best_score:.3f})"

    if new_score > best_score - 0.05:
        return False, f"微退化: score {new_score:.3f} ≈ 基线 {best_score:.3f}, 保持基线"

    return False, f"退化: score {new_score:.3f} < 基线 {best_score:.3f} ({' | '.join(parts)})"


def _build_diagnostic_feedback(metrics: dict, reason: str) -> str:
    """构建结构化诊断反馈供 Optimizer 使用。"""
    trades = metrics.get("trades", 0) or 0
    sharpe = metrics.get("sharpe") or 0
    wr = metrics.get("win_rate") or 0
    dd = abs(metrics.get("max_drawdown") or 0)
    ann_ret = metrics.get("annualized_return") or metrics.get("total_return") or 0

    lines = [reason]
    if trades == 0:
        lines.append("诊断: 0 笔交易 — 入场条件太严，减少到 2 个条件或用 rank(pct=True) > 0.8")
    elif trades < MIN_TRADES_THRESHOLD:
        lines.append(f"诊断: 仅 {trades} 笔交易(目标≥{MIN_TRADES_THRESHOLD}) — 用截面排序 rank > 0.8 放宽入场")
    elif sharpe < 0:
        lines.append(f"诊断: sharpe={sharpe:.3f}<0, 年化收益={ann_ret:.1f}%")
        lines.append("核心: 组合层面亏损，需要:")
        lines.append("  1. 加趋势过滤 (close > MA20) 避免逆势入场")
        lines.append("  2. 加止损 (close < 入场价*0.95) 限制单笔亏损")
        lines.append("  3. 或反转策略方向（如突破改均值回复）")
    elif sharpe < 0.3:
        lines.append(f"诊断: sharpe={sharpe:.3f} 偏低(目标>0.3)")
        lines.append("建议: 收紧入场精度 (rank > 0.9) 或优化出场止盈 (涨幅>8%退出)")
    else:
        lines.append(f"诊断: sharpe={sharpe:.3f} 可接受")
        if wr < 45:
            lines.append(f"优化方向: 胜率={wr:.1f}%偏低，尝试加强入场确认")
        if dd > 15:
            lines.append(f"优化方向: 回撤={dd:.1f}%偏大，加时间止损")
    return "\n".join(lines)


def _detect_mode(topic: str) -> str:
    """根据用户意图自动检测模式: technical / factor / mining。"""
    lower = topic.lower()
    mining_keywords = ["挖掘", "mining", "因子挖掘", "因子发现", "factor mining",
                       "自动发现", "筛选因子", "因子搜索"]
    for kw in mining_keywords:
        if kw in lower:
            return "mining"
    factor_keywords = ["因子", "factor", "smart_money", "voi", "skewness", "kurtosis", "amihud",
                       "聪明钱", "主力资金", "资金流", "流动性", "偏度", "峰度"]
    for kw in factor_keywords:
        if kw in lower:
            return "factor"
    return "technical"


def _extract_generate_factor(llm_reply: str) -> str:
    """从 LLM 回复中提取 generate_factor 函数代码。"""
    code_match = re.search(r"```python\s*(.*?)\s*```", llm_reply, re.DOTALL)
    code = code_match.group(1) if code_match else llm_reply
    fn_match = re.search(
        r"((?:import\s+\w+\s*\n)*\s*def\s+generate_factor\(.*?\n(?:[ \t]+.*(?:\n|$))*)",
        code,
    )
    if fn_match:
        return _sanitize_code(fn_match.group(1).rstrip())
    if "def generate_factor" in code:
        return _sanitize_code(code.strip())
    return _sanitize_code(code.strip())


# ══════════════════════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════════════════════

async def run_quant_pipeline(
    orchestrator,
    topic: str,
    notify_fn=None,
    base_strategy: dict = None,
    progress_channel: str = "",
    backtest_mode: str = "vectorbt",
    max_rounds: int = MAX_ROUNDS,
) -> dict:
    """
    执行量化研发流水线 (v2 爬山优化架构)。

    流程: Alpha Generator (1次) → 爬山循环 N 轮 (各1次 Optimizer) → PM 总结 (1次)
    总 LLM 调用: max_rounds + 2 次

    Args:
        orchestrator: Orchestrator 实例
        topic: 研究主题
        notify_fn: 进度通知回调
        base_strategy: 可选基础策略 {code, metrics}
        progress_channel: Redis channel
        backtest_mode: 兼容参数 (新架构固定 core_engine)
        max_rounds: 最大迭代轮数
    """
    import redis.asyncio as aioredis
    from agents.llm_router import get_llm_router
    from agents.bridge_client import get_bridge_client

    router = get_llm_router()
    bridge = get_bridge_client()

    _redis = None
    try:
        _redis = aioredis.from_url(os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"))
    except Exception:
        pass

    async def _emit(event_type: str, step: str, title: str, content: str, **extra):
        if _redis and progress_channel:
            evt = {
                "type": event_type, "step": step, "title": title,
                "content": content, "ts": time.time(), **extra,
            }
            try:
                await _redis.publish(progress_channel, json.dumps(evt, ensure_ascii=False, default=str))
            except Exception:
                pass

    async def _notify(text):
        if notify_fn:
            try:
                await notify_fn(text)
            except Exception:
                pass
        logger.info(text)

    mode = _detect_mode(topic)
    is_mining = mode == "mining"
    factor_param = ", factor_dfs" if mode == "factor" else ""
    factors = AVAILABLE_FACTORS if mode == "factor" else []
    fn_name = "generate_factor" if is_mining else "generate_signals"

    start_date = (date.today() - timedelta(days=180)).isoformat()
    end_date = date.today().isoformat()
    strategy_name = topic[:30]

    async def _call_optimizer(code, metrics_dict, feedback_text, system_extra=""):
        if is_mining:
            prompt = FACTOR_OPTIMIZER_PROMPT.format(
                baseline_code=code,
                baseline_metrics=json.dumps(metrics_dict, ensure_ascii=False, indent=2),
                feedback=feedback_text,
            )
            sys_msg = "你是 OpenClaw 因子挖掘优化专家。" + system_extra
        else:
            prompt = STRATEGY_OPTIMIZER_PROMPT.format(
                baseline_code=code,
                baseline_metrics=json.dumps(metrics_dict, ensure_ascii=False, indent=2),
                feedback=feedback_text, mode=mode, factor_param=factor_param,
            )
            sys_msg = "你是 OpenClaw 量化策略优化专家。" + system_extra
        reply = await router.chat([
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": prompt},
        ], task_type="code")
        if reply:
            return (_extract_generate_factor if is_mining else _extract_generate_signals)(reply)
        return None

    process_log = []
    rounds_data: list[dict] = []
    error_history = []

    best_code = ""
    best_metrics: dict = {"sharpe": -99}
    backtest_ever_succeeded = False

    total_steps = max_rounds + 1
    await _notify(f"🔬 量化研发启动: {topic} [模式: {mode}, 最多 {max_rounds} 轮]")
    await _emit("step", f"0/{total_steps}", "Pipeline", f"启动: {topic}", status="running",
                mode=mode, max_rounds=max_rounds)

    # ══════════════════════════════════════════════════════════════════════════
    # Round 1: Alpha Generator — 初始 alpha 生成
    # ══════════════════════════════════════════════════════════════════════════

    is_optimize = bool(base_strategy and base_strategy.get("code"))

    if is_optimize:
        existing_code = base_strategy["code"]
        existing_metrics = base_strategy.get("metrics", {})
        alpha_code = existing_code
        best_code = existing_code
        if existing_metrics:
            best_metrics = existing_metrics
            backtest_ever_succeeded = True
        process_log.append(f"[初始化] 使用已有策略作为基线 (sharpe={best_metrics.get('sharpe', '?')})")
        await _notify(f"📂 使用已有策略作为优化基线")
        await _emit("step", f"1/{total_steps}", "Baseline", "加载已有策略", status="done")
    else:
        if is_mining:
            gen_role = "Factor Miner"
            gen_prompt = FACTOR_GENERATOR_PROMPT.format(topic=topic)
            gen_sys = "你是 OpenClaw 因子挖掘工程师。只输出 generate_factor 函数代码。"
        else:
            gen_role = "Alpha Generator"
            gen_prompt = ALPHA_GENERATOR_PROMPT.format(topic=topic, mode=mode, factor_param=factor_param)
            gen_sys = "你是 OpenClaw 首席量化 Alpha 策略工程师。只输出 generate_signals 函数代码。"

        await _notify(f"🧠 [1/{total_steps}] {gen_role}: 生成初始{'因子' if is_mining else '策略'}")
        await _emit("step", f"1/{total_steps}", gen_role, f"生成 {fn_name} ({mode})", status="running")

        gen_reply = await router.chat([
            {"role": "system", "content": gen_sys},
            {"role": "user", "content": gen_prompt},
        ], task_type="code")

        if not gen_reply:
            await _emit("error", f"1/{total_steps}", gen_role, "LLM 不可用")
            return {"status": "error", "message": f"{gen_role} LLM 不可用", "name": strategy_name}

        alpha_code = (_extract_generate_factor if is_mining else _extract_generate_signals)(gen_reply)
        fn_issues = _validate_alpha_code(alpha_code, fn_name)
        if fn_issues:
            issue_text = "; ".join(fn_issues)
            process_log.append(f"[{gen_role}] 代码校验失败: {issue_text}")
            await _emit("error", f"1/{total_steps}", gen_role, issue_text, status="error")
            return {"status": "error", "message": f"{gen_role} 代码校验失败: {issue_text}", "name": strategy_name}

        process_log.append(f"[{gen_role}] 生成 {len(alpha_code)} 字符代码")
        await _emit("code", f"1/{total_steps}", gen_role, alpha_code[:300], status="done",
                    code_len=len(alpha_code), detail=alpha_code)

    # ══════════════════════════════════════════════════════════════════════════
    # Round 1~N: 爬山循环
    # ══════════════════════════════════════════════════════════════════════════

    feedback = ""
    current_code = alpha_code

    for round_num in range(1, max_rounds + 1):
        step_label = f"{round_num + 1}/{total_steps}"
        await _notify(f"⚙️ [{step_label}] 回测 + 爬山优化 (第 {round_num} 轮)")
        await _emit("step", step_label, "Backtest", f"提交 core_engine 执行 (Round {round_num})",
                    status="running", round=round_num)

        # ── 调用 139 core_engine ──
        try:
            if is_mining:
                resp = await bridge.run_factor_mining(
                    factor_code=current_code,
                    start_date=start_date,
                    end_date=end_date,
                )
            else:
                resp = await bridge.run_alpha(
                    alpha_code=current_code,
                    start_date=start_date,
                    end_date=end_date,
                    mode=mode,
                    factors=factors if mode == "factor" else None,
                )
        except Exception as e:
            resp = {"status": "error", "error": f"Bridge 通信失败: {e}", "metrics": None}

        round_entry: dict = {"round": round_num, "code": current_code, "code_chars": len(current_code)}

        # ── 处理引擎错误 ──
        if resp.get("status") == "error":
            err = resp.get("error", "未知错误")
            feedback = f"引擎错误: {err}"
            error_history.append(f"[Round {round_num}] {feedback}")
            process_log.append(f"[回测 R{round_num}] 错误: {err[:300]}")
            await _emit("step", step_label, "Backtest", f"引擎错误: {err[:300]}", status="error",
                        round=round_num, detail=err)
            round_entry["backtest"] = {"success": False, "error": err[:500]}
            rounds_data.append(round_entry)

            if round_num < max_rounds:
                await _notify(f"🔧 引擎错误，让 Optimizer 修正...")
                new_code = await _call_optimizer(
                    best_code or current_code, best_metrics, feedback, "修正引擎报错的代码。")
                if new_code:
                    current_code = new_code
            continue

        # ── 提取指标（兼容 core_engine 返回格式）──
        metrics = resp.get("metrics") or {}

        _KEY_MAP = {
            "sharpe_ratio": "sharpe", "max_drawdown_pct": "max_drawdown",
            "total_trades": "trades", "win_rate_pct": "win_rate",
            "total_return_pct": "total_return",
            "annualized_return_pct": "annualized_return",
            "annualized_volatility_pct": "annualized_volatility",
            "ic_mean": "ic", "ic_ir": "ic_ir",
        }
        for src, dst in _KEY_MAP.items():
            if src in metrics and dst not in metrics:
                metrics[dst] = metrics[src]
        _METRIC_DEFAULTS = {"sharpe": 0, "max_drawdown": 0, "trades": 0, "win_rate": 0}
        for k, v in list(metrics.items()):
            if isinstance(v, (int, float)) and math.isinf(v) or (isinstance(v, float) and math.isnan(v)):
                metrics[k] = _METRIC_DEFAULTS.get(k, 0)

        required = ("sharpe", "max_drawdown", "trades", "win_rate")
        anomalies = [f"{k}=null" for k in required if metrics.get(k) is None]
        if anomalies:
            anomaly_desc = "; ".join(anomalies)
            feedback = f"指标异常: {anomaly_desc}"
            error_history.append(f"[Round {round_num}] {feedback}")
            process_log.append(f"[回测 R{round_num}] {feedback}")
            await _emit("step", step_label, "Backtest", feedback, status="error",
                        round=round_num, metrics=metrics)
            round_entry["backtest"] = {"success": False, "error": feedback, "metrics": metrics}
            rounds_data.append(round_entry)

            if round_num < max_rounds:
                new_code = await _call_optimizer(
                    best_code or current_code, best_metrics, feedback)
                if new_code:
                    current_code = new_code
            continue

        # ── 爬山裁决 ──
        backtest_ever_succeeded = True

        # 分离 trade_log：保留在 rounds_data 但不在 SSE/process_log 里广播
        trade_log = metrics.pop("trade_log", None)
        is_better, reason = _hill_climb_decision(metrics, best_metrics)

        metrics_text = json.dumps(metrics, ensure_ascii=False, indent=2)
        process_log.append(f"[回测 R{round_num}] {reason} | {metrics_text[:200]}")

        round_entry["backtest"] = {"success": True, "metrics": metrics, "hill_climb": reason}
        if trade_log:
            round_entry["backtest"]["trade_log"] = trade_log
        rounds_data.append(round_entry)

        if is_better:
            best_code = current_code
            best_metrics = metrics.copy()
            feedback = _build_diagnostic_feedback(metrics, reason)
            await _notify(f"📈 R{round_num} {reason}")
            await _emit("metrics", step_label, "Hill Climb",
                        f"✅ {reason}", status="done",
                        round=round_num, metrics=metrics)
        else:
            feedback = _build_diagnostic_feedback(metrics, reason)
            current_code = best_code
            await _notify(f"📉 R{round_num} {reason} → 回滚到基线")
            await _emit("metrics", step_label, "Hill Climb",
                        f"❌ {reason} → 回滚", status="warning",
                        round=round_num, metrics=metrics)

        # ── 生成下一代 ──
        if round_num < max_rounds:
            next_code = await _call_optimizer(best_code, best_metrics, feedback, "在基线上做小步改进。")
            if next_code:
                code_issues = _validate_alpha_code(next_code) if not is_mining else []
                if code_issues:
                    process_log.append(f"[Optimizer R{round_num}] 校验失败: {'; '.join(code_issues)}, 使用基线继续")
                    current_code = best_code
                else:
                    current_code = next_code
                    process_log.append(f"[Optimizer R{round_num}] 生成 {len(next_code)} 字符改进代码")
            else:
                process_log.append(f"[Optimizer R{round_num}] LLM 不可用，使用基线继续")
                current_code = best_code

    # ══════════════════════════════════════════════════════════════════════════
    # PM 总结
    # ══════════════════════════════════════════════════════════════════════════

    final_code = best_code
    final_metrics = best_metrics if backtest_ever_succeeded else {}
    pm_summary = ""

    if not backtest_ever_succeeded:
        error_log = "\n\n".join(f"Round {i+1}: {e}" for i, e in enumerate(error_history)) if error_history else "无详细错误"
        pm_summary = f"回测引擎连续 {max_rounds} 轮均未成功，流水线终止。\n\n错误摘要:\n{error_log[:2000]}"
        final_status = "REJECT"
        await _notify(f"❌ 回测连续 {max_rounds} 轮失败")
        await _emit("step", f"{total_steps}/{total_steps}", "Pipeline", pm_summary[:500], status="error")
    else:
        sharpe = final_metrics.get("sharpe") or 0
        trades = final_metrics.get("trades") or 0
        win_rate = final_metrics.get("win_rate") or 0

        if sharpe > 0.3 and trades >= MIN_TRADES_THRESHOLD and win_rate > 40:
            final_status = "APPROVE"
        elif sharpe > 0 and trades >= 50:
            final_status = "PENDING"
        else:
            final_status = "REJECT"

        await _notify(f"📋 [{total_steps}/{total_steps}] PM 撰写总结")
        await _emit("step", f"{total_steps}/{total_steps}", "Portfolio Manager", "撰写决策总结", status="running")

        pm_reply = await router.chat([
            {"role": "system", "content": "你是 OpenClaw 的基金经理，负责撰写量化策略决策报告。"},
            {"role": "user", "content": PM_PROMPT.format(
                name=strategy_name, topic=topic,
                rounds=max_rounds,
                status=final_status,
                process_log="\n".join(process_log),
                metrics=json.dumps(final_metrics, ensure_ascii=False, indent=2),
            )},
        ], task_type="analysis")

        pm_summary = pm_reply or "PM 总结不可用"
        process_log.append(f"[PM] {pm_summary[:300]}")
        await _emit("step", f"{total_steps}/{total_steps}", "Portfolio Manager",
                    pm_summary[:500], status="done")

    # 从 PM 总结中提取迭代参考 JSON
    optimization_hint = {}
    try:
        import re as _re
        _json_match = _re.search(r'```json\s*(\{.*?\})\s*```', pm_summary, _re.DOTALL)
        if _json_match:
            optimization_hint = json.loads(_json_match.group(1))
    except Exception:
        pass

    # ══════════════════════════════════════════════════════════════════════════
    # 决策账本归档 (Bridge + Redis)
    # ══════════════════════════════════════════════════════════════════════════

    ledger_path = ""
    try:
        ledger_resp = await bridge.save_strategy(
            title=strategy_name,
            topic=topic,
            strategy_code=final_code,
            backtest_metrics=final_metrics,
            decision_report=pm_summary,
            status=final_status,
            model_used="qwen3-coder-plus",
            attempts=max_rounds,
            rounds_data=rounds_data,
        )
        if isinstance(ledger_resp, dict):
            ledger_path = ledger_resp.get("path", "") or ledger_resp.get("id", "")
    except Exception as e:
        logger.warning("Save strategy failed: %s", e)

    # 构建最终文本
    if backtest_ever_succeeded:
        status_emoji = {"APPROVE": "✅", "PENDING": "⚠️", "REJECT": "❌"}.get(final_status, "❓")
        final_text = (
            f"{status_emoji} 量化研发完成: {strategy_name}\n"
            f"状态: {final_status} | 迭代: {max_rounds}轮 | 模式: {mode}\n\n"
        )
        if final_metrics:
            final_text += "📊 最终指标:\n"
            for k, v in final_metrics.items():
                if k != "sharpe" or v != -99:
                    final_text += f"  {k}: {v}\n"
        final_text += f"\n📋 PM 总结:\n{pm_summary}\n"
    else:
        final_text = (
            f"❌ 量化研发失败: {strategy_name}\n"
            f"回测连续 {max_rounds} 轮未通过 | 错误 {len(error_history)} 条\n\n"
            f"📋 错误摘要:\n{pm_summary[:1000]}\n"
        )
    if ledger_path:
        final_text += f"\n📝 账本: {ledger_path}"

    result = {
        "status": final_status,
        "name": strategy_name,
        "topic": topic,
        "mode": mode,
        "metrics": final_metrics,
        "code": final_code,
        "attempts": max_rounds,
        "summary": final_text,
        "process_log": process_log,
        "error_history": error_history,
        "pm_summary": pm_summary,
        "ledger_path": ledger_path,
        "optimization_hint": optimization_hint,
    }

    await _emit("done", f"{total_steps}/{total_steps}", "完成", final_text, result=result)

    if _redis:
        try:
            record = {
                "id": f"research_{int(time.time())}",
                "type": "research",
                "title": strategy_name,
                "topic": topic,
                "mode": mode,
                "status": final_status,
                "metrics": final_metrics,
                "code": final_code,
                "attempts": max_rounds,
                "pm_summary": pm_summary,
                "optimization_hint": optimization_hint,
                "process_log": process_log,
                "created_at": date.today().isoformat(),
                "ts": time.time(),
            }
            await _redis.lpush("rragent:quant_records", json.dumps(record, ensure_ascii=False, default=str))
            await _redis.ltrim("rragent:quant_records", 0, 99)
            await _redis.close()
        except Exception as e:
            logger.warning("Save quant record failed: %s", e)

    return result

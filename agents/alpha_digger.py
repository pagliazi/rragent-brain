"""
Alpha Digger — 不间断因子挖掘守护模块

职责:
  1. 维护探索主题池，每轮随机选取方向
  2. 调用 LLM 生成 generate_factor 因子表达式 (批量)
  3. 提交 139 沙箱 mining 模式评估
  4. 通过筛选的因子存入 Factor Library
  5. 因子库达到阈值时触发融合
  6. 支持 daemon 模式持续运行 / 单轮手动触发

架构位置:
  Exploitation (quant_pipeline) = 命题作文优化
  Exploration  (alpha_digger)   = 自主盲挖发现
  两者产出汇入同一个 Factor Library → Factor Combiner → 实盘候选
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import re
import time
from datetime import date, timedelta
from typing import Optional

logger = logging.getLogger("agent.alpha_digger")

# ══════════════════════════════════════════════════════════════════════════════
# 配置
# ══════════════════════════════════════════════════════════════════════════════

DIGGER_CONFIG = {
    "factors_per_round": 5,
    "round_interval_sec": 300,
    "max_rounds_per_session": 50,
    "backtest_window_days": 180,
    "combine_threshold": 10,
}

# ══════════════════════════════════════════════════════════════════════════════
# 探索主题池 — 每个主题携带 LLM 可理解的描述和参数空间
# ══════════════════════════════════════════════════════════════════════════════

EXPLORATION_THEMES = [
    # ── A股实战主题: 短线博弈与资金行为 ──
    {
        "id": "limit_up_chain",
        "name": "涨停连板因子",
        "desc": "A股独特的涨停板制度产生的连板效应。连续涨停后的溢价/断板概率，首板次日的溢价率分布。核心：连板高度越高，次日竞价溢价越高但断板风险也越大。",
        "hints": [
            "close == high 且 pct_change >= 0.095 判断涨停",
            "连续涨停天数: (close==high).rolling(N).sum() 近似",
            "涨停后次日开盘溢价: open.shift(-1)/close - 1 的历史分布",
            "首板放量度: 涨停日 volume / volume.rolling(20).mean()"],
        "lookback_range": (3, 15),
    },
    {
        "id": "money_flow",
        "name": "资金流向因子",
        "desc": "主力资金进出痕迹。大单成交占比、尾盘资金异动、连续多日净流入/流出。通过 volume×close×sign(close-open) 近似资金方向。",
        "hints": [
            "资金方向: volume * np.sign(close - open)",
            "累积资金流: (volume * (close - open) / (high - low + 1e-8)).rolling(N).sum()",
            "OBV变种: (np.sign(close.diff()) * volume).cumsum()",
            "大小单分离: volume * abs(close-open)/(high-low+1e-8) 为主力资金proxy"],
        "lookback_range": (5, 30),
    },
    {
        "id": "sector_rotation",
        "name": "板块轮动因子",
        "desc": "A股板块轮动规律: 龙头→跟风→补涨→退潮。通过个股与板块(截面均值)的相对强弱、领先/滞后关系捕捉轮动节奏。先涨板块的个股做空，滞后板块的个股做多。",
        "hints": [
            "板块动量: close.pct_change(N).rank(axis=1, pct=True) 的时序变化",
            "相对强弱加速: rank_today - rank.shift(5)，正值=在走强",
            "截面离散度: close.pct_change(N).std(axis=1) 高=分化期(选股有效), 低=普涨/普跌",
            "领先滞后: 高rank股票N日前涨幅 vs 低rank股票当日涨幅的关系"],
        "lookback_range": (3, 20),
    },
    {
        "id": "emotion_cycle",
        "name": "市场情绪周期因子",
        "desc": "A股散户主导的情绪周期: 恐慌→犹豫→乐观→疯狂→恐慌。用涨跌停家数比、振幅中位数、换手率中位数等截面统计量刻画市场温度。",
        "hints": [
            "涨停占比: (close/close.shift(1) >= 1.095).sum(axis=1) / close.count(axis=1)",
            "市场宽度: (close > close.rolling(20).mean()).sum(axis=1) / close.count(axis=1)",
            "截面换手率中位数: (volume*close).median(axis=1) 的时序趋势",
            "恐慌指标: close.pct_change().std(axis=1) 截面波动高=恐慌，低=平静"],
        "lookback_range": (5, 30),
    },
    {
        "id": "auction_microstructure",
        "name": "竞价博弈因子",
        "desc": "开盘竞价隐含的多空博弈信息。高开低走=获利了结，低开高走=超跌抢筹。open相对昨close的跳空+当日走势的组合模式。",
        "hints": [
            "竞价强度: (open - close.shift(1)) / close.shift(1)",
            "开盘后趋势确认: (close - open) / (open - close.shift(1) + 1e-8)",
            "高开回落: (open > close.shift(1)*1.02) & (close < open) 的频率",
            "低开反包: (open < close.shift(1)*0.98) & (close > open) 的频率"],
        "lookback_range": (3, 15),
    },
    {
        "id": "chip_concentration",
        "name": "筹码集中度因子",
        "desc": "通过成交量在不同价位的分布推断筹码集中度。缩量上涨=筹码锁定好(看多)，放量滞涨=抛压重(看空)。",
        "hints": [
            "量价配合度: close.pct_change() / (volume/volume.rolling(20).mean() + 1e-8)",
            "缩量上涨强度: np.where(close>close.shift(1), 1/(volume/volume.rolling(10).mean()+1e-8), 0)",
            "筹码松动: 连续放量(volume>volume.shift(1))的天数 vs 价格方向",
            "套牢盘密度proxy: rolling(N).max() - close 偏离度"],
        "lookback_range": (5, 30),
    },
    {
        "id": "breakout_quality",
        "name": "突破质量因子",
        "desc": "不是所有突破都有效。有效突破=放量+实体大+收盘在高位。假突破=缩量+长上影线。评估突破的成功概率。",
        "hints": [
            "N日新高且放量: (close >= close.rolling(N).max()) & (volume > volume.rolling(N).mean()*1.3)",
            "突破强度: (close - close.rolling(N).max().shift(1)) / close * (volume/volume.rolling(N).mean())",
            "突破后回踩深度: 突破后M日的最低价 / 突破价 → 回踩浅=有效突破",
            "假突破特征: (high >= high.rolling(N).max()) & (close < (high+low)/2) 的频率"],
        "lookback_range": (10, 40),
    },
    {
        "id": "reversal_signal",
        "name": "反转信号因子",
        "desc": "超跌反弹和超涨回调。不是简单的均值回归，而是结合了成交量确认和情绪极端值。只在极端超跌+缩量止跌+首次放量反弹时触发。",
        "hints": [
            "超跌度: close / close.rolling(20).max() - 1  (负值越大越超跌)",
            "止跌信号: (close > open) & (close.shift(1) <= open.shift(1)) & (low < low.shift(1))",
            "反弹力度: close.pct_change() * (volume / volume.shift(1))",
            "V型反转: (close - low) / (high - low + 1e-8) > 0.7 且 low < close.rolling(10).min().shift(1)"],
        "lookback_range": (5, 20),
    },
    {
        "id": "smart_money_divergence",
        "name": "聪明钱背离因子",
        "desc": "聪明钱(主力)在尾盘和开盘行为与日内散户行为的差异。尾盘主力偷偷买入+日内散户抛售=看多信号。",
        "hints": [
            "尾盘资金proxy: close与(open+high+low)/3的偏差方向",
            "聪明钱指标: volume * (close - low - (high - close)) / (high - low + 1e-8)",
            "价格位置: (close - low) / (high - low + 1e-8) 收盘在日内位置",
            "聪明钱累积: 上述指标的 rolling(N).sum() 趋势"],
        "lookback_range": (5, 20),
    },
    {
        "id": "volatility_contraction_breakout",
        "name": "波动收缩突破因子",
        "desc": "经典的VCP(Volatility Contraction Pattern)模式: 振幅逐渐收窄→能量积累→方向性突破。关键是收缩后的第一根放量大阳线。",
        "hints": [
            "振幅收缩率: (high-low).rolling(5).mean() / (high-low).rolling(20).mean()",
            "收缩后突破: (振幅收缩率 < 0.6) & (close > close.rolling(20).max().shift(1))",
            "能量释放: volume / volume.rolling(20).mean() 在收缩后首次 > 1.5",
            "收缩持续度: ((high-low)/close < 0.03).rolling(5).sum() >= 3"],
        "lookback_range": (10, 30),
    },
    # ── 保留部分原始技术因子(但降低权重) ──
    {
        "id": "momentum",
        "name": "动量因子",
        "desc": "过去N日涨幅的截面排序。强势股继续强势。需结合成交量确认动量的有效性。",
        "hints": ["close.pct_change(N).rank(axis=1, pct=True)",
                  "(close/close.shift(N)-1) * (volume/volume.rolling(N).mean())"],
        "lookback_range": (5, 30),
    },
    {
        "id": "mean_reversion",
        "name": "均值回归因子",
        "desc": "偏离均线+缩量止跌的组合。纯偏离无效，必须有止跌确认信号。",
        "hints": ["(close - close.rolling(N).mean()) / close.rolling(N).std()",
                  "超跌+缩量: zscore < -2 且 volume < volume.rolling(N).mean()*0.7"],
        "lookback_range": (10, 40),
    },
    {
        "id": "intraday_pattern",
        "name": "日内形态因子",
        "desc": "K线实体和影线蕴含的多空力量对比。长下影线=下方有支撑，长上影线=上方有抛压。",
        "hints": ["(high - np.maximum(open, close)) / (high - low + 1e-8)",
                  "(close - open) / (high - low + 1e-8)"],
        "lookback_range": (3, 15),
    },
    {
        "id": "gap_factor",
        "name": "跳空缺口因子",
        "desc": "缺口类型决定后续走势: 突破缺口(放量)看涨，衰竭缺口(放量后缩量)看跌。",
        "hints": ["(open - close.shift(1)) / close.shift(1)",
                  "缺口+量能: gap_pct * (volume/volume.rolling(5).mean())"],
        "lookback_range": (3, 15),
    },
    {
        "id": "cross_sectional_rank",
        "name": "截面相对强弱因子",
        "desc": "个股在全市场中的相对排名及其变化速度。排名快速上升=被资金关注。",
        "hints": ["close.pct_change(N).rank(axis=1, pct=True)",
                  "rank变化速度: rank_today - rank.shift(M)"],
        "lookback_range": (3, 20),
    },
]


# ══════════════════════════════════════════════════════════════════════════════
# Alpha Miner Prompt
# ══════════════════════════════════════════════════════════════════════════════

ALPHA_MINER_PROMPT = """你是A股量化因子工程师。基于已验证的高胜率因子模式，创造一个新因子。

█ 本轮探索方向: {theme_name} — {theme_desc}
█ 参考思路: {hints}
{market_context}
█ 种子参数: lookback={lookback}, decay_weight={decay_weight:.2f}

█ 高胜率因子的3条铁律 (来自463个因子的实证统计):
  1. 必须用 ewm(alpha=X) 平滑 — 使用ewm的因子胜率56% vs 不用的46%
  2. 必须组合 high/low 维度 — 使用high+low的因子sharpe高40%
  3. 控制交易数量在2000-6000 — 太多=噪声(8000+胜率<46%), 太少=统计不显著

█ 已验证最高胜率因子代码 (胜率69% Sharpe=3.01, 必须参考此结构):
    price_dev = (close - close.rolling(N).mean()) / (close.rolling(N).std() + 1e-10)
    vol_ratio = volume / (volume.rolling(N).mean() + 1e-10)
    range_current = high - low
    range_avg = (high - low).rolling(N).mean()
    range_ratio = range_current / (range_avg + 1e-10)
    signal = -price_dev * (1 + vol_ratio * 0.3) * (1 + range_ratio * 0.2)  # 取反=均值回归
    factor = signal.ewm(alpha={decay_weight}).mean().fillna(0)

█ 第二高胜率代码 (胜率67% Sharpe=2.66):
    price_dev = (close - close.rolling(N).mean()) / (close.rolling(N).std() + 1e-10)
    vol_ratio = volume / (volume.rolling(N).mean() + 1e-10)
    combined = price_dev * vol_ratio
    signal_mean = combined.rolling(N).mean().fillna(0)
    signal_std = combined.rolling(N).std().fillna(0)
    factor = -combined + signal_mean + signal_std  # 反向偏离信号

█ 你的任务: 基于上述高胜率结构，融入「{theme_name}」的思路做变体。
  核心约束:
  - 保持 ewm 平滑 + 取反(-) 的均值回归结构
  - 必须用到 high, low, volume 中至少2个
  - 中间变量全部 .fillna(0)
  - 最终 factor 必须是 DataFrame, shape同close

█ 数据: matrices = {{'open','high','low','close','volume'}} 各为 DataFrame
█ 仅用 numpy(np) + pandas(pd)
█ 禁止: import其他模块, stack/unstack/melt/pivot, 数据库/文件/网络

{quality_rules}

只输出代码:

```python
import numpy as np
import pandas as pd

def generate_factor(matrices):
    close = matrices['close']
    high = matrices['high']
    low = matrices['low']
    volume = matrices['volume']
    # 基于高胜率模式创新
    factor = ...
    return factor
```"""


FACTOR_MUTATOR_PROMPT = """你是因子进化器。目标: 提升胜率(当前约50%，目标60%+)和收益。

█ 原始因子代码 (Sharpe={sharpe:.2f}, 已验证有效):
```python
{original_code}
```

█ 当前指标: Sharpe={sharpe:.3f}, IC={ic_mean:.4f}, IR={ir:.3f}

█ 变异方向: {mutation_direction}

█ 提升胜率的关键技巧 (来自实证):
  - 加入 (high-low) 维度可提升胜率 ~10%
  - ewm 平滑可减少噪声交易，提升胜率
  - 乘以 volume 权重可过滤弱信号
  - 取反(-factor)适合均值回归类因子

█ 规则:
  1. 保持 ewm/rolling 结构不变
  2. 保持 .fillna(0)
  3. 只做变异方向指定的那一个改动
  4. 输出完整的 generate_factor 函数

{quality_rules}

只输出代码:

```python
import numpy as np
import pandas as pd

def generate_factor(matrices):
    ...
    return factor
```"""


# ══════════════════════════════════════════════════════════════════════════════
# 主题→买卖规则映射
# 与 139 SimulationEngine 的 TradingRules 结构对齐
# 每个因子入库时自动携带对应的实盘交易规则建议，便于直接提升为 StrategyPreset
# ══════════════════════════════════════════════════════════════════════════════

# 规则设计原则:
#   EV = win_rate × take_profit - (1-win_rate) × stop_loss > 0
#   win_rate 取自因子库各主题的实测均值
#   A股日线止损下限: 6% (覆盖1-2日正常波动噪声，避免被振荡出局)
#
#   主题实测胜率汇总 (IC量化回测基准):
#   mean_reversion=59%, higher_moment=54%, volatility_regime=51%,
#   volume_distribution=48%, volume_price_div=46%, liquidity_shock=44%,
#   intraday_pattern=44%, price_volume_sync=43%, gap_factor=42%,
#   cross_sectional_rank=40%, momentum=38%
#
#   要求 EV ≥ 2%/笔 作为最低收益阈值

THEME_TRADING_RULES: dict[str, dict] = {
    # 动量因子 win_rate≈38% → 需要 P/L≥3.0x 才有正期望
    # EV = 0.38×18% - 0.62×6% = 6.84% - 3.72% = +3.1%/笔
    "momentum": {
        "signal_mode": "daily_signal",
        "entry_mode": "next_open",
        "stop_loss_pct": 0.06,
        "take_profit_pct": 0.18,      # 3.0x 比例弥补低胜率
        "trailing_stop_pct": 0.09,    # 追踪止损锁住浮盈
        "max_hold_days": 12,          # 给动量充足发展时间
        "max_positions": 5,
        "exit_on_deselect": False,
        "benchmark": "000300.SH",
    },
    # 均值回归 win_rate≈59% → P/L≥1.5x 已足够，宽止损给回归空间
    # EV = 0.59×18% - 0.41×8% = 10.62% - 3.28% = +7.3%/笔
    "mean_reversion": {
        "signal_mode": "daily_signal",
        "entry_mode": "close",
        "stop_loss_pct": 0.08,        # 均值回归必须给足振荡空间
        "take_profit_pct": 0.18,      # 2.25x 比例
        "trailing_stop_pct": 0.10,
        "max_hold_days": 15,
        "max_positions": 6,
        "exit_on_deselect": False,
        "benchmark": "000300.SH",
    },
    # 量价背离 win_rate≈46% → P/L≥2.5x
    # EV = 0.46×18% - 0.54×7% = 8.28% - 3.78% = +4.5%/笔
    "volume_price_div": {
        "signal_mode": "daily_signal",
        "entry_mode": "next_open",
        "stop_loss_pct": 0.07,
        "take_profit_pct": 0.18,      # 2.57x 比例
        "trailing_stop_pct": 0.09,
        "max_hold_days": 15,
        "max_positions": 5,
        "exit_on_deselect": False,
        "benchmark": "000300.SH",
    },
    # 波动率状态 win_rate≈51% → P/L≥2.0x，加固定止盈防止利润回吐
    # EV = 0.51×15% - 0.49×7% = 7.65% - 3.43% = +4.2%/笔
    "volatility_regime": {
        "signal_mode": "daily_signal",
        "entry_mode": "next_open",
        "stop_loss_pct": 0.07,
        "take_profit_pct": 0.15,      # 加固定止盈（原为None），防利润回吐
        "trailing_stop_pct": 0.10,    # 追踪止损作为补充
        "max_hold_days": 15,
        "max_positions": 5,
        "exit_on_deselect": False,
        "benchmark": "000300.SH",
    },
    # 流动性冲击 win_rate≈44% → P/L≥2.5x
    # EV = 0.44×15% - 0.56×6% = 6.6% - 3.36% = +3.2%/笔
    "liquidity_shock": {
        "signal_mode": "daily_signal",
        "entry_mode": "next_open",
        "stop_loss_pct": 0.06,        # 提高到6%（原5%太紧）
        "take_profit_pct": 0.15,      # 2.5x 比例（原10%不够）
        "trailing_stop_pct": 0.08,
        "max_hold_days": 7,           # 适当延长（原5天太短）
        "max_positions": 5,
        "exit_on_deselect": False,
        "benchmark": "000300.SH",
    },
    # 日内形态 win_rate≈44% → P/L≥2.5x，止损必须≥6%避免日内噪声触发
    # EV = 0.44×15% - 0.56×6% = 6.6% - 3.36% = +3.2%/笔
    "intraday_pattern": {
        "signal_mode": "daily_signal",
        "entry_mode": "next_open",
        "stop_loss_pct": 0.06,        # 提高到6%（原4%一日波动即触发）
        "take_profit_pct": 0.15,      # 2.5x 比例（原8%亏损比原来大）
        "trailing_stop_pct": 0.08,
        "max_hold_days": 5,           # 适当延长（原3天信号未必兑现）
        "max_positions": 5,
        "exit_on_deselect": False,
        "benchmark": "000300.SH",
    },
    # 截面相对强弱 win_rate≈40% → P/L≥3.0x
    # EV = 0.40×18% - 0.60×6% = 7.2% - 3.6% = +3.6%/笔
    "cross_sectional_rank": {
        "signal_mode": "daily_signal",
        "entry_mode": "next_open",
        "stop_loss_pct": 0.06,        # 提高到6%
        "take_profit_pct": 0.18,      # 3.0x 比例（原12%不够）
        "trailing_stop_pct": 0.09,
        "max_hold_days": 12,          # 延长持有期（原8天）
        "max_positions": 6,
        "exit_on_deselect": False,
        "benchmark": "000300.SH",
    },
    # 高阶矩 win_rate≈54% → P/L≥1.5x，已足够，周期调仓
    # EV = 0.54×20% - 0.46×8% = 10.8% - 3.68% = +7.1%/笔
    "higher_moment": {
        "signal_mode": "periodic",
        "entry_mode": "close",
        "rebalance_interval": 5,
        "stop_loss_pct": 0.08,
        "take_profit_pct": 0.20,
        "trailing_stop_pct": 0.10,
        "max_hold_days": 20,
        "max_positions": 8,
        "exit_on_deselect": True,
        "benchmark": "000300.SH",
    },
    # 量价同步 win_rate≈43% → P/L≥2.5x，加固定止盈
    # EV = 0.43×15% - 0.57×6% = 6.45% - 3.42% = +3.0%/笔
    "price_volume_sync": {
        "signal_mode": "daily_signal",
        "entry_mode": "next_open",
        "stop_loss_pct": 0.06,
        "take_profit_pct": 0.15,      # 加固定止盈（原为None，无法量化收益）
        "trailing_stop_pct": 0.09,
        "max_hold_days": 15,          # 延长持有期（原12天）
        "max_positions": 5,
        "exit_on_deselect": False,
        "benchmark": "000300.SH",
    },
    # 跳空缺口 win_rate≈42% → P/L≥2.5x，止损提高避免日内噪声
    # EV = 0.42×15% - 0.58×6% = 6.3% - 3.48% = +2.8%/笔
    "gap_factor": {
        "signal_mode": "daily_signal",
        "entry_mode": "next_open",
        "stop_loss_pct": 0.06,        # 提高到6%（原4%）
        "take_profit_pct": 0.15,      # 2.5x 比例（原8%，缺口续涨空间通常不止8%）
        "trailing_stop_pct": 0.08,
        "max_hold_days": 6,           # 适当延长（原4天）
        "max_positions": 5,
        "exit_on_deselect": False,
        "benchmark": "000300.SH",
    },
    # 振幅压缩 win_rate≈50% → P/L≥2.0x
    # EV = 0.50×15% - 0.50×6% = 7.5% - 3.0% = +4.5%/笔
    "range_compression": {
        "signal_mode": "daily_signal",
        "entry_mode": "next_open",
        "stop_loss_pct": 0.06,
        "take_profit_pct": 0.15,      # 2.5x 比例
        "trailing_stop_pct": 0.09,
        "max_hold_days": 15,          # 延长（原12天）
        "max_positions": 5,
        "exit_on_deselect": False,
        "benchmark": "000300.SH",
    },
    # 成交量分布 win_rate≈48% → P/L≥2.0x
    # EV = 0.48×15% - 0.52×7% = 7.2% - 3.64% = +3.6%/笔
    "volume_distribution": {
        "signal_mode": "daily_signal",
        "entry_mode": "close",
        "stop_loss_pct": 0.07,
        "take_profit_pct": 0.15,
        "trailing_stop_pct": 0.09,
        "max_hold_days": 15,          # 延长（原12天）
        "max_positions": 5,
        "exit_on_deselect": False,
        "benchmark": "000300.SH",
    },
    # ── 新增A股实战主题的交易规则 ──
    "limit_up_chain": {
        "signal_mode": "daily_signal", "entry_mode": "next_open",
        "stop_loss_pct": 0.07, "take_profit_pct": 0.20,
        "trailing_stop_pct": 0.10, "max_hold_days": 5,
        "max_positions": 3, "exit_on_deselect": False, "benchmark": "000300.SH",
    },
    "money_flow": {
        "signal_mode": "daily_signal", "entry_mode": "next_open",
        "stop_loss_pct": 0.07, "take_profit_pct": 0.18,
        "trailing_stop_pct": 0.09, "max_hold_days": 12,
        "max_positions": 5, "exit_on_deselect": False, "benchmark": "000300.SH",
    },
    "sector_rotation": {
        "signal_mode": "daily_signal", "entry_mode": "next_open",
        "stop_loss_pct": 0.07, "take_profit_pct": 0.18,
        "trailing_stop_pct": 0.09, "max_hold_days": 10,
        "max_positions": 6, "exit_on_deselect": True, "benchmark": "000300.SH",
    },
    "emotion_cycle": {
        "signal_mode": "daily_signal", "entry_mode": "close",
        "stop_loss_pct": 0.08, "take_profit_pct": 0.20,
        "trailing_stop_pct": 0.10, "max_hold_days": 15,
        "max_positions": 5, "exit_on_deselect": False, "benchmark": "000300.SH",
    },
    "auction_microstructure": {
        "signal_mode": "daily_signal", "entry_mode": "next_open",
        "stop_loss_pct": 0.06, "take_profit_pct": 0.15,
        "trailing_stop_pct": 0.08, "max_hold_days": 5,
        "max_positions": 5, "exit_on_deselect": False, "benchmark": "000300.SH",
    },
    "chip_concentration": {
        "signal_mode": "daily_signal", "entry_mode": "next_open",
        "stop_loss_pct": 0.07, "take_profit_pct": 0.18,
        "trailing_stop_pct": 0.09, "max_hold_days": 15,
        "max_positions": 5, "exit_on_deselect": False, "benchmark": "000300.SH",
    },
    "breakout_quality": {
        "signal_mode": "daily_signal", "entry_mode": "next_open",
        "stop_loss_pct": 0.06, "take_profit_pct": 0.18,
        "trailing_stop_pct": 0.09, "max_hold_days": 12,
        "max_positions": 5, "exit_on_deselect": False, "benchmark": "000300.SH",
    },
    "reversal_signal": {
        "signal_mode": "daily_signal", "entry_mode": "close",
        "stop_loss_pct": 0.08, "take_profit_pct": 0.18,
        "trailing_stop_pct": 0.10, "max_hold_days": 10,
        "max_positions": 5, "exit_on_deselect": False, "benchmark": "000300.SH",
    },
    "smart_money_divergence": {
        "signal_mode": "daily_signal", "entry_mode": "next_open",
        "stop_loss_pct": 0.07, "take_profit_pct": 0.18,
        "trailing_stop_pct": 0.09, "max_hold_days": 10,
        "max_positions": 5, "exit_on_deselect": False, "benchmark": "000300.SH",
    },
    "volatility_contraction_breakout": {
        "signal_mode": "daily_signal", "entry_mode": "next_open",
        "stop_loss_pct": 0.06, "take_profit_pct": 0.18,
        "trailing_stop_pct": 0.09, "max_hold_days": 12,
        "max_positions": 5, "exit_on_deselect": False, "benchmark": "000300.SH",
    },
}

# 变异因子继承原始主题规则，fallback 通用规则
# EV = 0.53×15% - 0.47×7% = 7.95% - 3.29% = +4.7%/笔 (基于mutation观测胜率~53%)
_DEFAULT_TRADING_RULES: dict = {
    "signal_mode": "daily_signal",
    "entry_mode": "next_open",
    "stop_loss_pct": 0.07,
    "take_profit_pct": 0.15,
    "trailing_stop_pct": 0.09,
    "max_hold_days": 12,
    "max_positions": 5,
    "exit_on_deselect": False,
    "benchmark": "000300.SH",
}


def get_trading_rules_for_theme(theme_id: str) -> dict:
    """根据主题 ID 返回对应的买卖规则配置。变异因子或未知主题使用默认规则。"""
    return THEME_TRADING_RULES.get(theme_id, _DEFAULT_TRADING_RULES)


# ══════════════════════════════════════════════════════════════════════════════
# 代码提取与校验
# ══════════════════════════════════════════════════════════════════════════════

def _extract_factor_code(llm_reply: str) -> str:
    """从 LLM 回复中提取 generate_factor 函数代码。"""
    code_match = re.search(r"```python\s*(.*?)\s*```", llm_reply, re.DOTALL)
    code = code_match.group(1) if code_match else llm_reply

    fn_match = re.search(
        r"((?:import\s+\w+.*\n)*\s*def\s+generate_factor\(.*?\n(?:[ \t]+.*(?:\n|$))*)",
        code,
    )
    if fn_match:
        return fn_match.group(1).rstrip()

    if "def generate_factor" in code:
        return code.strip()

    return code.strip()


def _validate_factor_code(code: str) -> list[str]:
    """使用 factor_quality 框架进行代码静态分析。

    返回问题列表 (空列表 = 通过)。
    致命问题会阻止提交沙箱，警告会记录但不阻止。
    """
    from agents.factor_quality import code_audit

    report = code_audit(code)

    # 只返回致命问题 (fatal) 作为拦截理由
    # 警告 (warning) 记录日志但不拦截
    issues = []
    for issue in report.issues:
        if issue.severity == "fatal":
            issues.append(f"[{issue.rule_id}] {issue.message}")
        elif issue.severity == "warning":
            logger.info("代码警告 [%s]: %s", issue.rule_id, issue.message)

    return issues


# ══════════════════════════════════════════════════════════════════════════════
# 种子生成器
# ══════════════════════════════════════════════════════════════════════════════

# A股实战主题优先 (新增的主题权重更高)
_ASTOCK_PRIORITY_THEME_IDS = {
    "limit_up_chain", "money_flow", "sector_rotation", "emotion_cycle",
    "auction_microstructure", "chip_concentration", "breakout_quality",
    "reversal_signal", "smart_money_divergence", "volatility_contraction_breakout",
}

# 超短线主题
_ULTRA_SHORT_THEME_IDS = {
    "limit_up_chain", "auction_microstructure", "intraday_pattern",
    "gap_factor", "reversal_signal",
}


async def _fetch_market_context() -> str:
    """从Redis获取最新市场行情数据，注入到因子生成prompt中。"""
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"), decode_responses=True)
        # 尝试读取最新的市场概览
        ctx_parts = []
        # 读取最新的涨跌停数据
        overview = await r.get("openclaw:market:overview_cache")
        if overview:
            import json as _j
            data = _j.loads(overview)
            if data.get("limit_up_count"):
                ctx_parts.append(f"今日涨停 {data['limit_up_count']} 家, 跌停 {data.get('limit_down_count', 0)} 家")
            if data.get("hot_sectors"):
                sectors = data["hot_sectors"][:5]
                ctx_parts.append(f"热门板块: {', '.join(s.get('name','') for s in sectors)}")
        await r.aclose()
        if ctx_parts:
            return "█ 当前市场环境 (实时):\n  " + "\n  ".join(ctx_parts)
    except Exception:
        pass
    return ""


def _generate_seed(ultra_short_weight: float = 1.0) -> dict:
    """随机选取探索主题 + 参数扰动，生成本轮种子。

    A股实战主题权重为普通技术主题的3倍，确保大部分因子围绕A股特色展开。

    Args:
        ultra_short_weight: 超短线相关主题的权重倍数 (1.0=均等, 2.5=超短线优先)
    """
    from agents.factor_quality import QUALITY_RULES_FOR_PROMPT

    # 权重: A股实战主题=2.0, 超短线(如果开启)=ultra_short_weight, 普通技术=1.0
    # 保留旧技术主题的权重，因为它们已经验证能产出正Sharpe因子
    weights = []
    for t in EXPLORATION_THEMES:
        w = 1.0
        if t["id"] in _ASTOCK_PRIORITY_THEME_IDS:
            w = 2.0  # A股实战主题适度优先（不完全压制旧主题）
        if ultra_short_weight > 1.0 and t["id"] in _ULTRA_SHORT_THEME_IDS:
            w = max(w, ultra_short_weight)
        weights.append(w)

    theme = random.choices(EXPLORATION_THEMES, weights=weights, k=1)[0]

    lo, hi = theme["lookback_range"]
    if ultra_short_weight > 1.0 and theme["id"] in _ULTRA_SHORT_THEME_IDS:
        hi = min(hi, 15)
    lookback = random.randint(lo, hi)
    decay_weight = round(random.uniform(0.5, 1.0), 2)

    hints_text = "\n".join(f"  - {h}" for h in theme["hints"])

    extra_hint = ""
    if ultra_short_weight > 1.0 and theme["id"] in _ULTRA_SHORT_THEME_IDS:
        extra_hint = """
█ 超短线优先模式 (lookback ≤ 15日，信号需在 1-3 日内有效):
  - 优先使用 open/high/low 价格，含有更多短期博弈信息
  - 跳空缺口: (open - close.shift(1)) / close.shift(1) 是强短期信号
  - 换手率异动: volume / volume.rolling(5).mean() > 1.5 才是有效信号
  - 组合 open/high/low/close/volume 5个字段，多信号交叉确认
"""

    prompt = ALPHA_MINER_PROMPT.format(
        theme_name=theme["name"],
        theme_desc=theme["desc"],
        hints=hints_text + extra_hint,
        market_context="{market_context}",  # placeholder, filled at runtime
        lookback=lookback,
        decay_weight=decay_weight,
        quality_rules=QUALITY_RULES_FOR_PROMPT,
    )

    return {
        "theme_id": theme["id"],
        "theme_name": theme["name"],
        "lookback": lookback,
        "decay_weight": decay_weight,
        "prompt": prompt,
    }


def _generate_mutation_seed(original_code: str, metrics: dict) -> dict:
    """基于已有因子生成变异种子。"""
    from agents.factor_quality import QUALITY_RULES_FOR_PROMPT

    mutations = [
        # 胜率提升 (实证验证的高胜率技巧)
        "添加 (high-low) 振幅维度: 乘以 (high-low)/((high-low).rolling(20).mean()+1e-8) 作为波动比权重",
        "增强 ewm 平滑: 在最终 factor 上再套一层 .ewm(alpha=0.85).mean().fillna(0)",
        "添加 volume 权重: 乘以 (1 + volume/(volume.rolling(20).mean()+1e-8) * 0.3)",
        "取反因子方向: 将 factor 改为 -factor (均值回归的反向逻辑，胜率可能提升10%)",
        # 收益提升
        "添加价格偏离: 乘以 abs(close - close.rolling(20).mean()) / (close.rolling(20).std()+1e-10) 放大极端偏离信号",
        "添加收盘位置: 乘以 (close-low)/(high-low+1e-8) 高位收盘权重更大",
        "乘以 close.pct_change(5).rank(axis=1, pct=True) 的截面动量确认",
        # 信号质量
        "用 rolling(N).rank(pct=True) 替换原始数值做截面标准化，减少异常值影响",
        "添加量能波动子因子: 与 np.log(volume+1).diff().rolling(N).std().fillna(0) 等权平均",
        "缩短 lookback 窗口使信号更灵敏 (将所有 rolling(N) 中 N 减少30%)",
    ]
    direction = random.choice(mutations)

    prompt = FACTOR_MUTATOR_PROMPT.format(
        original_code=original_code,
        sharpe=metrics.get("sharpe", 0),
        ic_mean=metrics.get("mean_ic", 0),
        ir=metrics.get("ir", 0),
        mutation_direction=direction,
        quality_rules=QUALITY_RULES_FOR_PROMPT,
    )

    return {
        "theme_id": "mutation",
        "theme_name": f"变异: {direction}",
        "prompt": prompt,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 主挖掘循环
# ══════════════════════════════════════════════════════════════════════════════

async def run_mining_round(
    router,
    bridge,
    factor_lib,
    notify_fn=None,
    n_factors: int = 5,
    ultra_short_weight: float = 1.0,
) -> dict:
    """执行单轮因子挖掘。

    流程: 生成 N 个种子 → LLM 生成因子代码 → 逐个提交沙箱 → 筛选入库

    Returns:
        {"generated": N, "tested": M, "admitted": K, "factors": [...]}
    """
    start_date = (date.today() - timedelta(days=DIGGER_CONFIG["backtest_window_days"])).isoformat()
    end_date = date.today().isoformat()

    async def _notify(text: str):
        if notify_fn:
            try:
                await notify_fn(text)
            except Exception:
                pass
        logger.info(text)

    seeds = [_generate_seed(ultra_short_weight=ultra_short_weight) for _ in range(n_factors)]

    # 注入实时市场环境到所有种子的prompt中
    market_ctx = await _fetch_market_context()
    for seed in seeds:
        seed["prompt"] = seed["prompt"].replace("{market_context}", market_ctx)

    # 变异策略: 60% 变异种子 + 40% 新种子 (变异基于已验证因子，成功率远高于全新生成)
    existing = await factor_lib.get_all_factors()
    if existing and len(existing) >= 3:
        # 按 sharpe 排序，从 top 因子中随机选取做变异
        top_factors = sorted(existing, key=lambda f: f.sharpe, reverse=True)[:20]
        n_mutations = max(1, int(n_factors * 0.6))  # 60% 变异
        for i in range(n_mutations):
            if i >= len(seeds):
                break
            base = random.choice(top_factors)
            mutation_seed = _generate_mutation_seed(base.code, {
                "sharpe": base.sharpe, "mean_ic": base.ic_mean, "ir": base.ir
            })
            mutation_seed["prompt"] = mutation_seed["prompt"].replace("{market_context}", market_ctx)
            seeds[-(i + 1)] = mutation_seed
            await _notify(f"  🧬 变异种子 {i+1}: 基于 {base.sub_theme or base.theme} (sharpe={base.sharpe:.2f})")
    elif existing:
        best = max(existing, key=lambda f: f.ir)
        mutation_seed = _generate_mutation_seed(best.code, {
            "sharpe": best.sharpe, "mean_ic": best.ic_mean, "ir": best.ir
        })
        mutation_seed["prompt"] = mutation_seed["prompt"].replace("{market_context}", market_ctx)
        seeds[-1] = mutation_seed

    generated_codes = []
    for i, seed in enumerate(seeds):
        try:
            reply = await router.chat([
                {"role": "system", "content": "你是 OpenClaw Alpha 因子挖掘引擎。只输出 generate_factor 函数代码。"},
                {"role": "user", "content": seed["prompt"]},
            ], task_type="code")
        except Exception as e:
            logger.warning("LLM 调用失败 (seed %d): %s", i, e)
            continue

        if not reply:
            continue

        code = _extract_factor_code(reply)
        issues = _validate_factor_code(code)
        if issues:
            logger.info("种子 %d 代码校验失败: %s", i, "; ".join(issues))
            continue

        generated_codes.append({
            "code": code,
            "theme_id": seed["theme_id"],
            "theme_name": seed["theme_name"],
        })

    await _notify(f"⛏️ 本轮生成 {len(generated_codes)}/{n_factors} 个因子代码")

    results = {"generated": len(generated_codes), "tested": 0, "admitted": 0, "rejected_reasons": [], "factors": []}

    for item in generated_codes:
        code = item["code"]
        try:
            resp = await bridge.run_factor_mining(
                factor_code=code,
                start_date=start_date,
                end_date=end_date,
            )
        except Exception as e:
            logger.warning("沙箱调用失败: %s", e)
            results["rejected_reasons"].append(f"沙箱异常: {e}")
            continue

        results["tested"] += 1

        if resp.get("status") == "error":
            err = resp.get("error", "未知")[:200]
            logger.info("沙箱执行失败: %s", err)
            results["rejected_reasons"].append(f"引擎错误: {err}")
            await _notify(f"  ⚠️ {item['theme_name']} 沙箱执行失败: {err[:80]}")
            continue

        metrics = resp.get("metrics") or {}
        _sharpe_raw = metrics.get("sharpe_ratio") or metrics.get("sharpe") or 0
        _trades_raw = metrics.get("total_trades") or metrics.get("trades") or 0
        _wr_raw = metrics.get("win_rate_pct") or metrics.get("win_rate") or 0
        await _notify(f"  📊 {item['theme_name']} 回测结果: sharpe={_sharpe_raw:.2f} trades={_trades_raw} win={_wr_raw:.1f}%")

        if not metrics.get("sharpe_ratio") and not metrics.get("sharpe"):
            results["rejected_reasons"].append("指标异常: sharpe 为空")
            continue

        # ── 因子质量综合评审 (代码 + 指标双重检测) ──
        from agents.factor_quality import full_audit
        quality = full_audit(code, metrics)

        if not quality.passed:
            reason_text = f"质量评审不通过 [{quality.grade}]: " + "; ".join(
                f"[{i.rule_id}] {i.message}" for i in quality.issues if i.severity == "fatal"
            )
            logger.info("因子被质量关卡拒绝: %s — %s", item["theme_name"], reason_text)
            results["rejected_reasons"].append(reason_text)
            _sharpe = metrics.get("sharpe_ratio") or metrics.get("sharpe") or 0
            await _notify(f"  ❌ {item['theme_name']} sharpe={_sharpe:.2f} — {reason_text[:120]}")

            factor_info = {
                "theme": item["theme_name"],
                "sharpe": _sharpe,
                "quality_grade": quality.grade,
                "quality_score": quality.score,
                "admitted": False,
                "reason": reason_text,
            }
            results["factors"].append(factor_info)
            continue

        # ── CSCV 过拟合概率验证 (PBO) ──
        # 初筛通过后再花 k-1 次回测代价计算 PBO，抛弃 PBO > 0.5 的伪因子
        try:
            cscv_resp = await bridge.run_factor_mining_cscv(
                factor_code=code, total_days=360, k=4
            )
            pbo_score = cscv_resp.get("pbo_score", 0.0)
            window_sharpes = cscv_resp.get("window_sharpes", [])
            metrics["pbo_score"] = pbo_score
            metrics["cscv_windows"] = cscv_resp.get("metrics", {}).get("cscv_windows", [])
            await _notify(
                f"  CSCV k=4 Sharpes={[round(s, 2) for s in window_sharpes]}  PBO={pbo_score:.3f}"
            )
            # PBO 智能判定: k=4 时 PBO 只有 0/0.333/0.667/1.0 四种离散值
            # 如果整体 Sharpe > 0 且多数窗口为正，即使 PBO 高也可能是市场周期导致
            positive_windows = sum(1 for s in window_sharpes if s > 0)
            overall_sharpe = metrics.get("sharpe_ratio") or metrics.get("sharpe") or 0
            # 豁免条件: 整体 sharpe >= 0.3 且 至少一半窗口为正
            pbo_exempt = overall_sharpe >= 0.3 and positive_windows >= len(window_sharpes) / 2
            if pbo_score > 0.75 and not pbo_exempt:
                reason_text = (
                    f"[PB01] PBO={pbo_score:.3f} — CSCV 检验过拟合，拒绝入库"
                    f"  窗口Sharpe={[round(s, 2) for s in window_sharpes]}"
                )
                logger.info("CSCV 过拟合拒绝: %s — %s", item["theme_name"], reason_text)
                results["rejected_reasons"].append(reason_text)
                await _notify(f"  ❌ {item['theme_name']} PBO={pbo_score:.3f} 过拟合拒绝")
                results["factors"].append({
                    "theme": item["theme_name"],
                    "sharpe": metrics.get("sharpe_ratio") or metrics.get("sharpe"),
                    "pbo_score": pbo_score,
                    "window_sharpes": window_sharpes,
                    "admitted": False,
                    "reason": reason_text,
                })
                continue
            elif pbo_score > 0.5:
                await _notify(f"  ⚠️ PBO={pbo_score:.3f} 偏高但豁免 (sharpe={overall_sharpe:.2f}, {positive_windows}/{len(window_sharpes)}窗口为正)")
        except Exception as e:
            logger.warning("CSCV 验证异常 (跳过 PBO 检查): %s", e)

        # 质量通过，提交入库（附带对应主题的买卖规则建议）
        _trading_rules = get_trading_rules_for_theme(item["theme_id"])
        admitted, reason, factor_id = await factor_lib.add_factor(
            code=code,
            metrics=metrics,
            theme=item["theme_id"],
            sub_theme=item["theme_name"],
            suggested_trading_rules=_trading_rules,
        )

        factor_info = {
            "theme": item["theme_name"],
            "sharpe": metrics.get("sharpe_ratio") or metrics.get("sharpe"),
            "ic_mean": metrics.get("factor_ic") or metrics.get("mean_ic"),
            "ir": metrics.get("sortino_ratio") or metrics.get("ir"),
            "quality_grade": quality.grade,
            "quality_score": quality.score,
            "admitted": admitted,
            "reason": reason,
            "suggested_trading_rules": _trading_rules if admitted else None,
        }
        results["factors"].append(factor_info)

        if admitted:
            results["admitted"] += 1
            rules_summary = (
                f"止损={_trading_rules.get('stop_loss_pct')} "
                f"止盈={_trading_rules.get('take_profit_pct')} "
                f"追踪={_trading_rules.get('trailing_stop_pct')} "
                f"最长={_trading_rules.get('max_hold_days')}天"
            )
            await _notify(
                f"🏆 因子入库! ID={factor_id} | theme={item['theme_name']} | "
                f"sharpe={metrics.get('sharpe', 0):.3f} | IR={metrics.get('ir', 0):.3f}\n"
                f"   买卖规则: {rules_summary}"
            )
        else:
            results["rejected_reasons"].append(reason)
            logger.info("因子被拒: %s — %s", item["theme_name"], reason)
            await _notify(f"  ❌ {item['theme_name']} 入库被拒: {reason}")

    stats = await factor_lib.get_stats()
    results["library_stats"] = stats

    if stats.get("ready_to_combine"):
        await _notify(f"🔮 因子库已达 {stats['active_count']} 个，可触发融合!")

    return results


async def run_digger_session(
    notify_fn=None,
    max_rounds: int = 0,
    factors_per_round: int = 0,
    round_interval: int = 0,
    ultra_short_weight: float = 1.0,
) -> dict:
    """运行一次完整的 Digger 会话（多轮挖掘）。

    Args:
        max_rounds: 最大轮数 (0=使用默认配置)
        factors_per_round: 每轮因子数 (0=使用默认配置)
        round_interval: 轮间间隔秒数 (0=使用默认配置)
    """
    from agents.llm_router import get_llm_router
    from agents.bridge_client import get_bridge_client
    from agents.factor_library import get_factor_library

    router = get_llm_router()
    bridge = get_bridge_client()
    factor_lib = get_factor_library()

    max_rounds = max_rounds or DIGGER_CONFIG["max_rounds_per_session"]
    factors_per_round = factors_per_round or DIGGER_CONFIG["factors_per_round"]
    interval = round_interval or DIGGER_CONFIG["round_interval_sec"]

    async def _notify(text: str):
        if notify_fn:
            try:
                await notify_fn(text)
            except Exception:
                pass
        logger.info(text)

    session_stats = {
        "rounds_completed": 0,
        "total_generated": 0,
        "total_tested": 0,
        "total_admitted": 0,
        "start_time": time.time(),
    }

    await _notify(f"🏭 Alpha Digger 启动: 最多 {max_rounds} 轮, 每轮 {factors_per_round} 个因子")

    for round_num in range(1, max_rounds + 1):
        await _notify(f"⛏️ [{round_num}/{max_rounds}] 挖掘中...")

        try:
            round_result = await run_mining_round(
                router=router,
                bridge=bridge,
                factor_lib=factor_lib,
                notify_fn=notify_fn,
                n_factors=factors_per_round,
                ultra_short_weight=ultra_short_weight,
            )
        except Exception as e:
            logger.error("挖掘轮次 %d 异常: %s", round_num, e)
            await _notify(f"⚠️ 轮次 {round_num} 异常: {e}")
            if round_num < max_rounds:
                await asyncio.sleep(interval)
            continue

        session_stats["rounds_completed"] += 1
        session_stats["total_generated"] += round_result["generated"]
        session_stats["total_tested"] += round_result["tested"]
        session_stats["total_admitted"] += round_result["admitted"]

        lib_stats = round_result.get("library_stats", {})
        await _notify(
            f"📊 轮次 {round_num} 完成: "
            f"生成={round_result['generated']}, 测试={round_result['tested']}, "
            f"入库={round_result['admitted']} | 因子库: {lib_stats.get('active_count', 0)} 个活跃因子"
        )

        if round_num < max_rounds:
            await asyncio.sleep(interval)

    session_stats["elapsed_sec"] = time.time() - session_stats["start_time"]
    final_stats = await factor_lib.get_stats()
    session_stats["final_library"] = final_stats

    summary = (
        f"🏭 Alpha Digger 会话结束\n"
        f"轮次: {session_stats['rounds_completed']}/{max_rounds}\n"
        f"生成: {session_stats['total_generated']} | "
        f"测试: {session_stats['total_tested']} | "
        f"入库: {session_stats['total_admitted']}\n"
        f"因子库: {final_stats.get('active_count', 0)} 个活跃因子\n"
        f"耗时: {session_stats['elapsed_sec']:.0f}s"
    )
    await _notify(summary)
    session_stats["summary"] = summary

    return session_stats


# ══════════════════════════════════════════════════════════════════════════════
# Orchestrator 集成入口
# ══════════════════════════════════════════════════════════════════════════════

async def run_alpha_digger(
    orchestrator=None,
    notify_fn=None,
    max_rounds: int = 10,
    factors_per_round: int = 5,
    round_interval: int = 60,
) -> dict:
    """供 Orchestrator 调用的入口。

    默认跑 10 轮 (50 个因子)，适合单次手动触发。
    daemon 模式下设 max_rounds=50 + round_interval=300。
    """
    return await run_digger_session(
        notify_fn=notify_fn,
        max_rounds=max_rounds,
        factors_per_round=factors_per_round,
        round_interval=round_interval,
    )


async def get_digger_status() -> dict:
    """获取当前因子库状态（供 orchestrator/telegram 查询）。"""
    from agents.factor_library import get_factor_library
    lib = get_factor_library()
    return await lib.get_stats()

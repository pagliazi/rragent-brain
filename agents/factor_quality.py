"""
Factor Quality Analyzer — 因子质量分析框架

职责:
  1. 代码静态分析: 检测未来函数、数据泄露、不合理逻辑
  2. 回测指标诊断: 过拟合检测、样本内外一致性、异常指标识别
  3. 因子有效性评估: 覆盖度、区分度、时序稳定性

设计原则:
  - 所有规则都有明确的判定依据和阈值说明
  - 返回结构化的质量报告，供 alpha_digger/factor_library 直接使用
  - 作为 rrclaw 挖掘流水线的核心质量关卡

架构位置:
  alpha_digger.py → [LLM生成代码] → factor_quality.code_audit()  → 静态拦截
  core_engine.py  → [沙箱回测结果] → factor_quality.metrics_audit() → 指标诊断
  factor_library.py → [入库决策]   → factor_quality.full_audit()    → 综合判定
"""
from __future__ import annotations

import re
import itertools
import logging
import math
from dataclasses import dataclass, field, asdict
from typing import Optional

logger = logging.getLogger("agent.factor_quality")


# ══════════════════════════════════════════════════════════════════════════════
# PBO / CSCV — 回测过拟合概率 (Bailey, Borwein, López de Prado 2014)
# ══════════════════════════════════════════════════════════════════════════════

def compute_pbo_cscv(window_sharpes: list) -> float:
    """计算回测过拟合概率 (PBO) — 走前向 IS/OOS logit 检验.

    方法 (Bailey, Borwein, López de Prado 2014 精神，走前向适配):
      对 K 个时序窗口 Sharpe [S0..SK-1], 构造 K-1 个走前向步骤:
        步骤 i (i=1..K-1): IS = mean(S0..Si-1),  OOS = Si
        ω_i = OOS_i / (IS_mean_i + OOS_i)  ∈ (0,1)
          - ω < 0.5 → IS > OOS → 样本内过拟合信号
          - ω > 0.5 → OOS > IS → 样本外改善
        λ_i = logit(ω_i) = log(ω_i / (1-ω_i))
      用 t 分布估计 P(λ < 0) = PBO:
        - PBO > 0.5 → 多数步骤显示 IS 优于 OOS → 拒绝
        - PBO ≤ 0.5 → 时序一致或改善 → 允许入库

    注: 标准 CSCV 的 C(K,K/2) 对称分割在单因子时序评估中因镜像分割相互
    抵消导致 mu=0 退化；走前向打破对称性，保留时序因果方向。

    Args:
        window_sharpes: K 个时间窗口的 Sharpe 比率 (length >= 3 有效)

    Returns:
        PBO ∈ [0, 1]. PBO > 0.5 表示过拟合概率超过 50%.
    """
    K = len(window_sharpes)
    if K < 3:
        # 仅 2 个窗口不足以拟合 t 分布，回落到简单比较
        if K == 2:
            return 0.8 if window_sharpes[0] > 0 and window_sharpes[1] <= 0 else 0.3
        return 0.0

    lambdas: list = []
    for i in range(1, K):
        is_mean = sum(window_sharpes[:i]) / i
        oos = window_sharpes[i]

        # ω: OOS 占 (IS + OOS) 的比例；处理负值边界
        if is_mean <= 0 and oos <= 0:
            omega = 0.6 if oos >= is_mean else 0.4   # 两段均负: 较不负=较好
        elif is_mean <= 0:
            omega = 0.85  # IS 负但 OOS 正 → 时序改善
        elif oos <= 0:
            omega = 0.05  # IS 正但 OOS 负 → 明显过拟合
        else:
            omega = oos / (is_mean + oos)

        omega = max(0.001, min(0.999, omega))
        lam = math.log(omega / (1.0 - omega))
        lambdas.append(lam)

    n = len(lambdas)
    mu = sum(lambdas) / n

    try:
        from scipy import stats as scipy_stats  # type: ignore
        if n >= 3:
            var = sum((l - mu) ** 2 for l in lambdas) / (n - 1)
            std = var ** 0.5
            if std < 1e-10:
                return 1.0 if mu < 0 else 0.0
            pbo = float(scipy_stats.t.cdf(0.0, df=n - 1, loc=mu, scale=std / n ** 0.5))
        else:
            pbo = sum(1 for l in lambdas if l < 0) / n
    except ImportError:
        pbo = sum(1 for l in lambdas if l < 0) / n

    return round(float(pbo), 4)


# ══════════════════════════════════════════════════════════════════════════════
# 质量报告数据结构
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class QualityIssue:
    """单条质量问题"""
    severity: str       # "fatal" | "warning" | "info"
    category: str       # "future_func" | "overfit" | "coverage" | "logic" | "stability"
    rule_id: str        # 规则编号，方便追踪
    message: str        # 人可读的描述
    detail: str = ""    # 补充说明


@dataclass
class QualityReport:
    """因子质量分析完整报告"""
    passed: bool = True
    grade: str = "A"                          # A/B/C/D/F
    score: float = 100.0                      # 0-100 综合评分
    issues: list[QualityIssue] = field(default_factory=list)
    code_audit: dict = field(default_factory=dict)
    metrics_audit: dict = field(default_factory=dict)
    summary: str = ""

    def add_issue(self, severity: str, category: str, rule_id: str, message: str, detail: str = ""):
        self.issues.append(QualityIssue(severity, category, rule_id, message, detail))
        if severity == "fatal":
            self.passed = False

    @property
    def fatal_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "fatal")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "warning")

    def compute_grade(self):
        """根据问题数量和严重性计算综合评分"""
        if self.fatal_count > 0:
            self.grade = "F"
            self.score = 0
            self.passed = False
            return

        penalty = self.warning_count * 15
        self.score = max(0, 100 - penalty)

        if self.score >= 85:
            self.grade = "A"
        elif self.score >= 70:
            self.grade = "B"
        elif self.score >= 50:
            self.grade = "C"
        else:
            self.grade = "D"
            self.passed = False

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "grade": self.grade,
            "score": self.score,
            "fatal_count": self.fatal_count,
            "warning_count": self.warning_count,
            "issues": [asdict(i) for i in self.issues],
            "code_audit": self.code_audit,
            "metrics_audit": self.metrics_audit,
            "summary": self.summary,
        }

    def format_summary(self) -> str:
        lines = [f"质量评级: {self.grade} ({self.score:.0f}分)"]
        if self.fatal_count:
            lines.append(f"致命问题: {self.fatal_count} 个")
        if self.warning_count:
            lines.append(f"警告: {self.warning_count} 个")
        for issue in self.issues:
            tag = {"fatal": "FATAL", "warning": "WARN", "info": "INFO"}[issue.severity]
            lines.append(f"  [{tag}] {issue.rule_id}: {issue.message}")
        self.summary = "\n".join(lines)
        return self.summary


# ══════════════════════════════════════════════════════════════════════════════
# 第一层: 代码静态分析 (Code Audit)
# ══════════════════════════════════════════════════════════════════════════════
# 在 LLM 生成代码后、提交沙箱回测前执行
# 能拦截的问题就不浪费沙箱算力

# 未来函数模式清单 — 不仅是 shift(-N)，还有各种变体
FUTURE_FUNCTION_PATTERNS = [
    # 直接未来偏移
    (r'\.shift\s*\(\s*-\s*\d+', "F01", "shift(-N): 直接使用未来数据"),
    # 用未来收益作为过滤条件
    (r'pct_change\s*\(\s*-\s*\d+', "F02", "pct_change(-N): 计算未来收益率"),
    # iloc 负索引在循环中可能导致未来窥探
    (r'\.iloc\s*\[\s*[^,\]]*\+\s*1', "F03", "iloc[i+1]: 循环中访问未来行"),
    # diff(-N)
    (r'\.diff\s*\(\s*-\s*\d+', "F04", "diff(-N): 差分使用未来数据"),
    # rolling 后接 shift 负数
    (r'rolling\([^)]+\)\..*\.shift\s*\(\s*-', "F05", "rolling后shift(-N): 窗口统计使用未来数据"),
]

# 涨停/事后标注模式 — 这类因子本质是已知结果反推，无预测能力
LABEL_LEAKAGE_PATTERNS = [
    # 涨停检测: pct_change > 0.08~0.10
    (r'pct_change\s*\([^)]*\)\s*>\s*0\.0[789]', "L01",
     "涨停检测: 用涨跌幅阈值识别涨停股，属于事后标注"),
    # 涨停变量名
    (r'\bis_limit\b|limit_up|is_zt|is_dt', "L02",
     "涨停变量: 因子逻辑包含涨停/跌停标识"),
    # 中文涨停引用
    (r'涨停|跌停', "L03",
     "涨停/跌停: 因子逻辑引用涨跌停概念"),
    # 用 .where 在涨停条件下过滤
    (r'\.where\s*\(.*(?:limit|zt)', "L04",
     "涨停过滤: 用涨停条件做 where 过滤，等于事后选股"),
]

# 可疑逻辑模式 — 不一定是错的，但需要警惕
SUSPICIOUS_PATTERNS = [
    # 嵌套 for 循环 — 矩阵运算中极慢且容易藏 bug
    (r'for\s+\w+\s+in\s+range.*\n.*for\s+\w+\s+in\s+range', "S01",
     "嵌套for循环: 在矩阵运算中效率极低，且容易引入下标错误"),
    # 硬编码股票代码
    (r'\d{6}\.(SH|SZ|sh|sz)', "S02",
     "硬编码股票代码: 因子不应针对特定股票"),
    # 硬编码日期
    (r'20[12]\d-[01]\d-[0-3]\d|20[12]\d[01]\d[0-3]\d', "S03",
     "硬编码日期: 因子不应包含特定日期"),
    # 极端 magic number
    (r'(?<!\d\.)\b(0\.0{4,}|9{4,})\b', "S04",
     "极端常数: 过小或过大的硬编码常数可能是过拟合的参数"),
]

# 基本合规检查
COMPLIANCE_PATTERNS = [
    (r'\b(clickhouse_connect|dolphindb|vectorbt|open\s*\(|exec\s*\(|eval\s*\(|__import__)\b',
     "C01", "禁止的操作: 外部访问/动态执行"),
    (r'\.(stack|unstack|melt|pivot|pivot_table)\s*\(',
     "C02", "禁止的数据变形操作"),
    (r'\bimport\s+(os|sys|subprocess|socket|shutil|pathlib|vectorbt)\b',
     "C03", "禁止的模块导入"),
]


def code_audit(code: str, allow_limit_up: bool = False) -> QualityReport:
    """对因子代码进行静态质量分析。

    在提交沙箱回测前执行，能拦截的问题不浪费算力。

    Args:
        allow_limit_up: 为 True 时跳过涨停相关规则(L01-L04)，用于妖股因子挖掘。
    """
    report = QualityReport()
    report.code_audit = {"checks_run": 0, "code_length": len(code)}

    # 基础结构检查
    if "def generate_factor" not in code:
        report.add_issue("fatal", "logic", "B01", "缺少 def generate_factor 函数定义")
    if "return" not in code:
        report.add_issue("fatal", "logic", "B02", "函数缺少 return 语句")

    # 未来函数检测 — 致命
    for pattern, rule_id, msg in FUTURE_FUNCTION_PATTERNS:
        report.code_audit["checks_run"] += 1
        matches = re.findall(pattern, code)
        if matches:
            report.add_issue("fatal", "future_func", rule_id, msg,
                             detail=f"匹配到 {len(matches)} 处")

    # 涨停/事后标注检测 — 致命 (妖股因子豁免)
    if not allow_limit_up:
        for pattern, rule_id, msg in LABEL_LEAKAGE_PATTERNS:
            report.code_audit["checks_run"] += 1
            if re.search(pattern, code, re.IGNORECASE):
                report.add_issue("fatal", "future_func", rule_id, msg)

    # 可疑逻辑检测 — 警告
    for pattern, rule_id, msg in SUSPICIOUS_PATTERNS:
        report.code_audit["checks_run"] += 1
        if re.search(pattern, code, re.DOTALL):
            report.add_issue("warning", "logic", rule_id, msg)

    # 合规检查 — 致命
    for pattern, rule_id, msg in COMPLIANCE_PATTERNS:
        report.code_audit["checks_run"] += 1
        matches = re.findall(pattern, code, re.IGNORECASE)
        if matches:
            report.add_issue("fatal", "logic", rule_id, msg, detail=str(matches))

    report.compute_grade()
    report.format_summary()
    return report


# ══════════════════════════════════════════════════════════════════════════════
# 第二层: 回测指标诊断 (Metrics Audit)
# ══════════════════════════════════════════════════════════════════════════════
# 在沙箱返回 metrics 后、入库前执行
# 基于统计规律判断因子是否过拟合

# 过拟合信号阈值 — 基于量化实战经验
OVERFIT_THRESHOLDS = {
    # Sharpe > 5 在 A 股半年回测窗口内几乎不可能是真实信号
    # 学术文献中 Sharpe > 3 就需要额外验证
    "sharpe_max": 5.0,

    # 胜率 > 75% 在日频选股策略中极其罕见
    # 考虑到 A 股 T+1 和涨跌停限制，正常胜率 40-65%
    "win_rate_max": 0.75,

    # 胜率 < 45% 说明因子方向可能搞反了或信号质量差
    "win_rate_min": 0.45,

    # Sortino > Sharpe * 3 说明收益集中在少数极端日，不稳定
    "sortino_sharpe_ratio_max": 4.0,

    # 因子覆盖度 < 5% 说明因子只对极少数股票有值
    # 这种因子通常是过拟合在某个特定模式上
    "coverage_min": 0.05,

    # 总交易次数 < 100 在半年全市场回测中说明信号太稀疏
    # 统计意义不足，容易被随机噪声主导
    "trades_min": 100,

    # 交易覆盖的股票数占比 < 2% 说明信号集中在几只股上
    "stock_coverage_min": 0.02,

    # 最大回撤 > 30% 说明策略风险不可控
    "max_drawdown_max": 0.30,

    # 年化收益 > 500% 在 A 股日频几乎不可能
    "ann_return_max": 500.0,
}

# 样本内外一致性检测 (需要沙箱返回分段指标)
# 如果前半段 Sharpe >> 后半段 Sharpe，说明前半段过拟合
SPLIT_TEST_THRESHOLDS = {
    # 前后半段 Sharpe 差异 > 2 倍，说明不稳定
    "sharpe_ratio_max": 2.5,
    # 前后半段胜率差异 > 15%
    "win_rate_diff_max": 0.15,
}


def metrics_audit(metrics: dict, code: str = "") -> QualityReport:
    """对沙箱回测返回的指标进行过拟合诊断。

    Args:
        metrics: core_engine 返回的 metrics dict
        code: 可选，因子源码（用于交叉验证）

    Returns:
        QualityReport: 含过拟合诊断结果
    """
    report = QualityReport()
    diag = {}

    sharpe = metrics.get("sharpe_ratio") or metrics.get("sharpe") or 0
    sortino = metrics.get("sortino_ratio") or metrics.get("sortino") or 0
    win_rate_raw = metrics.get("win_rate_pct") or metrics.get("win_rate") or 0
    # 统一为小数 (0-1)
    win_rate = win_rate_raw / 100.0 if win_rate_raw > 1 else win_rate_raw
    trades = metrics.get("total_trades") or metrics.get("trades") or 0
    max_dd_raw = metrics.get("max_drawdown_pct") or metrics.get("max_drawdown") or 0
    max_dd = abs(max_dd_raw) / 100.0 if abs(max_dd_raw) > 1 else abs(max_dd_raw)
    ann_return = abs(metrics.get("annualized_return_pct") or 0)
    universe_size = metrics.get("universe_size") or 1
    stocks_traded = metrics.get("stocks_traded") or 0
    num_days = metrics.get("num_days") or 1
    factor_coverage = metrics.get("factor_coverage") or 0
    factor_ic = metrics.get("factor_ic") or 0

    stock_coverage = stocks_traded / universe_size if universe_size > 0 else 0

    diag["sharpe"] = sharpe
    diag["sortino"] = sortino
    diag["win_rate"] = win_rate
    diag["trades"] = trades
    diag["max_drawdown"] = max_dd
    diag["ann_return"] = ann_return
    diag["stock_coverage"] = stock_coverage
    diag["factor_coverage"] = factor_coverage
    diag["factor_ic"] = factor_ic
    diag["universe_size"] = universe_size
    diag["num_days"] = num_days

    # ── 过拟合检测 ──

    # OV01: Sharpe 过高
    if sharpe > OVERFIT_THRESHOLDS["sharpe_max"]:
        report.add_issue("fatal", "overfit", "OV01",
                         f"Sharpe={sharpe:.2f} > {OVERFIT_THRESHOLDS['sharpe_max']} "
                         f"(A股半年日频策略 Sharpe > 5 几乎一定是过拟合或数据泄露)",
                         detail="真实有效因子 Sharpe 通常在 0.5-3.0 之间")

    # OV02: 胜率过高
    if win_rate > OVERFIT_THRESHOLDS["win_rate_max"]:
        report.add_issue("fatal", "overfit", "OV02",
                         f"胜率={win_rate:.1%} > {OVERFIT_THRESHOLDS['win_rate_max']:.0%} "
                         f"(A股 T+1 日频策略胜率 > 75% 极其可疑)",
                         detail="正常选股策略胜率通常在 40%-65%")

    # OV03: 年化收益过高
    if ann_return > OVERFIT_THRESHOLDS["ann_return_max"]:
        report.add_issue("fatal", "overfit", "OV03",
                         f"年化收益={ann_return:.0f}% > {OVERFIT_THRESHOLDS['ann_return_max']}% "
                         f"(日频策略年化超过 500% 不现实)")

    # OV04: Sortino/Sharpe 比值异常 (收益集中在少数极端日)
    if sharpe > 0 and sortino / sharpe > OVERFIT_THRESHOLDS["sortino_sharpe_ratio_max"]:
        ratio = sortino / sharpe
        report.add_issue("warning", "overfit", "OV04",
                         f"Sortino/Sharpe={ratio:.1f} > {OVERFIT_THRESHOLDS['sortino_sharpe_ratio_max']} "
                         f"(收益集中在少数极端交易日，稳定性存疑)")

    # ── 信号质量检测 ──

    # SQ01: 交易次数过少 (统计意义不足)
    if trades < OVERFIT_THRESHOLDS["trades_min"]:
        report.add_issue("warning", "coverage", "SQ01",
                         f"交易次数={trades} < {OVERFIT_THRESHOLDS['trades_min']} "
                         f"(样本量不足，Sharpe 等指标的统计置信度低)",
                         detail=f"半年 {num_days} 个交易日，{universe_size} 只股票")

    # SQ02: 交易覆盖股票数过少
    if stock_coverage < OVERFIT_THRESHOLDS["stock_coverage_min"]:
        report.add_issue("warning", "coverage", "SQ02",
                         f"交易覆盖股票={stocks_traded}/{universe_size} ({stock_coverage:.1%}) "
                         f"(信号集中在极少数个股，可能过拟合于特定标的)")

    # SQ03: 因子覆盖度过低 (大部分股票因子值为 NaN/0)
    if factor_coverage and factor_coverage < OVERFIT_THRESHOLDS["coverage_min"]:
        report.add_issue("warning", "coverage", "SQ03",
                         f"因子覆盖度={factor_coverage:.1%} < {OVERFIT_THRESHOLDS['coverage_min']:.0%} "
                         f"(因子值仅覆盖少数股票，区分度不足)")

    # SQ04: 胜率过低
    if win_rate < OVERFIT_THRESHOLDS["win_rate_min"]:
        report.add_issue("warning", "coverage", "SQ04",
                         f"胜率={win_rate:.1%} < {OVERFIT_THRESHOLDS['win_rate_min']:.0%} "
                         f"(因子质量不足，胜率低于45%目标)")

    # SQ05: 回撤过大
    if max_dd > OVERFIT_THRESHOLDS["max_drawdown_max"]:
        report.add_issue("warning", "stability", "SQ05",
                         f"最大回撤={max_dd:.1%} > {OVERFIT_THRESHOLDS['max_drawdown_max']:.0%} "
                         f"(策略风险不可控)")

    # ── 样本内外分割检测 ──
    # 如果沙箱返回了分段指标 (first_half / second_half)
    fh = metrics.get("first_half") or {}
    sh = metrics.get("second_half") or {}
    if fh and sh:
        fh_sharpe = fh.get("sharpe_ratio") or fh.get("sharpe") or 0
        sh_sharpe = sh.get("sharpe_ratio") or sh.get("sharpe") or 0
        diag["first_half_sharpe"] = fh_sharpe
        diag["second_half_sharpe"] = sh_sharpe

        if sh_sharpe > 0 and fh_sharpe / sh_sharpe > SPLIT_TEST_THRESHOLDS["sharpe_ratio_max"]:
            report.add_issue("warning", "overfit", "OV05",
                             f"前半段 Sharpe={fh_sharpe:.2f} / 后半段 Sharpe={sh_sharpe:.2f} "
                             f"= {fh_sharpe/sh_sharpe:.1f}x (样本内外表现差异大，过拟合风险高)")
        elif fh_sharpe > 0 and sh_sharpe <= 0:
            # 降级为 warning: CSCV PBO 已作为过拟合硬门槛，这里不再重复 fatal 拦截
            report.add_issue("warning", "overfit", "OV06",
                             f"前半段 Sharpe={fh_sharpe:.2f} 但后半段 Sharpe={sh_sharpe:.2f} "
                             f"(样本外表现差，过拟合风险高，由 CSCV PBO 做最终裁决)")

        fh_wr = fh.get("win_rate_pct", 0)
        sh_wr = sh.get("win_rate_pct", 0)
        fh_wr = fh_wr / 100 if fh_wr > 1 else fh_wr
        sh_wr = sh_wr / 100 if sh_wr > 1 else sh_wr
        wr_diff = abs(fh_wr - sh_wr)
        if wr_diff > SPLIT_TEST_THRESHOLDS["win_rate_diff_max"]:
            report.add_issue("warning", "overfit", "OV07",
                             f"前后半段胜率差={wr_diff:.1%} > {SPLIT_TEST_THRESHOLDS['win_rate_diff_max']:.0%} "
                             f"(因子稳定性不足)")

    # ── Sharpe 统计置信度 ──
    # Bailey & Lopez de Prado (2014): 考虑多次试验后的 Sharpe deflation
    if sharpe > 0 and trades > 0 and num_days > 30:
        # 简化估算: Sharpe 的标准误 ≈ 1/sqrt(T) (T 为年数)
        years = num_days / 252
        sharpe_se = 1.0 / math.sqrt(years) if years > 0 else float('inf')
        # 如果 Sharpe < 2 * SE，则不显著
        if sharpe < 2 * sharpe_se:
            report.add_issue("warning", "overfit", "OV08",
                             f"Sharpe={sharpe:.2f} 在 {num_days} 天回测中不显著 "
                             f"(SE≈{sharpe_se:.2f}, 需 Sharpe > {2*sharpe_se:.2f} 才有统计意义)")

    # ── PBO: 回测过拟合概率 (Bailey, Borwein, López de Prado 2014) ──
    # 由 bridge_client.run_factor_mining_cscv() 预先计算并注入 metrics["pbo_score"]
    pbo_score = metrics.get("pbo_score")
    if pbo_score is not None:
        diag["pbo_score"] = pbo_score
        # k=4 时 PBO 只有 0/0.333/0.667/1.0 四种离散值
        # 0.667 在 A 股短期回测中极常见（某个季度行情差），不应作为 fatal
        if pbo_score > 0.75:
            report.add_issue("fatal", "overfit", "PB01",
                             f"PBO={pbo_score:.3f} > 0.75 — 所有 CSCV 窗口都退化，确定过拟合",
                             detail="Bailey & López de Prado (2014) CSCV 检验")
        elif pbo_score > 0.5:
            report.add_issue("warning", "overfit", "PB02",
                             f"PBO={pbo_score:.3f} — 部分窗口退化，过拟合风险中等")
        elif pbo_score > 0.4:
            report.add_issue("warning", "overfit", "PB03",
                             f"PBO={pbo_score:.3f} — 轻微过拟合风险")
        else:
            diag["pbo_note"] = f"PBO={pbo_score:.3f} 通过 (<0.4)，时序一致性良好"

    report.metrics_audit = diag
    report.compute_grade()
    report.format_summary()
    return report


# ══════════════════════════════════════════════════════════════════════════════
# 综合评审 (Full Audit)
# ══════════════════════════════════════════════════════════════════════════════

def full_audit(code: str, metrics: dict) -> QualityReport:
    """综合因子质量评审: 代码静态分析 + 回测指标诊断。

    入库前的最终关卡。两层检测的问题汇总到一份报告中。

    Args:
        code: 因子源码
        metrics: 沙箱回测返回的 metrics

    Returns:
        QualityReport: 最终质量报告
    """
    code_report = code_audit(code)
    metrics_report = metrics_audit(metrics, code=code)

    # 合并两层报告
    final = QualityReport()
    final.issues = code_report.issues + metrics_report.issues
    final.code_audit = code_report.code_audit
    final.metrics_audit = metrics_report.metrics_audit
    final.compute_grade()
    final.format_summary()

    return final


# ══════════════════════════════════════════════════════════════════════════════
# LLM Prompt 质量规范 — 供 alpha_digger 注入到挖掘 prompt 中
# ══════════════════════════════════════════════════════════════════════════════

QUALITY_RULES_FOR_PROMPT = """
█ 因子质量规范 (rrclaw 因子准入标准，违反任何致命规则 = 因子直接废弃):

[致命] 未来函数检测:
  F01. 禁止 shift(-N) — 向未来偏移 = 偷看明天的数据
  F02. 禁止 pct_change(-N) — 计算未来收益率
  F03. 禁止循环中 iloc[i+1] — 访问未来行
  F04. 禁止 diff(-N) — 差分使用未来数据
  F05. 禁止 rolling 后接 shift(-N) — 窗口统计使用未来数据
  ➜ 只能用正数偏移: shift(1)=昨天, shift(5)=5天前

[致命] 事后标注检测:
  L01. 禁止用 pct_change > 0.08/0.09 检测涨停 — 这是已知结果，不是预测
  L02. 禁止 is_limit / limit_up 等涨停变量
  L03. 因子值不能依赖于"当天是否涨停"的判断
  L04. 不能用 .where(涨停条件) 过滤 — 等于事后选股
  ➜ 因子应基于价量统计规律，不是标注已知事件

[致命] 过拟合特征:
  OV01. Sharpe > 5 → 几乎一定是数据泄露或过拟合 (真实因子 0.8-3.0)
  OV02. 胜率 > 75% → A股 T+1 日频策略不可能这么高 (正常 45-65%)
  OV03. 年化 > 500% → 不现实

[警告] 质量标准 (准入门槛已提升):
  SQ01. 交易次数 > 100 (半年全市场回测)
  SQ02. 覆盖股票数 > 总股数 2%
  SQ03. 因子非零/非NaN覆盖度 > 5%
  SQ04. 胜率 > 45% (门槛提升，低质因子直接淘汰)
  SQ05. 最大回撤 < 30%

█ 什么是好因子 (rrclaw 高质量标准):
  - 优先: 量价背离+情绪共振类因子 (多信号交叉确认，比单一技术指标更鲁棒)
  - 避免: 纯技术指标单因子 (如单独的 RSI/MACD 变体，IC 衰减快)
  - IC 衰减: 好因子在 5-20 日内仍保持 IC > 0，快速衰减 (2-3日清零) 的因子不稳定
  - 超短线要求: 换手率信号强度 > 0.3 (量价比大于均值30%以上)，盈亏比 > 1.5
  - 对大部分股票都有非零的因子值 (高覆盖度)
  - 因子值越高的股票组合，收益稳定高于因子值低的组合 (单调性)
  - 在不同时间段表现一致 (时序稳定性)
  - 目标: Sharpe ≥ 0.8, 胜率 ≥ 45%, IC_mean ≥ 0.005, IR ≥ 0.5, 半年交易 500+ 次
"""

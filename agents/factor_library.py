"""
Factor Library — 因子库管理模块

职责:
  1. 存储通过筛选的挖掘因子 (Redis 持久化)
  2. 入库前相关性去重 (与现有因子的 metrics 相似度)
  3. 定期衰减验证 (样本外 IC 追踪)
  4. 因子融合 (当库内因子数达阈值时触发组合策略生成)

存储: Redis Hash + List (rragent:factor_library:*)
      后续可扩展到 ClickHouse 持久化
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Optional

logger = logging.getLogger("agent.factor_library")

REDIS_KEY_FACTORS = "openclaw:factor_library:factors"
REDIS_KEY_INDEX = "openclaw:factor_library:index"
REDIS_KEY_STATS = "openclaw:factor_library:stats"
MAX_LIBRARY_SIZE = 500
# 进化失败次数达到此阈值后因子进入 tier_retired / low_pool
FAILURE_THRESHOLD_EVOLUTION = 5

ADMISSION_THRESHOLDS = {
    # A股短线因子门槛 — 宽进严出，让更多因子入库再通过融合和实盘验证筛选
    "sharpe_min": 0.3,          # 从0.8降至0.3: 正sharpe即有初步alpha，后续通过融合提升
    "ic_mean_min": 0.002,       # 从0.005降至0.002: 短线因子IC天然偏低
    "ir_min": 0.2,              # 从0.5降至0.2: IR与sharpe高度相关，不必双重卡
    "trades_min": 30,           # 从50降至30: 某些精选因子交易次数本来就少
    "win_rate_min": 0.35,       # 从0.45降至0.35: 低胜率+高盈亏比策略也有效
    "max_corr_with_existing": 0.7,
    # PBO: k=4 时只有 0/0.333/0.667/1.0 四个离散值
    # 0.667 在A股短期回测中极常见，只拒绝 PBO=1.0 (全窗口退化)
    "pbo_max": 0.75,
}

COMBINE_THRESHOLD = 10


@dataclass
class FactorRecord:
    id: str = ""
    theme: str = ""
    sub_theme: str = ""
    code: str = ""
    code_hash: str = ""
    sharpe: float = 0.0
    win_rate: float = 0.0
    ic_mean: float = 0.0
    ir: float = 0.0
    monotonicity: float = 0.0
    turnover: float = 0.0
    max_drawdown: float = 0.0
    trades: int = 0
    quantile_spread: float = 0.0
    decay_halflife: Optional[int] = None
    status: str = "active"
    created_at: float = 0.0
    last_validated: float = 0.0
    validation_count: int = 0
    oos_sharpe_history: list[float] = field(default_factory=list)
    # 进化追踪
    evolution_attempts: int = 0   # 已尝试进化次数
    evolution_failures: int = 0   # 连续进化失败次数
    # 因子池分类 (旧字段, 保持向后兼容): "active" | "high_pool" | "low_pool"
    pool: str = "active"
    pool_score: float = 0.0       # 综合评分 = sharpe × win_rate (用于排序)
    # ── 五层分级系统 ──────────────────────────────────────────────
    # tier: "tier_elite"|"tier_high"|"tier_standard"|"tier_marginal"|"tier_retired"
    tier: str = "tier_marginal"
    # pool_score_v2 = sharpe × win_rate × (1 + 0.3 × monotonicity)
    pool_score_v2: float = 0.0
    # 历史分层记录 (最多保留 10 条)
    tier_history: list[dict] = field(default_factory=list)
    # 部署目标: ["live", "screener_elite", "screener_diversified", "screener_thematic", "research", "archive"]
    deployment_targets: list[str] = field(default_factory=list)
    # 实盘排名 (T1 因子才赋值)
    live_rank: Optional[int] = None
    # 筛选器组信息
    screener_group_type: str = ""   # "elite"|"diversified"|"thematic"
    screener_group_id: str = ""
    # 进化优先级 (越高越优先进化)
    evolution_priority: float = 0.0
    # 是否确认已衰减
    decay_confirmed: bool = False
    # 最后部署时间
    last_deployed_at: float = 0.0
    # 建议的买卖规则（对应 139 TradingRules 结构）
    # 因子入库时根据主题自动推导，促进因子到实盘策略的转化
    suggested_trading_rules: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> FactorRecord:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in known}
        return cls(**filtered)


class FactorLibrary:
    """因子库管理器，基于 Redis 的持久化存储。"""

    def __init__(self, redis_client=None):
        self._redis = redis_client

    async def _get_redis(self):
        if self._redis is None:
            import os
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(
                os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
            )
        return self._redis

    async def get_all_factors(self, status: str = "active") -> list[FactorRecord]:
        r = await self._get_redis()
        raw = await r.lrange(REDIS_KEY_FACTORS, 0, -1)
        factors = []
        for item in raw:
            try:
                d = json.loads(item)
                rec = FactorRecord.from_dict(d)
                if status == "" or rec.status == status:
                    factors.append(rec)
            except (json.JSONDecodeError, TypeError):
                continue
        return factors

    async def count(self, status: str = "active") -> int:
        factors = await self.get_all_factors(status)
        return len(factors)

    async def check_admission(self, metrics: dict, code: str = "") -> tuple[bool, str]:
        """检查因子指标是否满足入库门槛。

        过拟合和未来函数检测已在 alpha_digger 的 full_audit 中完成，
        这里只做基础的指标门槛检查。
        """
        sharpe = metrics.get("sharpe_ratio") or metrics.get("sharpe") or 0
        if sharpe < ADMISSION_THRESHOLDS["sharpe_min"]:
            return False, f"sharpe {sharpe:.3f} < {ADMISSION_THRESHOLDS['sharpe_min']}"

        ic_mean = metrics.get("factor_ic") or metrics.get("mean_ic") or 0
        if abs(ic_mean) < ADMISSION_THRESHOLDS["ic_mean_min"]:
            return False, f"|ic_mean| {abs(ic_mean):.4f} < {ADMISSION_THRESHOLDS['ic_mean_min']}"

        ir = metrics.get("sortino_ratio") or metrics.get("ir") or 0
        if abs(ir) < ADMISSION_THRESHOLDS["ir_min"]:
            return False, f"|ir| {abs(ir):.3f} < {ADMISSION_THRESHOLDS['ir_min']}"

        trades = metrics.get("total_trades") or metrics.get("trades") or 0
        if trades < ADMISSION_THRESHOLDS["trades_min"]:
            return False, f"trades {trades} < {ADMISSION_THRESHOLDS['trades_min']}"

        win_rate_raw = metrics.get("win_rate_pct") or metrics.get("win_rate") or 0
        win_rate = win_rate_raw / 100.0 if win_rate_raw > 1 else win_rate_raw
        if win_rate < ADMISSION_THRESHOLDS["win_rate_min"]:
            return False, f"win_rate {win_rate:.3f} < {ADMISSION_THRESHOLDS['win_rate_min']}"

        # PBO 过拟合概率门槛 (CSCV 检验)
        pbo_score = metrics.get("pbo_score")
        if pbo_score is not None and pbo_score > ADMISSION_THRESHOLDS["pbo_max"]:
            return False, f"pbo_score {pbo_score:.3f} > {ADMISSION_THRESHOLDS['pbo_max']} (CSCV 过拟合)"

        return True, "PASS"

    async def check_duplication(self, code_hash: str) -> bool:
        """检查代码哈希是否已存在。"""
        factors = await self.get_all_factors(status="")
        return any(f.code_hash == code_hash for f in factors)

    async def check_metrics_similarity(self, metrics: dict) -> tuple[bool, str]:
        """基于 metrics 向量判断新因子是否与现有因子过度相似。

        用 (sharpe, ic_mean, ir, win_rate, turnover) 做简单距离检查。
        精确的因子相关性需要在 139 端用因子矩阵做截面相关，
        这里先做轻量级的指标空间近似去重。
        """
        factors = await self.get_all_factors()
        if not factors:
            return True, "库内无因子，无需去重"

        new_vec = [
            metrics.get("sharpe", 0),
            metrics.get("mean_ic", 0) * 100,
            metrics.get("ir", 0),
            metrics.get("win_rate", 0),
            metrics.get("turnover_mean", 0) * 10,
        ]

        for f in factors:
            exist_vec = [
                f.sharpe, f.ic_mean * 100, f.ir, f.win_rate, f.turnover * 10,
            ]
            dist_sq = sum((a - b) ** 2 for a, b in zip(new_vec, exist_vec))
            if dist_sq < 0.001:
                return False, f"与因子 {f.id} 指标过度相似 (dist²={dist_sq:.4f})"

        return True, "PASS"

    async def add_factor(
        self,
        code: str,
        metrics: dict,
        theme: str = "",
        sub_theme: str = "",
        suggested_trading_rules: Optional[dict] = None,
    ) -> tuple[bool, str, Optional[str]]:
        """尝试将因子加入库中。返回 (是否成功, 原因, factor_id)。"""
        import hashlib
        code_hash = hashlib.sha256(code.encode()).hexdigest()[:16]

        if await self.check_duplication(code_hash):
            return False, "代码哈希重复", None

        passed, reason = await self.check_admission(metrics, code=code)
        if not passed:
            return False, f"未达入库门槛: {reason}", None

        unique, sim_reason = await self.check_metrics_similarity(metrics)
        if not unique:
            return False, f"指标去重: {sim_reason}", None

        current_count = await self.count(status="")
        if current_count >= MAX_LIBRARY_SIZE:
            return False, f"因子库已满 ({current_count}/{MAX_LIBRARY_SIZE})", None

        factor_id = f"fac_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        record = FactorRecord(
            id=factor_id,
            theme=theme,
            sub_theme=sub_theme,
            code=code,
            code_hash=code_hash,
            sharpe=metrics.get("sharpe", 0),
            win_rate=metrics.get("win_rate", 0),
            ic_mean=metrics.get("mean_ic", 0),
            ir=metrics.get("ir", 0),
            monotonicity=metrics.get("monotonicity_score", 0),
            turnover=metrics.get("turnover_mean", 0),
            max_drawdown=metrics.get("max_drawdown", 0),
            trades=metrics.get("trades", 0),
            quantile_spread=metrics.get("quantile_spread", 0),
            decay_halflife=metrics.get("decay_halflife"),
            status="active",
            created_at=time.time(),
            last_validated=time.time(),
            suggested_trading_rules=suggested_trading_rules or {},
        )

        r = await self._get_redis()
        await r.lpush(REDIS_KEY_FACTORS, json.dumps(record.to_dict(), ensure_ascii=False, default=str))
        await r.ltrim(REDIS_KEY_FACTORS, 0, MAX_LIBRARY_SIZE - 1)

        await r.hincrby(REDIS_KEY_STATS, "total_admitted", 1)
        await r.hset(REDIS_KEY_STATS, "last_admission", factor_id)

        logger.info("因子入库: %s (theme=%s, sharpe=%.3f, ir=%.3f)",
                     factor_id, theme, record.sharpe, record.ir)
        return True, "入库成功", factor_id

    async def mark_decayed(self, factor_id: str) -> bool:
        """将指定因子标记为衰减状态。"""
        r = await self._get_redis()
        raw_list = await r.lrange(REDIS_KEY_FACTORS, 0, -1)
        for i, item in enumerate(raw_list):
            try:
                d = json.loads(item)
                if d.get("id") == factor_id:
                    d["status"] = "decayed"
                    d["last_validated"] = time.time()
                    await r.lset(REDIS_KEY_FACTORS, i, json.dumps(d, ensure_ascii=False, default=str))
                    logger.info("因子标记衰减: %s", factor_id)
                    return True
            except (json.JSONDecodeError, TypeError):
                continue
        return False

    async def retire_factor(self, factor_id: str) -> bool:
        """彻底退休因子（不删除，仅标记）。"""
        r = await self._get_redis()
        raw_list = await r.lrange(REDIS_KEY_FACTORS, 0, -1)
        for i, item in enumerate(raw_list):
            try:
                d = json.loads(item)
                if d.get("id") == factor_id:
                    d["status"] = "retired"
                    await r.lset(REDIS_KEY_FACTORS, i, json.dumps(d, ensure_ascii=False, default=str))
                    logger.info("因子退休: %s", factor_id)
                    return True
            except (json.JSONDecodeError, TypeError):
                continue
        return False

    async def get_stats(self) -> dict:
        """获取因子库统计信息。"""
        r = await self._get_redis()
        raw_stats = await r.hgetall(REDIS_KEY_STATS)
        stats = {k.decode() if isinstance(k, bytes) else k:
                 v.decode() if isinstance(v, bytes) else v
                 for k, v in raw_stats.items()}

        all_factors = await self.get_all_factors(status="")
        active = [f for f in all_factors if f.status == "active"]
        decayed = [f for f in all_factors if f.status == "decayed"]

        stats["active_count"] = len(active)
        stats["decayed_count"] = len(decayed)
        stats["total_count"] = len(all_factors)
        stats["ready_to_combine"] = len(active) >= COMBINE_THRESHOLD

        if active:
            stats["best_sharpe"] = max(f.sharpe for f in active)
            stats["best_ir"] = max(f.ir for f in active)
            stats["avg_sharpe"] = sum(f.sharpe for f in active) / len(active)
            themes = {}
            for f in active:
                themes[f.theme] = themes.get(f.theme, 0) + 1
            stats["theme_distribution"] = themes

        return stats

    @staticmethod
    def _is_combinable(code: str) -> bool:
        """检查因子代码是否适合融合（排除计算量过大的因子）。

        嵌套 for 循环在 6300 股 × 120 天的矩阵上会导致沙箱 OOM/超时。
        rolling.apply(lambda) 虽慢但通常可接受。
        """
        has_nested_loop = ("for i in range" in code and "for j in range" in code)
        return not has_nested_loop

    async def get_combine_candidates(self) -> list[FactorRecord]:
        """获取可用于因子融合的候选因子列表（按 IR 降序，排除超慢因子）。"""
        active = await self.get_all_factors(status="active")
        combinable = [f for f in active if self._is_combinable(f.code)]
        return sorted(combinable, key=lambda f: abs(f.ir), reverse=True)

    async def get_smart_combine_groups(self, max_group_size: int = 4) -> list[list[FactorRecord]]:
        """智能择优融合: 生成跨主题互补组合。

        策略:
          1. 按主题分组，每个主题取 IR 最高的代表因子
          2. 跨主题两两/三三/四四组合 (不同主题 = 低相关 = 最大化互补)
          3. 同主题内取 top 2 做 "精炼" 组合 (同类增强)

        Returns:
            list[list[FactorRecord]]: 每个内层列表是一个推荐的融合组合
        """
        from itertools import combinations

        candidates = await self.get_combine_candidates()
        if len(candidates) < 2:
            return []

        # 按主题分组，每组取 IR 最高的代表
        theme_groups: dict[str, list[FactorRecord]] = {}
        for f in candidates:
            theme = f.theme or "unknown"
            if theme not in theme_groups:
                theme_groups[theme] = []
            theme_groups[theme].append(f)

        # 每主题代表 (IR 最高)
        theme_reps: list[FactorRecord] = []
        for theme, factors in theme_groups.items():
            best = max(factors, key=lambda f: abs(f.ir))
            theme_reps.append(best)
        theme_reps.sort(key=lambda f: abs(f.ir), reverse=True)

        groups = []

        # 策略 A: 跨主题互补组合 (2-4 个不同主题)
        if len(theme_reps) >= 2:
            for size in range(2, min(max_group_size + 1, len(theme_reps) + 1)):
                # 取 top 主题的组合
                for combo in combinations(theme_reps[:8], size):
                    # 确保每个因子来自不同主题
                    themes_in_combo = set(f.theme for f in combo)
                    if len(themes_in_combo) == len(combo):
                        groups.append(list(combo))
                    if len(groups) >= 20:
                        break
                if len(groups) >= 20:
                    break

        # 策略 B: 同主题内精炼 (同类 top 2 融合增强)
        for theme, factors in theme_groups.items():
            if len(factors) >= 2:
                top2 = sorted(factors, key=lambda f: abs(f.ir), reverse=True)[:2]
                # 只有当两个因子指标差异足够大时才融合 (避免同质融合)
                metric_dist = abs(top2[0].sharpe - top2[1].sharpe) + abs(top2[0].ic_mean - top2[1].ic_mean) * 100
                if metric_dist > 0.1:
                    groups.append(top2)

        return groups

    async def get_greedy_combine_sequence(self, max_factors: int = 5) -> list[FactorRecord]:
        """贪心择优序列: 从最强因子开始，逐步找到最互补的因子组合。

        用于 greedy combine: 先取 IR 最高的因子作为 base，
        然后逐个从剩余因子中选出与当前组合 "最不相似" 的因子加入。

        相似度 = 主题相同罚分 + 指标空间距离。
        目标: 最大化组合内因子的多样性。

        Returns:
            list[FactorRecord]: 按加入顺序排列的因子序列
        """
        candidates = await self.get_combine_candidates()
        if len(candidates) < 2:
            return candidates

        # 从 IR 最高的开始
        selected = [candidates[0]]
        remaining = candidates[1:]

        while len(selected) < max_factors and remaining:
            best_score = -1
            best_idx = 0

            for idx, candidate in enumerate(remaining):
                # 计算与已选因子的互补性分数
                score = self._complementarity_score(candidate, selected)
                if score > best_score:
                    best_score = score
                    best_idx = idx

            selected.append(remaining.pop(best_idx))

        return selected

    @staticmethod
    def _complementarity_score(candidate: 'FactorRecord', selected: list['FactorRecord']) -> float:
        """计算候选因子与已选因子组合的互补性。

        分数越高 = 越互补 (主题不同 + 指标差异大 + 候选本身质量高)。
        """
        score = 0.0

        # 候选自身质量 (IR 基础分)
        score += abs(candidate.ir) * 0.3

        for s in selected:
            # 主题多样性奖励: 不同主题 +1, 相同主题 -0.5
            if candidate.theme != s.theme:
                score += 1.0
            else:
                score -= 0.5

            # 指标空间距离 (越远越互补)
            dist = (
                abs(candidate.sharpe - s.sharpe) * 0.3 +
                abs(candidate.ic_mean - s.ic_mean) * 50 +
                abs(candidate.win_rate - s.win_rate) * 2 +
                abs(candidate.turnover - s.turnover) * 5
            )
            score += dist * 0.2

        return score / max(len(selected), 1)

    async def should_combine(self) -> bool:
        count = await self.count()
        return count >= COMBINE_THRESHOLD

    # ── 融合记录管理 ────────────────────────────────

    COMBINE_LOG_KEY = "openclaw:factor_library:combine_log"
    COMBINE_LOG_MAX = 50

    async def save_combine_record(self, record: dict) -> str:
        """保存一条融合记录，返回 record_id。"""
        r = await self._get_redis()
        record_id = f"cmb_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        record["id"] = record_id
        record["created_at"] = time.time()
        await r.lpush(self.COMBINE_LOG_KEY,
                       json.dumps(record, ensure_ascii=False, default=str))
        await r.ltrim(self.COMBINE_LOG_KEY, 0, self.COMBINE_LOG_MAX - 1)
        return record_id

    async def get_combine_records(self, limit: int = 20) -> list[dict]:
        """获取融合历史记录列表。"""
        r = await self._get_redis()
        raw = await r.lrange(self.COMBINE_LOG_KEY, 0, limit - 1)
        records = []
        for item in raw:
            try:
                records.append(json.loads(item))
            except (json.JSONDecodeError, TypeError):
                continue
        return records

    async def get_combine_record(self, record_id: str) -> dict | None:
        """获取单条融合记录。"""
        records = await self.get_combine_records(limit=self.COMBINE_LOG_MAX)
        for rec in records:
            if rec.get("id") == record_id:
                return rec
        return None

    @staticmethod
    def evaluate_combine_quality(
        input_factors: list[dict],
        combined_metrics: dict,
    ) -> dict:
        """评估融合结果质量，与输入因子的最佳单因子对比。

        返回:
            verdict: "accept" | "marginal" | "reject"
            report: 详细对比报告
            improvements: 各指标改善情况
        """
        if not input_factors:
            return {"verdict": "reject", "report": "缺少输入因子数据",
                    "improvements": {}}
        if not combined_metrics or not any(v for k, v in combined_metrics.items() if k != "sharpe"):
            return {"verdict": "reject",
                    "report": "融合回测未返回有效指标（metrics 为空），请检查回测流程日志",
                    "improvements": {}}

        best_single_sharpe = max(f.get("sharpe", 0) for f in input_factors)
        best_single_ir = max(abs(f.get("ir", 0)) for f in input_factors)
        avg_single_sharpe = sum(f.get("sharpe", 0) for f in input_factors) / len(input_factors)

        cmb_sharpe = combined_metrics.get("sharpe", 0)
        cmb_ir = abs(combined_metrics.get("ir", 0))
        cmb_dd = combined_metrics.get("max_drawdown", 1)
        cmb_wr = combined_metrics.get("win_rate", 0)

        improvements = {
            "sharpe_vs_best": cmb_sharpe - best_single_sharpe,
            "sharpe_vs_avg": cmb_sharpe - avg_single_sharpe,
            "ir_vs_best": cmb_ir - best_single_ir,
            "combined_sharpe": cmb_sharpe,
            "best_single_sharpe": best_single_sharpe,
            "avg_single_sharpe": avg_single_sharpe,
        }

        lines = [
            f"融合因子数: {len(input_factors)}",
            f"融合后 Sharpe: {cmb_sharpe:.3f} (最佳单因子: {best_single_sharpe:.3f}, 平均: {avg_single_sharpe:.3f})",
            f"融合后 IR: {cmb_ir:.3f} (最佳单因子: {best_single_ir:.3f})",
            f"融合后 Max DD: {cmb_dd*100:.2f}%",
            f"融合后 Win Rate: {cmb_wr*100:.1f}%",
            "",
        ]

        if cmb_sharpe > best_single_sharpe * 1.1:
            verdict = "accept"
            lines.append("结论: ACCEPT — 融合后 Sharpe 超过最佳单因子 10%+，建议采纳")
        elif cmb_sharpe >= best_single_sharpe * 0.95:
            verdict = "marginal"
            lines.append("结论: MARGINAL — 融合效果与最佳单因子接近，如回撤更低则可采纳")
            if cmb_dd < min(f.get("max_drawdown", 1) for f in input_factors):
                verdict = "accept"
                lines[-1] = "结论: ACCEPT (marginal→accept) — 回撤更低，风险调整后更优"
        else:
            verdict = "reject"
            lines.append(f"结论: REJECT — 融合后 Sharpe 低于最佳单因子 ({cmb_sharpe:.3f} < {best_single_sharpe:.3f})，建议回退")

        return {
            "verdict": verdict,
            "report": "\n".join(lines),
            "improvements": improvements,
        }

    async def increment_evolution_failure(self, factor_id: str) -> int:
        """记录一次进化失败，返回当前连续失败次数。"""
        r = await self._get_redis()
        raw_list = await r.lrange(REDIS_KEY_FACTORS, 0, -1)
        failures = 0
        for i, item in enumerate(raw_list):
            try:
                d = json.loads(item)
                if d.get("id") == factor_id:
                    d["evolution_attempts"] = d.get("evolution_attempts", 0) + 1
                    d["evolution_failures"] = d.get("evolution_failures", 0) + 1
                    failures = d["evolution_failures"]
                    await r.lset(REDIS_KEY_FACTORS, i, json.dumps(d, ensure_ascii=False, default=str))
                    break
            except (json.JSONDecodeError, TypeError):
                continue
        return failures

    async def record_evolution_success(self, factor_id: str, new_code: str, new_metrics: dict) -> bool:
        """记录进化成功，重置失败计数，更新代码和指标。"""
        import hashlib
        r = await self._get_redis()
        raw_list = await r.lrange(REDIS_KEY_FACTORS, 0, -1)
        for i, item in enumerate(raw_list):
            try:
                d = json.loads(item)
                if d.get("id") == factor_id:
                    d["code"] = new_code
                    d["code_hash"] = hashlib.sha256(new_code.encode()).hexdigest()[:16]
                    d["sharpe"] = new_metrics.get("sharpe", d.get("sharpe", 0))
                    d["win_rate"] = new_metrics.get("win_rate", d.get("win_rate", 0))
                    d["ir"] = new_metrics.get("ir", d.get("ir", 0))
                    d["trades"] = new_metrics.get("trades", d.get("trades", 0))
                    d["evolution_attempts"] = d.get("evolution_attempts", 0) + 1
                    d["evolution_failures"] = 0  # 重置连续失败
                    d["last_validated"] = time.time()
                    await r.lset(REDIS_KEY_FACTORS, i, json.dumps(d, ensure_ascii=False, default=str))
                    logger.info("因子进化成功: %s sharpe %.3f→%.3f", factor_id,
                                d.get("sharpe", 0), new_metrics.get("sharpe", 0))
                    return True
            except (json.JSONDecodeError, TypeError):
                continue
        return False

    async def set_pool(self, factor_id: str, pool: str, pool_score: float = 0.0) -> bool:
        """设置因子池分类。pool: 'active'|'high_pool'|'low_pool'"""
        r = await self._get_redis()
        raw_list = await r.lrange(REDIS_KEY_FACTORS, 0, -1)
        for i, item in enumerate(raw_list):
            try:
                d = json.loads(item)
                if d.get("id") == factor_id:
                    d["pool"] = pool
                    d["pool_score"] = pool_score
                    await r.lset(REDIS_KEY_FACTORS, i, json.dumps(d, ensure_ascii=False, default=str))
                    logger.info("因子池更新: %s → %s (score=%.3f)", factor_id, pool, pool_score)
                    return True
            except (json.JSONDecodeError, TypeError):
                continue
        return False

    async def get_high_pool_factors(self, limit: int = 50) -> list["FactorRecord"]:
        """获取高因子池中的因子，按 pool_score 降序排序。"""
        factors = await self.get_all_factors(status="active")
        high = [f for f in factors if getattr(f, "pool", "active") == "high_pool"]
        high.sort(key=lambda f: getattr(f, "pool_score", 0), reverse=True)
        return high[:limit]

    async def get_factors_for_evolution(self, limit: int = 30) -> list["FactorRecord"]:
        """获取需要进化的因子 (tier_marginal/tier_standard, failures<5)。
        按 evolution_priority 降序：优先进化最接近晋升门槛的因子。"""
        factors = await self.get_all_factors(status="active")
        candidates = [
            f for f in factors
            if getattr(f, "tier", "tier_marginal") in ("tier_marginal", "tier_standard")
            and getattr(f, "pool", "active") not in ("high_pool", "low_pool")
            and getattr(f, "evolution_failures", 0) < FAILURE_THRESHOLD_EVOLUTION
        ]
        candidates.sort(key=lambda f: getattr(f, "evolution_priority", 0), reverse=True)
        return candidates[:limit]

    @staticmethod
    def _compute_tier(sharpe: float, wr: float, monotonicity: float,
                      max_dd: float, failures: int) -> str:
        """根据指标计算五层分级。

        T1 Elite:   sharpe>=2.0, wr>=0.52
                    (monotonicity>=0.6 仅在有值时才作为额外加分项，不作硬门槛)
        T2 High:    sharpe>=1.5, wr>=0.48
        T3 Standard:sharpe>=1.0, wr>=0.45
        T4 Marginal:sharpe>=0.8, wr>=0.42, failures<5
        T5 Retired: failures>=5 OR below T4
        """
        if failures >= FAILURE_THRESHOLD_EVOLUTION:
            return "tier_retired"
        # T1: 核心门槛 sharpe+wr，单调性有值时要求达标
        mono_ok = (monotonicity <= 0.01) or (monotonicity >= 0.6)  # 0 = 未采集，跳过
        if sharpe >= 2.0 and wr >= 0.52 and mono_ok:
            return "tier_elite"
        if sharpe >= 1.5 and wr >= 0.48:
            return "tier_high"
        if sharpe >= 1.0 and wr >= 0.45:
            return "tier_standard"
        if sharpe >= 0.8 and wr >= 0.42:
            return "tier_marginal"
        return "tier_retired"

    @staticmethod
    def _compute_pool_score_v2(sharpe: float, wr: float, monotonicity: float) -> float:
        """pool_score_v2 = sharpe × win_rate × (1 + 0.3 × monotonicity)"""
        return round(sharpe * wr * (1 + 0.3 * monotonicity), 4)

    @staticmethod
    def _compute_evolution_priority(sharpe: float, wr: float, tier: str) -> float:
        """计算进化优先级。

        优先级策略:
        - T3 因子接近 T2 门槛 (sharpe 在 [1.0, 1.5)) → 高优先级
        - T4 因子接近 T3 门槛 (sharpe 在 [0.8, 1.0)) → 中优先级
        - 因子质量越高，进化成功概率越大，优先级越高
        """
        if tier == "tier_standard":
            # 距 T2 门槛 (sharpe=1.5) 的接近度
            gap_to_t2 = 1.5 - sharpe
            priority = 2.0 - gap_to_t2  # sharpe 越接近 1.5，优先级越高
        elif tier == "tier_marginal":
            # 距 T3 门槛 (sharpe=1.0) 的接近度
            gap_to_t3 = 1.0 - sharpe
            priority = 1.0 - gap_to_t3
        else:
            priority = 0.0
        # 胜率加权
        return round(max(0.0, priority) * (1 + wr * 0.2), 4)

    @staticmethod
    def _tier_to_pool(tier: str) -> str:
        """将五层分级映射到旧版 pool 字段 (向后兼容)。"""
        if tier in ("tier_elite", "tier_high"):
            return "high_pool"
        if tier == "tier_retired":
            return "low_pool"
        return "active"

    @staticmethod
    def _tier_to_deployment_targets(tier: str) -> list[str]:
        """根据分层确定部署目标。"""
        mapping = {
            "tier_elite":    ["live", "screener_elite", "screener_diversified"],
            "tier_high":     ["screener_diversified", "screener_thematic"],
            "tier_standard": ["screener_thematic", "research"],
            "tier_marginal": ["research"],
            "tier_retired":  ["archive"],
        }
        return mapping.get(tier, ["research"])

    async def classify_all_factors(self) -> dict:
        """对所有 active 因子执行五层分类，更新 tier / pool / pool_score_v2 / deployment_targets。

        Tier 映射到旧 pool 字段 (向后兼容):
          T1 Elite / T2 High  → high_pool
          T3 Standard / T4 Marginal → active
          T5 Retired          → low_pool

        Returns: {"tier_elite": N1, "tier_high": N2, "tier_standard": N3,
                  "tier_marginal": N4, "tier_retired": N5,
                  "high_pool": Nh, "low_pool": Nl, "active": Na}
        """
        factors = await self.get_all_factors(status="active")
        counts: dict[str, int] = {
            "tier_elite": 0, "tier_high": 0, "tier_standard": 0,
            "tier_marginal": 0, "tier_retired": 0,
            "high_pool": 0, "low_pool": 0, "active": 0,
        }

        r = await self._get_redis()
        raw_list = await r.lrange(REDIS_KEY_FACTORS, 0, -1)

        # 建立 id→index 映射
        id_to_idx: dict[str, int] = {}
        id_to_raw: dict[str, dict] = {}
        for i, item in enumerate(raw_list):
            try:
                d = json.loads(item)
                fid = d.get("id", "")
                if fid:
                    id_to_idx[fid] = i
                    id_to_raw[fid] = d
            except (json.JSONDecodeError, TypeError):
                continue

        pipe = r.pipeline()
        updated_count = 0

        for f in factors:
            wr = f.win_rate if f.win_rate <= 1 else f.win_rate / 100
            failures = getattr(f, "evolution_failures", 0)
            max_dd = abs(f.max_drawdown) if f.max_drawdown else 1.0

            new_tier = self._compute_tier(f.sharpe, wr, f.monotonicity, max_dd, failures)
            new_pool = self._tier_to_pool(new_tier)
            new_score_v2 = self._compute_pool_score_v2(f.sharpe, wr, f.monotonicity)
            new_score_v1 = round(f.sharpe * wr, 4)
            new_priority = self._compute_evolution_priority(f.sharpe, wr, new_tier)
            new_targets = self._tier_to_deployment_targets(new_tier)

            counts[new_tier] += 1
            counts[new_pool] += 1

            # 只更新有变化的因子
            fid = f.id
            if fid not in id_to_idx:
                continue
            d = id_to_raw[fid]

            old_tier = d.get("tier", "")
            tier_changed = old_tier != new_tier

            d["tier"] = new_tier
            d["pool"] = new_pool
            d["pool_score"] = new_score_v1
            d["pool_score_v2"] = new_score_v2
            d["deployment_targets"] = new_targets
            d["evolution_priority"] = new_priority

            # 记录分层历史 (最多 10 条)
            if tier_changed:
                history = d.get("tier_history", [])
                history.append({"tier": new_tier, "ts": time.time(), "sharpe": f.sharpe})
                d["tier_history"] = history[-10:]

            pipe.lset(REDIS_KEY_FACTORS, id_to_idx[fid],
                      json.dumps(d, ensure_ascii=False, default=str))
            updated_count += 1

        if updated_count:
            await pipe.execute()

        logger.info("因子分层完成: %s (更新 %d 条)", counts, updated_count)
        return counts

    async def get_factors_by_tier(self, tier: str, limit: int = 100) -> list["FactorRecord"]:
        """按分层获取因子，按 pool_score_v2 降序。"""
        factors = await self.get_all_factors(status="active")
        result = [f for f in factors if getattr(f, "tier", "") == tier]
        result.sort(key=lambda f: getattr(f, "pool_score_v2", 0), reverse=True)
        return result[:limit]

    async def get_live_factors(self, limit: int = 20) -> list["FactorRecord"]:
        """获取 T1 实盘因子，按 pool_score_v2 降序，赋值 live_rank。"""
        elites = await self.get_factors_by_tier("tier_elite", limit=limit)
        r = await self._get_redis()
        raw_list = await r.lrange(REDIS_KEY_FACTORS, 0, -1)

        id_to_idx: dict[str, int] = {}
        for i, item in enumerate(raw_list):
            try:
                d = json.loads(item)
                fid = d.get("id", "")
                if fid:
                    id_to_idx[fid] = i
            except (json.JSONDecodeError, TypeError):
                continue

        pipe = r.pipeline()
        for rank, f in enumerate(elites, 1):
            if f.id in id_to_idx:
                item = await r.lindex(REDIS_KEY_FACTORS, id_to_idx[f.id])
                try:
                    d = json.loads(item)
                    d["live_rank"] = rank
                    d["last_deployed_at"] = time.time()
                    pipe.lset(REDIS_KEY_FACTORS, id_to_idx[f.id],
                              json.dumps(d, ensure_ascii=False, default=str))
                except (json.JSONDecodeError, TypeError):
                    pass
        if elites:
            await pipe.execute()

        return elites

    async def close(self):
        if self._redis:
            await self._redis.close()


_factor_library: Optional[FactorLibrary] = None


def get_factor_library() -> FactorLibrary:
    global _factor_library
    if _factor_library is None:
        _factor_library = FactorLibrary()
    return _factor_library

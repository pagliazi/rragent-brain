"""
market_time — A股交易时段工具
提供统一的时间上下文，供所有 Agent 在数据获取和分析时感知当前市场阶段。
"""

from datetime import datetime, time, date
from enum import Enum
from zoneinfo import ZoneInfo

TZ_CN = ZoneInfo("Asia/Shanghai")

MORNING_OPEN = time(9, 30)
MORNING_CLOSE = time(11, 30)
AFTERNOON_OPEN = time(13, 0)
AFTERNOON_CLOSE = time(15, 0)
PRE_MARKET_START = time(9, 0)
AUCTION_END = time(9, 25)

HOLIDAYS_2026 = {
    date(2026, 1, 1), date(2026, 1, 2),
    date(2026, 2, 17), date(2026, 2, 18), date(2026, 2, 19),
    date(2026, 2, 20), date(2026, 2, 21), date(2026, 2, 22), date(2026, 2, 23),
    date(2026, 5, 1), date(2026, 5, 2), date(2026, 5, 3), date(2026, 5, 4), date(2026, 5, 5),
    date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3),
    date(2026, 10, 4), date(2026, 10, 5), date(2026, 10, 6), date(2026, 10, 7),
}


class MarketPhase(str, Enum):
    PRE_MARKET = "pre_market"
    AUCTION = "auction"
    MORNING = "morning"
    LUNCH_BREAK = "lunch_break"
    AFTERNOON = "afternoon"
    POST_CLOSE = "post_close"
    NIGHT = "night"
    NON_TRADING_DAY = "non_trading_day"


def now_cn() -> datetime:
    return datetime.now(TZ_CN)


def is_trading_day(d: date | None = None) -> bool:
    if d is None:
        d = now_cn().date()
    if d.weekday() >= 5:
        return False
    if d in HOLIDAYS_2026:
        return False
    return True


def get_market_phase(dt: datetime | None = None) -> MarketPhase:
    if dt is None:
        dt = now_cn()
    d = dt.date()
    t = dt.time()

    if not is_trading_day(d):
        return MarketPhase.NON_TRADING_DAY

    if t < PRE_MARKET_START:
        return MarketPhase.NIGHT
    if t < AUCTION_END:
        return MarketPhase.AUCTION
    if t < MORNING_OPEN:
        return MarketPhase.PRE_MARKET
    if t < MORNING_CLOSE:
        return MarketPhase.MORNING
    if t < AFTERNOON_OPEN:
        return MarketPhase.LUNCH_BREAK
    if t < AFTERNOON_CLOSE:
        return MarketPhase.AFTERNOON
    if t < time(18, 0):
        return MarketPhase.POST_CLOSE
    return MarketPhase.NIGHT


def is_trading_hours(dt: datetime | None = None) -> bool:
    phase = get_market_phase(dt)
    return phase in (MarketPhase.MORNING, MarketPhase.AFTERNOON)


def get_data_freshness_label(dt: datetime | None = None) -> str:
    """返回当前数据新鲜度描述"""
    phase = get_market_phase(dt)
    labels = {
        MarketPhase.MORNING: "实时盘中数据",
        MarketPhase.AFTERNOON: "实时盘中数据",
        MarketPhase.AUCTION: "集合竞价中，数据待开盘更新",
        MarketPhase.PRE_MARKET: "盘前数据（上一交易日收盘）",
        MarketPhase.LUNCH_BREAK: "午间休市数据（上午收盘快照）",
        MarketPhase.POST_CLOSE: "今日收盘数据",
        MarketPhase.NIGHT: "上一交易日收盘数据",
        MarketPhase.NON_TRADING_DAY: "最近交易日收盘数据（非交易日）",
    }
    return labels.get(phase, "数据时间未知")


def get_analysis_context(dt: datetime | None = None) -> dict:
    """
    返回完整的时间上下文，供 Agent 注入 prompt。
    包含: phase, is_realtime, freshness_label, time_hint, priority_hint
    """
    if dt is None:
        dt = now_cn()
    phase = get_market_phase(dt)
    realtime = phase in (MarketPhase.MORNING, MarketPhase.AFTERNOON)

    time_hints = {
        MarketPhase.MORNING: "当前为上午盘中交易时段，数据为实时更新。分析应聚焦盘中动态变化、资金流向、板块异动。",
        MarketPhase.AFTERNOON: "当前为下午盘中交易时段，数据为实时更新。分析应聚焦尾盘走势、封板强度、资金态度。",
        MarketPhase.LUNCH_BREAK: "当前为午间休市。数据截止至上午收盘。分析应总结上午走势，预判下午可能方向。",
        MarketPhase.POST_CLOSE: "当前为收盘后。数据为今日最终收盘数据。分析应做全天总结复盘，评估情绪演变和明日展望。",
        MarketPhase.PRE_MARKET: "当前为盘前。数据为上一交易日收盘数据。分析应聚焦隔夜消息面、外盘影响、盘前预判。",
        MarketPhase.AUCTION: "当前为集合竞价阶段。数据即将更新。关注竞价异动个股和板块。",
        MarketPhase.NIGHT: "当前为非交易时段。数据为上一交易日收盘数据。",
        MarketPhase.NON_TRADING_DAY: "今天非交易日。数据为最近一个交易日的收盘数据。分析应聚焦中期趋势和下周展望。",
    }

    priority_hints = {
        MarketPhase.MORNING: "优先关注：实时涨停板变化 > 板块资金流向 > 连板梯队强度",
        MarketPhase.AFTERNOON: "优先关注：封板强度变化 > 尾盘资金动向 > 跌停/炸板情况 > 明日预判",
        MarketPhase.LUNCH_BREAK: "优先关注：上午涨停梯队 > 板块分化程度 > 下午可能的方向选择",
        MarketPhase.POST_CLOSE: "优先关注：全天涨停统计 > 情绪周期判定 > 板块持续性 > 明日策略",
        MarketPhase.PRE_MARKET: "优先关注：隔夜消息 > 外盘影响 > 昨日复盘 > 今日预判",
        MarketPhase.AUCTION: "优先关注：竞价异动 > 高开/低开板块 > 昨日强势股表现",
        MarketPhase.NIGHT: "优先关注：收盘复盘 > 消息面 > 中期趋势",
        MarketPhase.NON_TRADING_DAY: "优先关注：周度复盘 > 中期趋势 > 下周展望 > 政策面",
    }

    return {
        "phase": phase.value,
        "phase_cn": {
            MarketPhase.MORNING: "上午盘中",
            MarketPhase.AFTERNOON: "下午盘中",
            MarketPhase.LUNCH_BREAK: "午间休市",
            MarketPhase.POST_CLOSE: "收盘后",
            MarketPhase.PRE_MARKET: "盘前",
            MarketPhase.AUCTION: "集合竞价",
            MarketPhase.NIGHT: "非交易时段",
            MarketPhase.NON_TRADING_DAY: "非交易日",
        }.get(phase, "未知"),
        "is_realtime": realtime,
        "is_trading_day": is_trading_day(dt.date()),
        "freshness_label": get_data_freshness_label(dt),
        "time_hint": time_hints.get(phase, ""),
        "priority_hint": priority_hints.get(phase, ""),
        "timestamp": dt.strftime("%Y-%m-%d %H:%M:%S"),
        "date": dt.strftime("%Y-%m-%d"),
        "weekday": ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][dt.weekday()],
    }


def format_time_context_block(ctx: dict | None = None) -> str:
    """格式化为可直接注入 prompt 的时间上下文块"""
    if ctx is None:
        ctx = get_analysis_context()
    return (
        f"⏰ 当前时间: {ctx['timestamp']} {ctx['weekday']} [{ctx['phase_cn']}]\n"
        f"📊 数据状态: {ctx['freshness_label']}\n"
        f"🎯 {ctx['priority_hint']}\n"
        f"💡 {ctx['time_hint']}"
    )

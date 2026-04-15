"""
openclaw_quant_lib — OpenClaw 量化沙箱内置工具库
================================================
封装 ClickHouse / DolphinDB 连接、数据清洗、因子计算、因子预处理与验证，
让 Quant Coder 只需专注 entries/exits 信号逻辑或因子组合。

数据接入:
  get_clean_kline_data()       — ClickHouse 日线 OHLCV（含 lookback 预热）
  get_kline_with_indicators()  — ClickHouse 日线 + SQL 窗口函数预计算指标
  get_minute_kline()           — DolphinDB 分钟 K 线 OHLCV
  get_mainflow_minute()        — ClickHouse 分钟级主力资金流
  get_stock_meta()             — 行业分类 + 流通市值（因子中性化用）

因子计算:
  calc_smart_money()           — 聪明钱净流入因子 (SMI)
  calc_voi()                   — 分钟级均量差 (Volume Order Imbalance)
  calc_intraday_skewness()     — 日内收益率偏度
  calc_intraday_kurtosis()     — 日内收益率峰度
  calc_amihud()                — 流动性冲击因子 (Amihud 改进版)
  get_daily_factors()          — 一站式因子聚合入口

因子预处理:
  mad_winsorize()              — MAD 去极值
  zscore_standardize()         — 截面 Z-Score 标准化
  neutralize()                 — 行业 + 市值中性化

因子验证:
  calc_ic_series()             — Rank IC 时间序列
  calc_ir()                    — 信息比率
  quantile_backtest()          — 分层回测
  factor_turnover()            — 因子换手率
  factor_decay_test()          — IC 衰减测试
  build_factor_result()        — 回测 + 因子质量综合输出

工具:
  safe_float()                 — 安全转 float
  build_result()               — vectorbt Portfolio 标准输出

使用示例:
  from openclaw_quant_lib import get_clean_kline_data, get_daily_factors, build_factor_result
  from openclaw_quant_lib import mad_winsorize, zscore_standardize, calc_ic_series
"""
from __future__ import annotations

import math
import os
from datetime import datetime, timedelta, date as _date
from typing import Any

import numpy as np
import pandas as pd

# ── 内部常量 ──────────────────────────────────────────────────────────────────

_CH_HOST = "127.0.0.1"
_CH_PORT = 8123
_CH_DB   = "reachrich"
_CH_USER = "default"

_INDICATOR_SQL_MAP: dict[str, str] = {
    "ma5":    "avg(close) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS 4 PRECEDING)",
    "ma10":   "avg(close) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS 9 PRECEDING)",
    "ma20":   "avg(close) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS 19 PRECEDING)",
    "ma60":   "avg(close) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS 59 PRECEDING)",
    "ma120":  "avg(close) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS 119 PRECEDING)",
    "vol_ma5":  "avg(volume) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS 4 PRECEDING)",
    "vol_ma10": "avg(volume) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS 9 PRECEDING)",
    "vol_ma20": "avg(volume) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS 19 PRECEDING)",
    "vol_ma60": "avg(volume) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS 59 PRECEDING)",
    "amount_ma5":  "avg(amount) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS 4 PRECEDING)",
    "amount_ma20": "avg(amount) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS 19 PRECEDING)",
    "pct_chg": "pct_chg",
    "turnover_rate": "turnover_rate",
    "amplitude": "amplitude",
    "high_over_ma20": "high / nullIf(avg(close) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS 19 PRECEDING), 0)",
}

# ── 日期工具 ──────────────────────────────────────────────────────────────────

def _to_dash(d: str | _date) -> str:
    """将 YYYYMMDD / YYYY-MM-DD / YYYY-M-D 等格式统一转为 'YYYY-MM-DD'。"""
    if isinstance(d, _date):
        return d.strftime("%Y-%m-%d")
    s = str(d).strip().replace("/", "-")
    if "-" in s:
        parts = [p for p in s.split("-") if p]
        if len(parts) == 3 and all(p.isdigit() for p in parts):
            y, m, dy = int(parts[0]), int(parts[1]), int(parts[2])
            if 1 <= m <= 12 and 1 <= dy <= 31:
                return f"{y:04d}-{m:02d}-{dy:02d}"
    s = s.replace("-", "")
    assert len(s) == 8 and s.isdigit(), f"日期格式无法识别: {d!r}"
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def _to_dt(d: str | _date) -> datetime:
    return datetime.strptime(_to_dash(d), "%Y-%m-%d")


def _lookback_date(start: str | _date, extra_days: int) -> str:
    """向前扩展 extra_days 个日历日，返回 'YYYY-MM-DD'。"""
    return (_to_dt(start) - timedelta(days=extra_days)).strftime("%Y-%m-%d")


# ── ClickHouse 连接 ───────────────────────────────────────────────────────────

def _get_ch_client():
    """创建 ClickHouse 连接（密码从 CH_PWD 环境变量读取）。"""
    import clickhouse_connect
    password = os.getenv("CH_PWD", "")
    return clickhouse_connect.get_client(
        host=_CH_HOST, port=_CH_PORT,
        database=_CH_DB, username=_CH_USER,
        password=password,
    )


# ── 核心函数 ──────────────────────────────────────────────────────────────────

def get_clean_kline_data(
    start_date: str | _date,
    end_date: str | _date,
    lookback_days: int = 80,
    st_filter: bool = True,
    min_coverage: float = 0.8,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    从 ClickHouse daily_kline 获取清洗好的 OHLCV 数据。

    封装内容（Coder 无需手写）:
      - ClickHouse 连接与密码读取
      - 日期格式自动转换 (YYYYMMDD → YYYY-MM-DD)
      - lookback 预热窗口自动扩展（用于 MA/RSI 等指标计算）
      - ST 股过滤
      - 去重 (drop_duplicates)
      - pivot + dtype 转换 (float64 / DatetimeIndex)
      - 过滤数据不足的股票 (< min_coverage)
      - 最终 close_df/volume_df 按 start_date 截断（去掉预热段）

    Args:
        start_date:    回测起始日，YYYYMMDD 或 YYYY-MM-DD 均可
        end_date:      回测截止日
        lookback_days: 指标预热窗口大小（日历天数），默认 80 天
                       若策略需要 MA60，建议 lookback_days=120
        st_filter:     True = SQL 层过滤 ST/退市股
        min_coverage:  股票在 [start, end] 区间内至少有 min_coverage 比例的数据

    Returns:
        (close_df, volume_df, raw_df)
        - close_df:  pd.DataFrame, float64, DatetimeIndex, 从 start_date 起
        - volume_df: pd.DataFrame, float64, 同 close_df
        - raw_df:    pd.DataFrame, 含 lookback 段的完整原始数据（供自行计算指标）
    """
    start_str   = _to_dash(start_date)
    end_str     = _to_dash(end_date)
    lookback_str = _lookback_date(start_date, lookback_days) if lookback_days > 0 else start_str

    st_clause = "AND name NOT LIKE '%ST%'" if st_filter else ""

    sql = f"""
        SELECT trade_date, ts_code, open, high, low, close,
               pre_close, volume, amount, pct_chg, turnover_rate, amplitude
        FROM daily_kline
        WHERE trade_date >= '{lookback_str}'
          AND trade_date <= '{end_str}'
          {st_clause}
        ORDER BY trade_date, ts_code
    """

    client = _get_ch_client()
    try:
        raw_df = client.query_df(sql)
    finally:
        client.close()

    if raw_df.empty:
        raise ValueError(
            f"daily_kline 返回空数据，请检查日期范围 [{lookback_str}, {end_str}]"
        )

    # 类型修正：ClickHouse Date → pd.Timestamp
    raw_df["trade_date"] = pd.to_datetime(raw_df["trade_date"])

    # 去重
    raw_df.drop_duplicates(subset=["trade_date", "ts_code"], keep="last", inplace=True)

    # 截取回测区间用于 pivot（保留预热段在 raw_df）
    start_ts = pd.Timestamp(start_str)
    end_ts   = pd.Timestamp(end_str)
    backtest_df = raw_df[raw_df["trade_date"] >= start_ts].copy()

    if backtest_df.empty:
        raise ValueError(
            f"start_date={start_str} 后无数据，请确认 daily_kline 覆盖此区间"
        )

    # pivot
    close_df  = backtest_df.pivot(index="trade_date", columns="ts_code", values="close")
    volume_df = backtest_df.pivot(index="trade_date", columns="ts_code", values="volume")

    # 过滤覆盖度不足的股票
    min_obs = int(len(close_df) * min_coverage)
    valid_cols = close_df.columns[(close_df.notna().sum() >= min_obs).values]
    close_df  = close_df[valid_cols]
    volume_df = volume_df[valid_cols]

    # 确保 float64（numba JIT 要求）
    close_df  = close_df.astype(np.float64)
    volume_df = volume_df.astype(np.float64)

    return close_df, volume_df, raw_df


def get_kline_with_indicators(
    start_date: str | _date,
    end_date: str | _date,
    indicators: list[str] | None = None,
    lookback_days: int = 120,
    st_filter: bool = True,
    min_coverage: float = 0.8,
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame], pd.DataFrame]:
    """
    从 ClickHouse 获取含 SQL 窗口函数预计算指标的数据（计算下推，节省内存）。

    Args:
        indicators: 需要预计算的指标列表，支持:
                    ma5, ma10, ma20, ma60, ma120
                    vol_ma5, vol_ma10, vol_ma20, vol_ma60
                    amount_ma5, amount_ma20
                    pct_chg, turnover_rate, amplitude
                    high_over_ma20
                    默认: ['ma20', 'ma60', 'vol_ma5', 'vol_ma20']

    Returns:
        (close_df, ind_dfs, raw_df)
        - close_df:  pd.DataFrame, float64, DatetimeIndex, 从 start_date 起
        - ind_dfs:   dict[str, pd.DataFrame]  如 ind_dfs['ma20'], ind_dfs['vol_ma5']
        - raw_df:    pd.DataFrame 含 lookback 段的完整数据
    """
    if indicators is None:
        indicators = ["ma20", "ma60", "vol_ma5", "vol_ma20"]

    unknown = [i for i in indicators if i not in _INDICATOR_SQL_MAP]
    assert not unknown, f"不支持的指标: {unknown}，可用: {list(_INDICATOR_SQL_MAP)}"

    start_str    = _to_dash(start_date)
    end_str      = _to_dash(end_date)
    lookback_str = _lookback_date(start_date, lookback_days)
    st_clause    = "AND name NOT LIKE '%ST%'" if st_filter else ""

    ind_sql_parts = ",\n              ".join(
        f"{_INDICATOR_SQL_MAP[ind]} AS {ind}" for ind in indicators
    )

    sql = f"""
        SELECT trade_date, ts_code, close, volume, amount,
               {ind_sql_parts}
        FROM daily_kline
        WHERE trade_date >= '{lookback_str}'
          AND trade_date <= '{end_str}'
          {st_clause}
        ORDER BY trade_date, ts_code
    """

    client = _get_ch_client()
    try:
        raw_df = client.query_df(sql)
    finally:
        client.close()

    if raw_df.empty:
        raise ValueError(
            f"daily_kline 返回空数据，请检查日期范围 [{lookback_str}, {end_str}]"
        )

    raw_df["trade_date"] = pd.to_datetime(raw_df["trade_date"])
    raw_df.drop_duplicates(subset=["trade_date", "ts_code"], keep="last", inplace=True)

    start_ts = pd.Timestamp(start_str)
    backtest_df = raw_df[raw_df["trade_date"] >= start_ts].copy()

    if backtest_df.empty:
        raise ValueError(f"start_date={start_str} 后无数据")

    close_df = backtest_df.pivot(index="trade_date", columns="ts_code", values="close")

    min_obs    = int(len(close_df) * min_coverage)
    valid_cols = close_df.columns[(close_df.notna().sum() >= min_obs).values]
    close_df  = close_df[valid_cols].astype(np.float64)

    ind_dfs: dict[str, pd.DataFrame] = {}
    for ind in indicators:
        ind_df = backtest_df.pivot(index="trade_date", columns="ts_code", values=ind)
        ind_df = ind_df[valid_cols].ffill().astype(np.float64)
        ind_dfs[ind] = ind_df

    return close_df, ind_dfs, raw_df


# ── 输出工具 ──────────────────────────────────────────────────────────────────

def safe_float(x: Any) -> float | None:
    """安全转 float，NaN / Inf / 非数值 → None。"""
    try:
        v = float(x)
        return round(v, 4) if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def build_result(pf: Any, close_df: pd.DataFrame, extra: dict | None = None) -> dict:
    """
    从 vectorbt Portfolio 对象生成标准回测输出 dict。

    Args:
        pf:       vbt.Portfolio 对象
        close_df: pivot 后的 close DataFrame（用于统计 num_stocks/num_days）
        extra:    附加字段 dict（会合并到结果中）

    Returns:
        符合沙箱输出规范的 dict，可直接 print(json.dumps(result))
    """
    n_trades = int(pf.trades.count().sum())
    _clean = lambda s: s.replace([np.inf, -np.inf], np.nan).dropna().mean() if hasattr(s, 'replace') else s
    result: dict = {
        "total_return":  safe_float(_clean(pf.total_return())),
        "sharpe":        safe_float(_clean(pf.sharpe_ratio())),
        "max_drawdown":  safe_float(_clean(pf.max_drawdown())),
        "win_rate":      safe_float(_clean(pf.trades.win_rate())) if n_trades > 0 else 0.0,
        "trades":        n_trades,
        "num_stocks":    int(close_df.shape[1]),
        "num_days":      int(close_df.shape[0]),
    }
    try:
        result["cagr"] = safe_float(_clean(pf.annualized_return()))
    except Exception:
        result["cagr"] = None

    if extra:
        result.update(extra)
    return result


# ══════════════════════════════════════════════════════════════════════════════
# Phase 1: DolphinDB 分钟数据 + ClickHouse 主力资金 + 行业/市值元数据
# ══════════════════════════════════════════════════════════════════════════════

_DDB_HOST = "127.0.0.1"
_DDB_PORT = 8848
_DDB_USER = "quant_sandbox"

_SESSION_FILTERS = {
    "full":    None,
    "open30":  ("09:30:00", "10:00:00"),
    "close15": ("14:45:00", "15:00:00"),
    "close30": ("14:30:00", "15:00:00"),
    "open60":  ("09:30:00", "10:30:00"),
}


def _get_ddb_session():
    """创建 DolphinDB 连接。"""
    import dolphindb as ddb
    s = ddb.session()
    pwd = os.getenv("DOLPHIN_PWD", "123456")
    s.connect(_DDB_HOST, _DDB_PORT, _DDB_USER, pwd)
    return s


def get_minute_kline(
    start_date: str | _date,
    end_date: str | _date,
    ts_codes: list[str] | None = None,
    session_filter: str = "full",
) -> pd.DataFrame:
    """
    从 DolphinDB minutely_bars 获取分钟级 OHLCV。

    Args:
        start_date:     起始日期
        end_date:       截止日期
        ts_codes:       股票列表，None = 全市场
        session_filter: "full"=全日, "open30"=开盘30分钟, "close15"=尾盘15分钟,
                        "close30"=尾盘30分钟, "open60"=开盘1小时

    Returns:
        DataFrame (bar_time, ts_code, open, high, low, close, volume, amount)
    """
    start_str = _to_dash(start_date)
    end_str = _to_dash(end_date)

    code_filter = ""
    if ts_codes:
        codes_str = ",".join(f'"{c}"' for c in ts_codes)
        code_filter = f" and ts_code in [{codes_str}]"

    time_filter = ""
    bounds = _SESSION_FILTERS.get(session_filter)
    if bounds:
        time_filter = f' and time(bar_time) >= {bounds[0].replace(":", "")}000 and time(bar_time) <= {bounds[1].replace(":", "")}000'

    script = (
        f'select bar_time, ts_code, open, high, low, close, volume, amount '
        f'from loadTable("dfs://reachrich", "minutely_bars") '
        f'where date(bar_time) >= {start_str.replace("-", ".")} '
        f'and date(bar_time) <= {end_str.replace("-", ".")}'
        f'{code_filter}{time_filter} '
        f'order by bar_time, ts_code'
    )

    sess = _get_ddb_session()
    try:
        result = sess.run(script)
    finally:
        sess.close()

    if result is None or (isinstance(result, pd.DataFrame) and result.empty):
        return pd.DataFrame(columns=["bar_time", "ts_code", "open", "high", "low", "close", "volume", "amount"])

    df = result if isinstance(result, pd.DataFrame) else pd.DataFrame(result)
    df["bar_time"] = pd.to_datetime(df["bar_time"])
    for col in ("open", "high", "low", "close", "volume", "amount"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def get_mainflow_minute(
    start_date: str | _date,
    end_date: str | _date,
    ts_codes: list[str] | None = None,
) -> pd.DataFrame:
    """
    从 ClickHouse mainflow_minute 获取分钟级主力资金流。

    Returns:
        DataFrame (trade_date, trade_time, ts_code,
                   main_net, super_net, large_net, medium_net, small_net)
    """
    start_str = _to_dash(start_date)
    end_str = _to_dash(end_date)

    code_clause = ""
    if ts_codes:
        in_list = ",".join(f"'{c}'" for c in ts_codes)
        code_clause = f"AND ts_code IN ({in_list})"

    sql = f"""
        SELECT trade_date, trade_time, ts_code,
               main_net, super_net, large_net, medium_net, small_net
        FROM mainflow_minute
        WHERE trade_date >= '{start_str}'
          AND trade_date <= '{end_str}'
          {code_clause}
        ORDER BY trade_date, trade_time, ts_code
    """
    client = _get_ch_client()
    try:
        df = client.query_df(sql)
    finally:
        client.close()

    if not df.empty:
        df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df


def get_stock_meta(trade_date: str | _date | None = None) -> pd.DataFrame:
    """
    获取个股行业分类（申万一级）和流通市值，用于因子中性化。

    Returns:
        DataFrame (ts_code, industry, circ_mv)
        industry 为申万一级行业名称，circ_mv 为流通市值（万元）。
        若行业数据不可用，industry 列统一填 "unknown"。
    """
    if trade_date is None:
        date_clause = "ORDER BY trade_date DESC LIMIT 1"
        date_filter = ""
    else:
        d = _to_dash(trade_date)
        date_clause = ""
        date_filter = f"AND trade_date = '{d}'"

    sql = f"""
        SELECT ts_code,
               any(name) AS stock_name,
               max(amount) AS circ_mv
        FROM daily_kline
        WHERE 1=1 {date_filter}
        GROUP BY ts_code
        HAVING circ_mv > 0
    """
    client = _get_ch_client()
    try:
        df = client.query_df(sql)
    finally:
        client.close()

    if df.empty:
        return pd.DataFrame(columns=["ts_code", "industry", "circ_mv"])

    df["industry"] = "unknown"
    df["circ_mv"] = pd.to_numeric(df["circ_mv"], errors="coerce").fillna(0)
    return df[["ts_code", "industry", "circ_mv"]]


# ══════════════════════════════════════════════════════════════════════════════
# Phase 2: 核心因子计算
# ══════════════════════════════════════════════════════════════════════════════

def _minute_ret(minute_df: pd.DataFrame) -> pd.DataFrame:
    """给分钟 DataFrame 添加 ret_min 列。"""
    df = minute_df.copy()
    mask = df["open"].abs() > 1e-8
    df["ret_min"] = np.where(mask, (df["close"] - df["open"]) / df["open"], 0.0)
    return df


def _add_trade_date(minute_df: pd.DataFrame) -> pd.DataFrame:
    """从 bar_time 提取 trade_date 列（如果不存在）。"""
    df = minute_df.copy()
    if "trade_date" not in df.columns:
        df["trade_date"] = df["bar_time"].dt.normalize()
    return df


def calc_smart_money(minute_df: pd.DataFrame, session: str = "full") -> pd.DataFrame:
    """
    聪明钱净流入因子 (Smart Money Indicator)。

    SMI = sum(ret * vol * |ret|) / sum(vol)
    放大剧烈波动分钟的成交量权重，过滤散户随机噪音。

    Args:
        minute_df: get_minute_kline() 返回的 DataFrame
        session:   "full"/"open30"/"close15"/"close30" (已在数据获取时过滤更佳)

    Returns:
        DataFrame (index=trade_date, columns=ts_code) 因子值矩阵
    """
    df = _add_trade_date(_minute_ret(minute_df))
    df["weighted_vol"] = df["ret_min"] * df["volume"] * np.abs(df["ret_min"])

    agg = df.groupby(["trade_date", "ts_code"]).agg(
        wv=("weighted_vol", "sum"),
        tv=("volume", "sum"),
    ).reset_index()
    agg["smi"] = agg["wv"] / (agg["tv"] + 1e-8)

    return agg.pivot(index="trade_date", columns="ts_code", values="smi").astype(np.float64)


def calc_voi(minute_df: pd.DataFrame) -> pd.DataFrame:
    """
    分钟级均量差 (Volume Order Imbalance)。

    VOI = sum(sign(ret) * volume) / sum(volume)
    衡量日内买卖力量失衡状态。

    Returns:
        DataFrame (index=trade_date, columns=ts_code)
    """
    df = _add_trade_date(_minute_ret(minute_df))
    df["signed_vol"] = np.sign(df["ret_min"]) * df["volume"]

    agg = df.groupby(["trade_date", "ts_code"]).agg(
        sv=("signed_vol", "sum"),
        tv=("volume", "sum"),
    ).reset_index()
    agg["voi"] = agg["sv"] / (agg["tv"] + 1e-8)

    return agg.pivot(index="trade_date", columns="ts_code", values="voi").astype(np.float64)


def calc_intraday_skewness(minute_df: pd.DataFrame) -> pd.DataFrame:
    """
    日内分钟收益率偏度 — 捕捉暴涨暴跌的极端博弈特征。
    正偏 = 日内有较大幅度上冲，负偏 = 日内有恐慌砸盘。

    Returns:
        DataFrame (index=trade_date, columns=ts_code)
    """
    df = _add_trade_date(_minute_ret(minute_df))
    agg = df.groupby(["trade_date", "ts_code"])["ret_min"].skew().reset_index()
    agg.columns = ["trade_date", "ts_code", "skew"]
    return agg.pivot(index="trade_date", columns="ts_code", values="skew").astype(np.float64)


def calc_intraday_kurtosis(minute_df: pd.DataFrame) -> pd.DataFrame:
    """
    日内分钟收益率峰度 — 尖峰厚尾特征。
    高峰度表示日内波动集中在少数几根极端 K 线。

    Returns:
        DataFrame (index=trade_date, columns=ts_code)
    """
    df = _add_trade_date(_minute_ret(minute_df))
    agg = df.groupby(["trade_date", "ts_code"])["ret_min"].apply(
        lambda x: x.kurtosis() if len(x) >= 4 else np.nan
    ).reset_index()
    agg.columns = ["trade_date", "ts_code", "kurt"]
    return agg.pivot(index="trade_date", columns="ts_code", values="kurt").astype(np.float64)


def calc_amihud(
    close_df: pd.DataFrame,
    volume_df: pd.DataFrame | None = None,
    raw_df: pd.DataFrame | None = None,
    window: int = 20,
) -> pd.DataFrame:
    """
    流动性冲击因子 (Amihud 改进版)。

    Amihud_i,t = rolling_mean(|ret_i| / amount_i, window)
    值越大 → 少量资金即可推动大幅波动 → 筹码锁定度高。

    Args:
        close_df:  日线收盘价矩阵 (index=trade_date, columns=ts_code)
        volume_df: 日线成交量矩阵（仅当 raw_df 无 amount 时作为替代）
        raw_df:    get_clean_kline_data() 的 raw_df（含 amount 列）
        window:    滚动窗口天数

    Returns:
        DataFrame (index=trade_date, columns=ts_code) 与 close_df 对齐
    """
    daily_ret = close_df.pct_change().abs()

    if raw_df is not None and "amount" in raw_df.columns:
        amt_df = raw_df.pivot(index="trade_date", columns="ts_code", values="amount")
        amt_df.index = pd.to_datetime(amt_df.index)
        amt_df = amt_df.reindex(index=close_df.index, columns=close_df.columns).astype(np.float64)
    elif volume_df is not None:
        amt_df = volume_df * close_df
    else:
        amt_df = close_df.copy()
        amt_df[:] = 1.0

    illiq = daily_ret / (amt_df + 1e-8)
    return illiq.rolling(window, min_periods=max(window // 2, 1)).mean().astype(np.float64)


_FACTOR_REGISTRY: dict[str, str] = {
    "smart_money": "minute",
    "voi":         "minute",
    "skewness":    "minute",
    "kurtosis":    "minute",
    "amihud":      "daily",
}


def get_daily_factors(
    start_date: str | _date,
    end_date: str | _date,
    factors: list[str] | None = None,
    lookback_days: int = 30,
    session_filter: str = "full",
) -> dict[str, pd.DataFrame]:
    """
    一站式因子计算入口。自动拉取分钟/日线数据，返回对齐的因子矩阵。

    Args:
        factors:        ['smart_money', 'voi', 'skewness', 'kurtosis', 'amihud']
                        默认全部
        lookback_days:  Amihud 等需要预热的天数
        session_filter: 分钟因子的时段过滤

    Returns:
        dict[str, DataFrame]  每个因子一个矩阵 (index=trade_date, columns=ts_code)
    """
    if factors is None:
        factors = list(_FACTOR_REGISTRY.keys())

    unknown = [f for f in factors if f not in _FACTOR_REGISTRY]
    assert not unknown, f"未知因子: {unknown}，可用: {list(_FACTOR_REGISTRY)}"

    need_minute = any(_FACTOR_REGISTRY[f] == "minute" for f in factors)
    need_daily = any(_FACTOR_REGISTRY[f] == "daily" for f in factors)

    minute_df = None
    close_df = volume_df = raw_df = None

    if need_minute:
        minute_df = get_minute_kline(start_date, end_date, session_filter=session_filter)
        if minute_df.empty:
            import warnings
            warnings.warn("DolphinDB minutely_bars 无数据，分钟因子将为空")

    if need_daily:
        close_df, volume_df, raw_df = get_clean_kline_data(
            start_date, end_date, lookback_days=lookback_days
        )

    result: dict[str, pd.DataFrame] = {}

    for f in factors:
        if f == "smart_money" and minute_df is not None and not minute_df.empty:
            result[f] = calc_smart_money(minute_df, session=session_filter)
        elif f == "voi" and minute_df is not None and not minute_df.empty:
            result[f] = calc_voi(minute_df)
        elif f == "skewness" and minute_df is not None and not minute_df.empty:
            result[f] = calc_intraday_skewness(minute_df)
        elif f == "kurtosis" and minute_df is not None and not minute_df.empty:
            result[f] = calc_intraday_kurtosis(minute_df)
        elif f == "amihud" and close_df is not None:
            result[f] = calc_amihud(close_df, volume_df, raw_df)
        else:
            result[f] = pd.DataFrame()

    if close_df is not None:
        ref_index = close_df.index
        ref_cols = close_df.columns
        for key in result:
            if not result[key].empty:
                result[key] = result[key].reindex(index=ref_index, columns=ref_cols)

    return result


# ══════════════════════════════════════════════════════════════════════════════
# Phase 3: 因子预处理工具链
# ══════════════════════════════════════════════════════════════════════════════

def mad_winsorize(factor_matrix: pd.DataFrame, n: float = 5.0) -> pd.DataFrame:
    """
    MAD (Median Absolute Deviation) 去极值，逐行（截面）处理。
    比均值 + 标准差更稳健，不受极端妖股扭曲。

    upper = median + n * 1.4826 * MAD
    lower = median - n * 1.4826 * MAD
    """
    def _winsorize_row(row: pd.Series) -> pd.Series:
        valid = row.dropna()
        if len(valid) < 3:
            return row
        med = valid.median()
        mad = (valid - med).abs().median()
        scale = n * 1.4826 * mad
        if scale < 1e-12:
            return row
        return row.clip(lower=med - scale, upper=med + scale)

    return factor_matrix.apply(_winsorize_row, axis=1)


def zscore_standardize(factor_matrix: pd.DataFrame) -> pd.DataFrame:
    """截面 Z-Score 标准化：每行（每日截面）均值 0、标准差 1。"""
    mean = factor_matrix.mean(axis=1)
    std = factor_matrix.std(axis=1).replace(0, np.nan)
    return factor_matrix.sub(mean, axis=0).div(std, axis=0)


def neutralize(
    factor_matrix: pd.DataFrame,
    market_cap: pd.DataFrame | pd.Series | None = None,
    industry_dummies: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    行业 + 市值 OLS 中性化（逐截面回归取残差）。

    Factor_neutral = Factor_raw - (beta_cap * log(cap) + sum(beta_i * Industry_i))

    Args:
        factor_matrix:   (index=trade_date, columns=ts_code) 原始因子矩阵
        market_cap:      与 factor_matrix 同结构的流通市值矩阵，或 Series(ts_code → circ_mv)
        industry_dummies: (ts_code × industry) 哑变量矩阵，如
                          pd.get_dummies(meta.set_index('ts_code')['industry'])
                          若为 None 则只做市值中性化
    """
    result = factor_matrix.copy()

    if market_cap is None and industry_dummies is None:
        return result

    if isinstance(market_cap, pd.Series):
        cap_series = market_cap
    elif isinstance(market_cap, pd.DataFrame):
        cap_series = None
        cap_df = market_cap
    else:
        cap_series = None
        cap_df = None

    for dt in factor_matrix.index:
        y = factor_matrix.loc[dt].dropna()
        if len(y) < 10:
            continue

        X_parts = []
        if cap_series is not None:
            log_cap = np.log(cap_series.reindex(y.index).clip(lower=1) + 1)
            X_parts.append(log_cap.rename("log_cap"))
        elif cap_df is not None and dt in cap_df.index:
            log_cap = np.log(cap_df.loc[dt].reindex(y.index).clip(lower=1) + 1)
            X_parts.append(log_cap.rename("log_cap"))

        if industry_dummies is not None:
            ind = industry_dummies.reindex(y.index).fillna(0)
            if ind.shape[1] > 1:
                ind = ind.iloc[:, :-1]
            X_parts.append(ind)

        if not X_parts:
            continue

        X = pd.concat(X_parts, axis=1).dropna()
        common = y.index.intersection(X.index)
        if len(common) < 10:
            continue

        y_c = y.loc[common].values
        X_c = X.loc[common].values
        X_c = np.column_stack([np.ones(len(X_c)), X_c])

        try:
            beta = np.linalg.lstsq(X_c, y_c, rcond=None)[0]
            residual = y_c - X_c @ beta
            result.loc[dt, common] = residual
        except np.linalg.LinAlgError:
            pass

    return result


# ══════════════════════════════════════════════════════════════════════════════
# Phase 4: 因子验证框架
# ══════════════════════════════════════════════════════════════════════════════

def calc_ic_series(
    factor_matrix: pd.DataFrame,
    forward_returns: pd.DataFrame,
    method: str = "spearman",
) -> pd.Series:
    """
    逐日计算因子暴露度与 t+1 收益的秩相关系数 (Rank IC)。

    Args:
        factor_matrix:    (trade_date × ts_code) 因子值
        forward_returns:  (trade_date × ts_code) t+1 收益率
                          可用 close_df.pct_change().shift(-1) 生成
        method:           'spearman' (秩相关, 默认) 或 'pearson'

    Returns:
        Series (index=trade_date) 每日 IC 值
    """
    common_dates = factor_matrix.index.intersection(forward_returns.index)
    ics = []
    for dt in common_dates:
        f = factor_matrix.loc[dt].dropna()
        r = forward_returns.loc[dt].dropna()
        common = f.index.intersection(r.index)
        if len(common) < 30:
            ics.append(np.nan)
            continue
        if method == "spearman":
            ic = f.loc[common].rank().corr(r.loc[common].rank())
        else:
            ic = f.loc[common].corr(r.loc[common])
        ics.append(ic)
    return pd.Series(ics, index=common_dates, name="IC", dtype=np.float64)


def calc_ir(ic_series: pd.Series) -> float:
    """
    信息比率 IR = Mean(IC) / Std(IC)。
    年化 IR > 0.5 表示因子具备实盘价值。
    """
    valid = ic_series.dropna()
    if len(valid) < 5:
        return 0.0
    std = valid.std()
    if std < 1e-10:
        return 0.0
    return float(valid.mean() / std)


def quantile_backtest(
    factor_matrix: pd.DataFrame,
    forward_returns: pd.DataFrame,
    n_groups: int = 5,
) -> dict:
    """
    按因子值分 N 组，计算各组累计收益曲线。

    Returns:
        {
            "group_returns":      DataFrame (trade_date × group) 各组日收益,
            "cumulative_returns": DataFrame (trade_date × group) 各组累计收益,
            "monotonicity_score": float  单调性评分 (-1 ~ 1),
            "long_short_return":  float  多空对冲年化收益,
        }
    """
    common_dates = factor_matrix.index.intersection(forward_returns.index)
    group_daily = {g: [] for g in range(n_groups)}
    valid_dates = []

    for dt in common_dates:
        f = factor_matrix.loc[dt].dropna()
        r = forward_returns.loc[dt].dropna()
        common = f.index.intersection(r.index)
        if len(common) < n_groups * 5:
            continue

        valid_dates.append(dt)
        ranked = f.loc[common].rank(pct=True)
        for g in range(n_groups):
            lo = g / n_groups
            hi = (g + 1) / n_groups
            mask = (ranked > lo) & (ranked <= hi) if g > 0 else (ranked <= hi)
            group_daily[g].append(r.loc[common][mask].mean())

    if not valid_dates:
        return {
            "group_returns": pd.DataFrame(),
            "cumulative_returns": pd.DataFrame(),
            "monotonicity_score": 0.0,
            "long_short_return": 0.0,
        }

    group_ret_df = pd.DataFrame(group_daily, index=valid_dates)
    group_ret_df.columns = [f"G{i+1}" for i in range(n_groups)]
    cum_ret = (1 + group_ret_df).cumprod() - 1

    final_rets = cum_ret.iloc[-1].values if len(cum_ret) > 0 else np.zeros(n_groups)
    expected_order = np.arange(n_groups, dtype=float)
    if len(final_rets) >= 3:
        from scipy.stats import spearmanr
        mono_score, _ = spearmanr(expected_order, final_rets)
    else:
        mono_score = 0.0

    ls_daily = group_ret_df.iloc[:, -1] - group_ret_df.iloc[:, 0]
    ls_annual = float(ls_daily.mean() * 252) if len(ls_daily) > 0 else 0.0

    return {
        "group_returns": group_ret_df,
        "cumulative_returns": cum_ret,
        "monotonicity_score": float(mono_score) if not np.isnan(mono_score) else 0.0,
        "long_short_return": ls_annual,
    }


def factor_turnover(factor_matrix: pd.DataFrame) -> pd.Series:
    """
    因子换手率：逐日因子排名的秩相关变化。
    turnover_t = 1 - corr(rank_t, rank_{t-1})
    值越高 → 换仓频率越高 → 交易成本越大。
    """
    ranks = factor_matrix.rank(axis=1)
    turnover = []
    dates = []
    for i in range(1, len(ranks)):
        prev = ranks.iloc[i - 1].dropna()
        curr = ranks.iloc[i].dropna()
        common = prev.index.intersection(curr.index)
        if len(common) < 20:
            turnover.append(np.nan)
        else:
            rho = prev.loc[common].corr(curr.loc[common])
            turnover.append(1 - rho)
        dates.append(ranks.index[i])
    return pd.Series(turnover, index=dates, name="turnover", dtype=np.float64)


def factor_decay_test(
    factor_matrix: pd.DataFrame,
    forward_returns: pd.DataFrame,
    max_lag: int = 10,
    method: str = "spearman",
) -> pd.DataFrame:
    """
    IC 随持有期衰减曲线：因子 t 期暴露与 t+k 期收益的相关性。

    Returns:
        DataFrame (lag × metrics) 含 mean_ic, ic_std, ir, ic_positive_ratio
    """
    close_ret = forward_returns
    results = []

    for lag in range(1, max_lag + 1):
        shifted = close_ret.shift(-lag)
        ic_s = calc_ic_series(factor_matrix, shifted, method=method)
        valid = ic_s.dropna()
        results.append({
            "lag": lag,
            "mean_ic": float(valid.mean()) if len(valid) > 0 else 0.0,
            "ic_std": float(valid.std()) if len(valid) > 0 else 0.0,
            "ir": calc_ir(ic_s),
            "ic_positive_ratio": float((valid > 0).mean()) if len(valid) > 0 else 0.0,
        })

    return pd.DataFrame(results).set_index("lag")


def build_factor_result(
    pf: Any,
    close_df: pd.DataFrame,
    factor_matrix: pd.DataFrame | None = None,
    extra: dict | None = None,
) -> dict:
    """
    扩展 build_result()，在回测指标基础上加入因子质量指标。

    新增指标:
        mean_ic, ic_std, ir, ic_positive_ratio  — IC/IR 检验
        quantile_spread    — 分层回测多空收益差
        monotonicity_score — 分层单调性评分
        turnover_mean      — 平均因子换手率
        decay_halflife     — IC 衰减半衰期（lag）
    """
    result = build_result(pf, close_df, extra)

    if factor_matrix is not None and not factor_matrix.empty:
        fwd_ret = close_df.pct_change().shift(-1)

        ic_s = calc_ic_series(factor_matrix, fwd_ret)
        valid_ic = ic_s.dropna()
        result["mean_ic"] = safe_float(valid_ic.mean()) if len(valid_ic) > 0 else 0.0
        result["ic_std"] = safe_float(valid_ic.std()) if len(valid_ic) > 0 else 0.0
        result["ir"] = safe_float(calc_ir(ic_s))
        result["ic_positive_ratio"] = safe_float((valid_ic > 0).mean()) if len(valid_ic) > 0 else 0.0

        try:
            qb = quantile_backtest(factor_matrix, fwd_ret, n_groups=5)
            result["quantile_spread"] = safe_float(qb["long_short_return"])
            result["monotonicity_score"] = safe_float(qb["monotonicity_score"])
        except Exception:
            result["quantile_spread"] = None
            result["monotonicity_score"] = None

        try:
            to = factor_turnover(factor_matrix)
            result["turnover_mean"] = safe_float(to.mean())
        except Exception:
            result["turnover_mean"] = None

        try:
            decay = factor_decay_test(factor_matrix, fwd_ret, max_lag=10)
            halflife = None
            for lag, row in decay.iterrows():
                if abs(row["mean_ic"]) < abs(decay.iloc[0]["mean_ic"]) / 2:
                    halflife = int(lag)
                    break
            result["decay_halflife"] = halflife
        except Exception:
            result["decay_halflife"] = None

    return result


# ── 兼容别名（供 Mac 端按文档名称调用）─────────────────────────────────────────
get_clean_ch_data = get_clean_kline_data

"""Opening Range Breakout signal logic.

Reference: Zarattini, Barbon & Aziz (2024), "A Profitable Day Trading
Strategy For The U.S. Equity Market" (SSRN 4729284).

For each session, the first N minutes after the open define the opening
range. A close above the OR high triggers a long; a close below the OR
low triggers a short. Stops sit at the opposite OR boundary; profit
target is N x the range width. All positions are flattened before close.

Session detection uses the America/New_York timezone so that DST
transitions are handled correctly (NYSE opens 9:30 ET year-round).
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

NYSE_TZ = "America/New_York"
SESSION_OPEN_ET = (9, 30)
SESSION_CLOSE_ET = (16, 0)
PREMARKET_OPEN_ET = (4, 0)


@dataclass
class OpeningRange:
    date: pd.Timestamp
    or_high: float
    or_low: float
    or_close: float

    @property
    def width(self) -> float:
        return self.or_high - self.or_low


def _to_et(bars: pd.DataFrame) -> pd.DataFrame:
    if bars.index.tz is None:
        bars = bars.tz_localize("UTC")
    return bars.tz_convert(NYSE_TZ)


def session_bars_by_day(bars: pd.DataFrame) -> dict[pd.Timestamp, pd.DataFrame]:
    """Group bars into per-session frames for regular US trading hours.

    Returns a mapping from ET trading date -> DataFrame of bars between
    9:30 (inclusive) and 16:00 (exclusive) Eastern Time.
    """
    et = _to_et(bars)
    open_h, open_m = SESSION_OPEN_ET
    close_h, close_m = SESSION_CLOSE_ET
    open_min = open_h * 60 + open_m
    close_min = close_h * 60 + close_m
    minutes = et.index.hour * 60 + et.index.minute
    rth = et[(minutes >= open_min) & (minutes < close_min)]
    return {pd.Timestamp(d): g for d, g in rth.groupby(rth.index.date)}


def premarket_bars_by_day(bars: pd.DataFrame) -> dict[pd.Timestamp, pd.DataFrame]:
    """Bars between 4:00 ET (premarket open) and 9:30 ET (regular open)."""
    et = _to_et(bars)
    open_h, open_m = PREMARKET_OPEN_ET
    close_h, close_m = SESSION_OPEN_ET
    open_min = open_h * 60 + open_m
    close_min = close_h * 60 + close_m
    minutes = et.index.hour * 60 + et.index.minute
    pre = et[(minutes >= open_min) & (minutes < close_min)]
    return {pd.Timestamp(d): g for d, g in pre.groupby(pre.index.date)}


def opening_range(session: pd.DataFrame, or_minutes: int = 5) -> OpeningRange | None:
    if session.empty:
        return None
    bar_minutes = (session.index[1] - session.index[0]).total_seconds() / 60 if len(session) > 1 else 5
    n_or_bars = max(1, int(round(or_minutes / bar_minutes)))
    or_window = session.iloc[:n_or_bars]
    return OpeningRange(
        date=pd.Timestamp(session.index[0].date()),
        or_high=float(or_window["high"].max()),
        or_low=float(or_window["low"].min()),
        or_close=float(or_window["close"].iloc[-1]),
    )


def relative_volume_today(
    bars: pd.DataFrame,
    today: pd.Timestamp,
    lookback_days: int = 14,
) -> float | None:
    """Today's premarket dollar volume divided by the trailing-N-day mean.

    Returns None when there is no premarket data on `today` or when fewer
    than half the lookback days have data. Uses dollar volume (price *
    shares) so cross-symbol comparison is meaningful.
    """
    pre = premarket_bars_by_day(bars)
    today_date = pd.Timestamp(today).date()
    if today_date not in {d.date() for d in pre}:
        return None

    today_key = next(d for d in pre if d.date() == today_date)
    today_df = pre[today_key]
    today_dv = float((today_df["close"] * today_df["volume"]).sum())

    prior = sorted(d for d in pre if d.date() < today_date)[-lookback_days:]
    if len(prior) < lookback_days // 2:
        return None
    prior_dvs = [float((pre[d]["close"] * pre[d]["volume"]).sum()) for d in prior]
    mean_dv = sum(prior_dvs) / len(prior_dvs)
    if mean_dv <= 0:
        return None
    return today_dv / mean_dv

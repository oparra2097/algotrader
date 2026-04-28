"""Opening Range Breakout signal logic.

Reference: Zarattini, Barbon & Aziz (2024), "A Profitable Day Trading
Strategy For The U.S. Equity Market" (SSRN 4729284).

For each session, the first N minutes after the open define the opening
range. A close above the OR high triggers a long; a close below the OR
low triggers a short. Stops sit at the opposite OR boundary; profit
target is N x the range width. All positions are flattened before close.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

# regular US session in UTC: 14:30 - 21:00 (no DST math needed because
# Alpaca returns timestamps in UTC and exchange hours align by minute).
SESSION_OPEN_UTC = (14, 30)
SESSION_CLOSE_UTC = (21, 0)


@dataclass
class OpeningRange:
    date: pd.Timestamp
    or_high: float
    or_low: float
    or_close: float

    @property
    def width(self) -> float:
        return self.or_high - self.or_low


def session_bars_by_day(bars: pd.DataFrame) -> dict[pd.Timestamp, pd.DataFrame]:
    """Group 5-min bars into per-session frames, regular hours only."""
    if bars.index.tz is None:
        bars = bars.tz_localize("UTC")
    else:
        bars = bars.tz_convert("UTC")
    open_h, open_m = SESSION_OPEN_UTC
    close_h, close_m = SESSION_CLOSE_UTC
    open_minutes = open_h * 60 + open_m
    close_minutes = close_h * 60 + close_m
    minutes = bars.index.hour * 60 + bars.index.minute
    rth = bars[(minutes >= open_minutes) & (minutes < close_minutes)]
    return {d: g for d, g in rth.groupby(rth.index.date)}


def opening_range(session: pd.DataFrame, or_minutes: int = 5) -> OpeningRange | None:
    if session.empty:
        return None
    open_bar = session.iloc[0]
    bar_minutes = (session.index[1] - session.index[0]).total_seconds() / 60 if len(session) > 1 else 5
    n_or_bars = max(1, int(round(or_minutes / bar_minutes)))
    or_window = session.iloc[:n_or_bars]
    return OpeningRange(
        date=pd.Timestamp(session.index[0].date()),
        or_high=float(or_window["high"].max()),
        or_low=float(or_window["low"].min()),
        or_close=float(or_window["close"].iloc[-1]),
    )

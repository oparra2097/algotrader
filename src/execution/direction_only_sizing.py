"""Direction-only sizing: derive stop/target/qty from venue market data.

When a signal source (parramacro) emits a directional view in its own
price frame (e.g., trend-anchored gold at $4727) but the execution venue
trades a different vehicle in a different frame (GLD at ~$220), we
discard the source's absolute levels and compute fresh ones from the
venue's own daily bars: ATR-based stop, R-multiple target, ATR-based
size with a max-notional cap.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pandas as pd


@dataclass
class SizedLevels:
    spot: float
    atr: float
    stop_price: float
    target_price: float
    qty: float
    dollars: float


def _atr(bars: pd.DataFrame, period: int) -> float:
    high_low = bars["high"] - bars["low"]
    high_close = (bars["high"] - bars["close"].shift()).abs()
    low_close = (bars["low"] - bars["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])


def compute_levels(
    symbol: str,
    direction: str,
    equity: float,
    risk_per_trade: float,
    max_position_fraction: float,
    atr_period: int = 14,
    atr_stop_multiplier: float = 2.0,
    target_r_multiple: float = 3.0,
    atr_lookback_days: int = 60,
) -> SizedLevels | None:
    """Return venue-frame stop/target/qty for the given symbol+direction.

    direction: "long" or "short". Returns None when the venue bars can't
    be fetched, when there isn't enough history for the ATR window, or
    when ATR is zero. The caller should treat None as "skip this trade".
    """
    if direction not in ("long", "short"):
        return None

    from src.data.alpaca import fetch_bars

    end = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    start = (datetime.now(timezone.utc) - timedelta(days=atr_lookback_days)).strftime("%Y-%m-%d")

    try:
        bars = fetch_bars(symbol, start, end, timeframe="1Day")
    except Exception:
        return None

    if len(bars) < atr_period + 1:
        return None

    spot = float(bars["close"].iloc[-1])
    atr = _atr(bars, atr_period)
    if atr <= 0 or pd.isna(atr):
        return None

    stop_distance = atr * atr_stop_multiplier

    if direction == "long":
        stop_price = spot - stop_distance
        target_price = spot + stop_distance * target_r_multiple
    else:
        stop_price = spot + stop_distance
        target_price = spot - stop_distance * target_r_multiple

    risk_dollars = equity * risk_per_trade
    qty_for_risk = risk_dollars / stop_distance
    qty_for_max = (equity * max_position_fraction) / spot
    qty = min(qty_for_risk, qty_for_max)

    return SizedLevels(
        spot=spot,
        atr=atr,
        stop_price=round(stop_price, 4),
        target_price=round(target_price, 4),
        qty=round(qty, 6),
        dollars=round(qty * spot, 2),
    )

"""Donchian breakout signal evaluator for live trading.

Stateless: consumes recent OHLCV bars + the current position (long or
flat) and returns a TradeIdea describing what to do on the next bar:
buy, close, or nothing. The caller (the bot runner) supplies position
state from the broker so this module has no I/O.

Rule (matches the validated backtest config in config/strategy.yaml):

  Entry (when flat):
    - latest bar's high >= prior 20-day Donchian high
    - close > 100-day SMA  (trend filter)
    -> long

  Exit (when long):
    - latest bar's low <= prior 10-day Donchian low
    -> close

  Sizing (vol-target):
    - position notional = (vol_target_daily / realized_vol) * equity
    - capped at max_position_fraction * equity
    - stop = close - atr_stop_multiplier * ATR
    - target = close + atr_stop_multiplier * ATR * target_r_multiple
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from src.signals.trade_idea import TradeIdea
from src.strategy.donchian import atr, donchian_signals, realized_vol


def evaluate(
    bars: pd.DataFrame,
    *,
    symbol: str,
    is_long: bool,
    equity: float,
    entry_lookback: int = 20,
    exit_lookback: int = 10,
    atr_period: int = 14,
    atr_stop_multiplier: float = 2.0,
    target_r_multiple: float = 3.0,
    trend_filter_ma: int | None = 100,
    vol_target_daily: float = 0.01,
    vol_lookback: int = 20,
    max_position_fraction: float = 1.0,
    risk_per_trade: float = 0.01,
) -> TradeIdea | None:
    """Evaluate the latest bar against the Donchian rule.

    Returns:
        TradeIdea(side='buy')   when flat and entry conditions met
        TradeIdea(side='close') when long and exit conditions met
        None                    when no action required
    """
    if len(bars) < max(entry_lookback, exit_lookback,
                       atr_period, vol_lookback,
                       trend_filter_ma or 0) + 2:
        return None

    sig = donchian_signals(bars, entry_lookback, exit_lookback, trend_filter_ma)
    sig["atr"] = atr(bars, atr_period)
    sig["vol"] = realized_vol(bars, vol_lookback)

    latest = sig.iloc[-1]
    spot = float(latest["close"])
    ts = datetime.now(timezone.utc)

    # ---- exit (long -> close) ----
    if is_long:
        if pd.isna(latest["exit_low"]):
            return None
        if latest["low"] <= latest["exit_low"]:
            return TradeIdea(
                source=f"donchian_live:{symbol}",
                symbol=symbol,
                side="close",
                timestamp=ts,
                rationale=(
                    f"{symbol} low {latest['low']:.2f} <= "
                    f"prior {exit_lookback}d low {latest['exit_low']:.2f} "
                    f"-> exit long"
                ),
                metadata={
                    "spot": spot,
                    "rule": "donchian_exit",
                    "exit_low": float(latest["exit_low"]),
                },
            )
        return None

    # ---- entry (flat -> buy) ----
    if pd.isna(latest["entry_high"]) or pd.isna(latest["atr"]):
        return None

    trend_ok = True
    if trend_filter_ma is not None and "trend_ma" in sig.columns:
        if pd.isna(latest["trend_ma"]):
            trend_ok = False
        else:
            trend_ok = latest["close"] > latest["trend_ma"]

    if not trend_ok:
        return None

    if latest["high"] < latest["entry_high"]:
        return None

    # Conditions met. Compute size + stop + target in the venue's frame.
    atr_v = float(latest["atr"])
    vol_v = float(latest["vol"]) if not pd.isna(latest["vol"]) else None

    stop_distance = atr_v * atr_stop_multiplier
    if stop_distance <= 0:
        return None
    stop_price = spot - stop_distance
    target_price = spot + stop_distance * target_r_multiple

    return TradeIdea(
        source=f"donchian_live:{symbol}",
        symbol=symbol,
        side="buy",
        timestamp=ts,
        rationale=(
            f"{symbol} high {latest['high']:.2f} >= "
            f"prior {entry_lookback}d high {latest['entry_high']:.2f}; "
            f"close {spot:.2f} > {trend_filter_ma}d MA "
            f"{latest.get('trend_ma', float('nan')):.2f} -> long, "
            f"stop {stop_price:.2f}, target {target_price:.2f} "
            f"(ATR {atr_v:.2f}, vol {vol_v:.4f})"
        ),
        stop_price=round(stop_price, 4),
        target_price=round(target_price, 4),
        metadata={
            "spot": spot,
            "atr": atr_v,
            "vol": vol_v,
            "entry_high": float(latest["entry_high"]),
            "trend_ma": (float(latest["trend_ma"])
                        if "trend_ma" in latest and not pd.isna(latest["trend_ma"])
                        else None),
            "rule": "donchian_breakout",
            "vol_target_daily": vol_target_daily,
            "max_position_fraction": max_position_fraction,
        },
    )

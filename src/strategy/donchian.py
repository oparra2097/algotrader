"""Donchian channel breakout signals, ATR, and realized vol for sizing."""
from __future__ import annotations

import numpy as np
import pandas as pd


def donchian_signals(
    df: pd.DataFrame,
    entry_lookback: int,
    exit_lookback: int,
    trend_filter_ma: int | None = None,
) -> pd.DataFrame:
    out = df.copy()
    # shift(1) ensures the level used today is computed from bars strictly before today
    out["entry_high"] = df["high"].rolling(entry_lookback).max().shift(1)
    out["exit_low"] = df["low"].rolling(exit_lookback).min().shift(1)
    if trend_filter_ma:
        out["trend_ma"] = df["close"].rolling(trend_filter_ma).mean()
    return out


def atr(df: pd.DataFrame, period: int) -> pd.Series:
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def realized_vol(df: pd.DataFrame, period: int) -> pd.Series:
    """Rolling stdev of daily log returns. Used for vol-target sizing."""
    log_ret = np.log(df["close"] / df["close"].shift(1))
    return log_ret.rolling(period).std()

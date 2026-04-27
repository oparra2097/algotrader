#!/usr/bin/env python3
"""Sanity-check the backtest engine on synthetic OHLC.

Real ETH data must be fetched on your local machine (this sandbox blocks
external network calls). This script only confirms the engine plumbing
works and sizing/exits behave on a known series.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import numpy as np
import pandas as pd

from src.backtest.engine import run_backtest


def synthetic_ohlc(n_days: int = 1000, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    # geometric brownian motion w/ a regime shift midway to create a real trend
    mu_a, mu_b = 0.0008, -0.0004
    sigma = 0.04
    rets = np.concatenate([
        rng.normal(mu_a, sigma, n_days // 2),
        rng.normal(mu_b, sigma, n_days - n_days // 2),
    ])
    close = 100 * np.exp(np.cumsum(rets))
    # build OHLC around close
    open_ = np.r_[close[0], close[:-1]]
    intraday = np.abs(rng.normal(0, sigma / 2, n_days)) * close
    high = np.maximum(open_, close) + intraday
    low = np.minimum(open_, close) - intraday
    low = np.maximum(low, 0.01)
    dates = pd.date_range("2018-01-01", periods=n_days, freq="D")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close,
         "volume": rng.uniform(1e6, 1e7, n_days)},
        index=dates,
    )


def main() -> None:
    df = synthetic_ohlc()
    print(f"synthetic bars: {len(df)}  start={df['close'].iloc[0]:.2f}  end={df['close'].iloc[-1]:.2f}")

    common = dict(
        entry_lookback=20,
        exit_lookback=10,
        atr_period=14,
        atr_stop_multiplier=2.0,
        trend_filter_ma=100,
        starting_equity=1000.0,
        max_position_fraction=1.0,
        fee_per_side=0.0025,
        slippage=0.0005,
    )

    for method in ("vol_target", "atr"):
        res = run_backtest(df, sizing_method=method, **common)
        print(f"\n--- {method} sizing ---")
        for k, v in res.stats.items():
            print(f"  {k}: {v}")
        assert res.equity_curve.index.is_monotonic_increasing
        assert (res.equity_curve > 0).all(), f"{method}: equity went non-positive"
        assert res.stats["n_trades"] > 0, f"{method}: no trades produced"

    print("\nOK: both sizing modes ran end-to-end without errors.")


if __name__ == "__main__":
    main()

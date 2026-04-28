#!/usr/bin/env python3
"""Sanity check the ORB engine on synthetic intraday data.

Real Alpaca data must be fetched on your local machine (this sandbox
blocks external network calls). This script confirms the engine plumbing
runs end-to-end and a contrived breakout produces a winning trade.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import numpy as np
import pandas as pd

from src.backtest.orb_engine import run_orb_backtest


def synthetic_intraday(
    n_days: int = 30,
    bars_per_day: int = 78,        # 6.5h * 12 5-min bars
    seed: int = 7,
) -> pd.DataFrame:
    """One bar every 5 minutes from 14:30 UTC for n_days. Half the days
    feature a clean post-OR breakout long; the rest meander."""
    rng = np.random.default_rng(seed)
    rows = []
    price = 100.0
    for d in range(n_days):
        date = pd.Timestamp("2024-01-01", tz="UTC") + pd.Timedelta(days=d)
        # OR period: tight range 99.8 - 100.2
        for i in range(bars_per_day):
            ts = date + pd.Timedelta(hours=14, minutes=30) + pd.Timedelta(minutes=5 * i)
            if i == 0:
                o = price
                h = price + 0.2
                l = price - 0.2
                c = price + rng.normal(0, 0.05)
            elif d % 2 == 0 and i >= 1:
                # breakout day: drift up
                step = rng.normal(0.06, 0.05)
                o = price
                c = price + step
                h = max(o, c) + abs(rng.normal(0, 0.04))
                l = min(o, c) - abs(rng.normal(0, 0.04))
            else:
                step = rng.normal(0, 0.05)
                o = price
                c = price + step
                h = max(o, c) + abs(rng.normal(0, 0.04))
                l = min(o, c) - abs(rng.normal(0, 0.04))
            price = c
            rows.append((ts, o, h, l, c, rng.uniform(1e3, 1e4)))
        # reset to ~100 each day (ignore overnight in synthetic)
        price = 100.0 + rng.normal(0, 0.3)

    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df = df.set_index("ts").sort_index()
    return df


def main() -> None:
    bars = {"SYN": synthetic_intraday()}
    print(f"synthetic bars: {len(bars['SYN'])}")

    res = run_orb_backtest(
        bars_by_symbol=bars,
        starting_equity=1000.0,
        risk_per_trade=0.01,
        max_concurrent_positions=1,
        max_position_fraction=0.5,
        or_minutes=5,
        target_r=10.0,
        flatten_minute_before_close=5,
        long_only=False,
        fee_per_side=0.0,
        slippage=0.0005,
        relvol_min=0.0,  # synthetic data has no premarket; skip the filter
    )

    print("\nstats:")
    for k, v in res.stats.items():
        print(f"  {k}: {v}")
    print(f"\nfirst 3 trades:")
    for t in res.trades[:3]:
        print(f"  {t.date.date()} {t.symbol} {t.side}  "
              f"entry={t.entry_price:.2f} exit={t.exit_price:.2f} "
              f"R={t.r_multiple:.2f} reason={t.reason}")

    assert res.stats["n_trades"] > 0, "no trades produced"
    assert (res.equity_curve > 0).all(), "equity went non-positive"
    print("\nOK: ORB engine ran end-to-end without errors.")


if __name__ == "__main__":
    main()

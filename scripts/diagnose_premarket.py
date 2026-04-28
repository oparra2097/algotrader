#!/usr/bin/env python3
"""For each symbol, print premarket-data viability diagnostics.

A symbol can be invisible to ORB for any of:
  - no premarket bars at all (illiquid)
  - too-short history relative to the 14-day relvol lookback
  - chronic low premarket volume so relvol never crosses the threshold
  - data series starting after the backtest start (recent IPO)

This script prints the numbers so we can see which case applies.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import pandas as pd

from src.data.alpaca import fetch_universe
from src.strategy.orb import (
    premarket_bars_by_day,
    relative_volume_today,
    session_bars_by_day,
)

UNIVERSE = [
    "TSLA", "NVDA", "AMD", "TSEM", "COIN", "MSTR", "PLTR", "MARA",
    "RIVN", "SOFI", "AFRM", "GME", "VG",
    "SPY", "QQQ", "AAPL",
]
START = "2024-01-01"


def main() -> None:
    print(f"Fetching {len(UNIVERSE)} symbols  {START} -> today")
    bars = fetch_universe(UNIVERSE, START, None, "5Min")

    print(f"\n{'sym':>6}  {'first_bar':>10}  {'last_bar':>10}  "
          f"{'rth_days':>8}  {'pre_days':>8}  "
          f"{'mean_pre_$vol':>14}  {'ge_1.5x':>7}  {'ge_2.0x':>7}  {'ge_3.0x':>7}")
    print("-" * 100)

    for sym in UNIVERSE:
        if sym not in bars:
            print(f"  {sym:>4}  (no data)")
            continue
        df = bars[sym]
        rth = session_bars_by_day(df)
        pre = premarket_bars_by_day(df)

        if not pre:
            print(f"  {sym:>4}  {df.index[0].date()!s:>10}  {df.index[-1].date()!s:>10}  "
                  f"{len(rth):>8}  {0:>8}  {'-':>14}  {'-':>7}  {'-':>7}  {'-':>7}")
            continue

        pre_dvs = [(d, float((g['close'] * g['volume']).sum())) for d, g in pre.items()]
        mean_dv = sum(dv for _, dv in pre_dvs) / len(pre_dvs)

        # count qualifying days using actual relvol calc
        rth_dates = sorted(rth)
        c15 = c20 = c30 = 0
        for d in rth_dates:
            rv = relative_volume_today(df, d, lookback_days=14)
            if rv is None:
                continue
            if rv >= 1.5: c15 += 1
            if rv >= 2.0: c20 += 1
            if rv >= 3.0: c30 += 1

        print(f"  {sym:>4}  {df.index[0].date()!s:>10}  {df.index[-1].date()!s:>10}  "
              f"{len(rth):>8}  {len(pre):>8}  ${mean_dv:>12,.0f}  "
              f"{c15:>7}  {c20:>7}  {c30:>7}")

    print("""
Read the table:
  rth_days     - count of regular-hours sessions with at least one bar
  pre_days     - count of days with any premarket data (4:00-9:30 ET)
  mean_pre_$vol - average premarket dollar volume across those days
  ge_X.Xx      - days where premarket dollar vol >= X.X x trailing 14-day mean

If pre_days == 0: name has no premarket data on the IEX feed -> ORB cannot trade it
If ge_2.0x is very small: thin premarket means relvol rarely spikes -> few opportunities
If first_bar is recent: IPO; 14-day lookback may not warm up enough early on
""")


if __name__ == "__main__":
    main()

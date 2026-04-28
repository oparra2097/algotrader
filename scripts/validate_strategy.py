#!/usr/bin/env python3
"""Compare backtest variants and print a side-by-side table.

Three checks before we trust the headline result:
  1. vol_target vs ATR sizing on the full window
  2. Full window (2018+) vs recent regime only (2022+)
  3. Donchian parameter sensitivity across reasonable lookbacks
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import pandas as pd

from src.backtest.engine import run_backtest
from src.data.fetch import fetch_ohlcv


BASE = dict(
    entry_lookback=20,
    exit_lookback=10,
    atr_period=14,
    atr_stop_multiplier=2.0,
    trend_filter_ma=100,
    starting_equity=1000.0,
    max_position_fraction=1.0,
    fee_per_side=0.0025,
    slippage=0.0005,
    sizing_method="vol_target",
    vol_target_daily=0.01,
    vol_lookback=20,
    risk_per_trade=0.01,
)


def run(label: str, df: pd.DataFrame, **overrides) -> dict:
    params = {**BASE, **overrides}
    res = run_backtest(df, **params)
    bh_ret = float(df["close"].iloc[-1] / df["close"].iloc[0] - 1)
    return {
        "label": label,
        "cagr": res.stats["cagr"],
        "sharpe": res.stats["sharpe"],
        "max_dd": res.stats["max_drawdown"],
        "n_trades": res.stats["n_trades"],
        "win_rate": res.stats["win_rate"],
        "pf": res.stats["profit_factor"],
        "total_ret": res.stats["total_return"],
        "bh_ret": bh_ret,
    }


def print_table(rows: list[dict], title: str) -> None:
    print(f"\n=== {title} ===")
    headers = ["label", "n_trades", "cagr", "sharpe", "max_dd",
               "win_rate", "pf", "total_ret", "bh_ret"]
    widths = {h: max(len(h), 8) for h in headers}
    widths["label"] = max(max(len(r["label"]) for r in rows), 12)

    print("  ".join(h.rjust(widths[h]) for h in headers))
    print("  ".join("-" * widths[h] for h in headers))

    pct_cols = {"cagr", "max_dd", "win_rate", "total_ret", "bh_ret"}
    for r in rows:
        cells = []
        for h in headers:
            v = r[h]
            if h == "label":
                cells.append(str(v).rjust(widths[h]))
            elif h == "n_trades":
                cells.append(str(v).rjust(widths[h]))
            elif h in pct_cols:
                cells.append(f"{v:.1%}".rjust(widths[h]))
            else:
                pf_inf = isinstance(v, float) and v == float("inf")
                cells.append(("inf" if pf_inf else f"{v:.2f}").rjust(widths[h]))
        print("  ".join(cells))


def main() -> None:
    print("Loading ETH-USD ...")
    df_full = fetch_ohlcv("ETH-USD", "2018-01-01", None)
    print(f"  full window:   {len(df_full)} bars  "
          f"({df_full.index[0].date()} -> {df_full.index[-1].date()})")

    df_recent = df_full[df_full.index >= "2022-01-01"]
    print(f"  recent window: {len(df_recent)} bars  "
          f"({df_recent.index[0].date()} -> {df_recent.index[-1].date()})")

    # Test 1: vol_target vs ATR sizing on full window
    rows1 = [
        run("vol_target", df_full),
        run("atr", df_full, sizing_method="atr"),
    ]
    print_table(rows1, "Test 1: Sizing comparison (full window 2018+)")

    # Test 2: full window vs recent regime
    rows2 = [
        run("full 2018+", df_full),
        run("recent 2022+", df_recent),
    ]
    print_table(rows2, "Test 2: Full vs recent regime (vol_target)")

    # Test 3: Donchian parameter sensitivity
    rows3 = []
    for lb_in, lb_out in [(10, 5), (20, 10), (30, 15), (55, 20)]:
        rows3.append(
            run(f"{lb_in}/{lb_out}", df_full,
                entry_lookback=lb_in, exit_lookback=lb_out)
        )
    print_table(rows3, "Test 3: Donchian parameter sensitivity (full window)")

    print("""
Interpretation guide
  Test 1 - if both sizings show similar CAGR/Sharpe, the edge is in the
           signal, not the sizing. Pick whichever has lower max_dd.
  Test 2 - recent CAGR should still be positive. If recent is deeply
           negative, the 2020-21 bull market did all the work and the
           edge has decayed.
  Test 3 - most lookbacks should show positive, similar-shaped results.
           If only 20/10 wins, that's a red flag for overfitting.
""")


if __name__ == "__main__":
    main()

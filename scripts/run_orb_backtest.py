#!/usr/bin/env python3
"""Run the configured ORB backtest across the universe.

Requires Alpaca paper API keys in .env (copy .env.example -> .env first).
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import yaml

from src.backtest.orb_engine import run_orb_backtest
from src.data.alpaca import fetch_universe


def fmt(key: str, value) -> str:
    if isinstance(value, float):
        if key in ("total_return", "cagr", "max_drawdown", "win_rate"):
            return f"{value:.2%}"
        if key in ("final_equity",):
            return f"${value:,.2f}"
        return f"{value:.2f}"
    return str(value)


def main(config_path: str = "config/orb.yaml") -> None:
    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    s = cfg["strategy"]
    r = cfg["risk"]
    c = cfg["costs"]
    b = cfg["backtest"]
    f = cfg.get("filter", {})

    print(f"Fetching {len(s['universe'])} symbols  {b['start']} -> {b['end'] or 'today'}  ({b['timeframe']})")
    bars = fetch_universe(s["universe"], b["start"], b["end"], b["timeframe"])
    for sym, df in bars.items():
        print(f"  {sym:>6}: {len(df)} bars")
    if not bars:
        print("No data fetched. Check your .env credentials.")
        return

    result = run_orb_backtest(
        bars_by_symbol=bars,
        starting_equity=r["starting_equity"],
        risk_per_trade=r["risk_per_trade"],
        max_concurrent_positions=r["max_concurrent_positions"],
        max_position_fraction=r["max_position_fraction"],
        or_minutes=s["opening_range_minutes"],
        target_r=s["target_r_multiple"],
        flatten_minute_before_close=s["flatten_minute_before_close"],
        long_only=s["long_only"],
        fee_per_side=c["fee_per_side"],
        slippage=c["slippage"],
        relvol_min=f.get("relvol_min", 1.5),
        relvol_lookback_days=f.get("relvol_lookback_days", 14),
        top_n_per_day=f.get("top_n_per_day"),
    )

    print("\n=== ORB Backtest Results ===")
    for k, v in result.stats.items():
        print(f"  {k:>16}: {fmt(k, v)}")

    out_dir = REPO_ROOT / "results"
    out_dir.mkdir(exist_ok=True)
    if result.trades:
        with open(out_dir / "orb_trades.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["symbol", "date", "side", "entry_price", "exit_price",
                        "size", "pnl", "r_multiple", "reason"])
            for t in result.trades:
                w.writerow([
                    t.symbol, t.date.date(), t.side,
                    f"{t.entry_price:.2f}", f"{t.exit_price:.2f}",
                    f"{t.size:.4f}", f"{t.pnl:.2f}",
                    f"{t.r_multiple:.3f}", t.reason,
                ])
        print(f"\ntrades  -> {out_dir / 'orb_trades.csv'}")

    by_symbol: dict[str, list] = {}
    for t in result.trades:
        by_symbol.setdefault(t.symbol, []).append(t)
    if by_symbol:
        print("\nPer-symbol summary:")
        print(f"  {'sym':>6}  {'n':>4}  {'win%':>6}  {'avg_R':>7}  {'pnl':>10}")
        for sym in sorted(by_symbol):
            ts = by_symbol[sym]
            wins = sum(1 for t in ts if t.pnl > 0)
            avg_r = sum(t.r_multiple for t in ts) / len(ts)
            pnl = sum(t.pnl for t in ts)
            print(f"  {sym:>6}  {len(ts):>4}  {wins / len(ts):>5.1%}  {avg_r:>7.2f}  ${pnl:>8.2f}")


if __name__ == "__main__":
    main()

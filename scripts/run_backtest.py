#!/usr/bin/env python3
"""Run the configured Donchian backtest on historical ETH data."""
from __future__ import annotations

import csv
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import yaml

from src.backtest.engine import run_backtest
from src.data.fetch import fetch_ohlcv


def fmt(key: str, value) -> str:
    if isinstance(value, float):
        if key in ("total_return", "cagr", "max_drawdown", "win_rate"):
            return f"{value:.2%}"
        if key in ("final_equity", "avg_win", "avg_loss"):
            return f"${value:,.2f}"
        return f"{value:.2f}"
    return str(value)


def main(config_path: str = "config/strategy.yaml") -> None:
    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    s = cfg["strategy"]
    r = cfg["risk"]
    c = cfg["costs"]
    b = cfg["backtest"]

    print(f"Fetching {s['symbol']} {b['start']} -> {b['end'] or 'today'}")
    df = fetch_ohlcv(s["symbol"], b["start"], b["end"])
    print(f"  loaded {len(df)} bars  ({df.index[0].date()} -> {df.index[-1].date()})")

    result = run_backtest(
        df,
        entry_lookback=s["entry_lookback"],
        exit_lookback=s["exit_lookback"],
        atr_period=s["atr_period"],
        atr_stop_multiplier=s["atr_stop_multiplier"],
        trend_filter_ma=s["trend_filter_ma"],
        starting_equity=r["starting_equity"],
        max_position_fraction=r["max_position_fraction"],
        fee_per_side=c["fee_per_side"],
        slippage=c["slippage"],
        sizing_method=r.get("sizing_method", "vol_target"),
        risk_per_trade=r.get("risk_per_trade", 0.01),
        vol_target_daily=r.get("vol_target_daily", 0.01),
        vol_lookback=r.get("vol_lookback", 20),
    )

    # buy-and-hold benchmark
    bh_return = float(df["close"].iloc[-1] / df["close"].iloc[0] - 1)
    bh_final = r["starting_equity"] * (1 + bh_return)

    print(f"\n=== Backtest Results  ({r.get('sizing_method', 'vol_target')} sizing) ===")
    for k, v in result.stats.items():
        print(f"  {k:>16}: {fmt(k, v)}")
    print(f"\n  buy_and_hold_ret: {bh_return:.2%}   (final ${bh_final:,.2f})")

    out_dir = REPO_ROOT / "results"
    out_dir.mkdir(exist_ok=True)

    # equity + drawdown chart
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True,
                                   gridspec_kw={"height_ratios": [3, 1]})
    ax1.plot(result.equity_curve.index, result.equity_curve.values,
             label="Donchian strategy", color="C0")
    bh_curve = r["starting_equity"] * (df["close"] / df["close"].iloc[0])
    ax1.plot(bh_curve.index, bh_curve.values, label="Buy & hold ETH",
             color="gray", alpha=0.6, linestyle="--")
    ax1.axhline(r["starting_equity"], color="black", linestyle=":", alpha=0.4)
    ax1.set_ylabel("Equity ($)")
    ax1.set_yscale("log")
    ax1.set_title(f"{s['symbol']} Donchian {s['entry_lookback']}/{s['exit_lookback']} - ${r['starting_equity']:.0f} start")
    ax1.legend(loc="upper left")
    ax1.grid(alpha=0.3)

    rolling_max = result.equity_curve.cummax()
    dd = (result.equity_curve / rolling_max - 1) * 100
    ax2.fill_between(dd.index, dd.values, 0, color="red", alpha=0.3)
    ax2.set_ylabel("Drawdown (%)")
    ax2.set_xlabel("Date")
    ax2.grid(alpha=0.3)

    fig.tight_layout()
    fig.savefig(out_dir / "backtest.png", dpi=110)
    print(f"\nchart   -> {out_dir / 'backtest.png'}")

    # trade log
    if result.trades:
        with open(out_dir / "trades.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["entry_date", "exit_date", "entry_price", "exit_price",
                        "size", "pnl", "return_pct", "reason"])
            for t in result.trades:
                w.writerow([
                    t.entry_date.date(), t.exit_date.date(),
                    f"{t.entry_price:.2f}", f"{t.exit_price:.2f}",
                    f"{t.size:.6f}", f"{t.pnl:.2f}",
                    f"{t.return_pct:.4f}", t.reason,
                ])
        print(f"trades  -> {out_dir / 'trades.csv'}")


if __name__ == "__main__":
    main()

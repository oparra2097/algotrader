#!/usr/bin/env python3
"""Run ORB backtest variants and print a comparison table.

Helps decide whether ORB is viable on retail tooling without iterating
on config files manually. Variants are defined in VARIANTS below.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.backtest.orb_engine import run_orb_backtest
from src.data.alpaca import fetch_universe


WIDE_UNIVERSE = [
    "TSLA", "NVDA", "AMD", "TSEM", "COIN", "MSTR", "PLTR", "MARA",
    "RIVN", "SOFI", "AFRM", "GME", "VG",
]
TSLA_ONLY = ["TSLA"]
ETF_UNIVERSE = ["SPY", "QQQ"]

BASE = dict(
    starting_equity=1000.0,
    risk_per_trade=0.01,
    max_concurrent_positions=2,
    max_position_fraction=0.5,
    or_minutes=5,
    flatten_minute_before_close=5,
    long_only=False,
    fee_per_side=0.0,
    slippage=0.0005,
    relvol_lookback_days=14,
    top_n_per_day=None,
)


VARIANTS = [
    # label                      universe        relvol  target_r
    ("wide x 3R x relvol 2.0",   WIDE_UNIVERSE,  2.0,    3.0),
    ("wide x 5R x relvol 2.0",   WIDE_UNIVERSE,  2.0,    5.0),
    ("wide x 3R x relvol 1.5",   WIDE_UNIVERSE,  1.5,    3.0),
    ("wide x 3R x relvol 3.0",   WIDE_UNIVERSE,  3.0,    3.0),
    ("TSLA only x 3R x rel 2.0", TSLA_ONLY,      2.0,    3.0),
    ("TSLA only x 5R x rel 2.0", TSLA_ONLY,      2.0,    5.0),
    ("ETFs x 3R x rel 2.0",      ETF_UNIVERSE,   2.0,    3.0),
]


def fetch_all(universes: list[list[str]]) -> dict[str, dict]:
    needed = sorted({s for u in universes for s in u})
    print(f"Fetching {len(needed)} symbols 2024-01-01 -> today (5Min)")
    return fetch_universe(needed, "2024-01-01", None, "5Min")


def print_table(rows: list[dict]) -> None:
    headers = ["label", "n_trades", "win_rate", "avg_r",
               "total_ret", "max_dd", "sharpe", "pf"]
    widths = {h: max(len(h), 8) for h in headers}
    widths["label"] = max(max(len(r["label"]) for r in rows), 22)

    print()
    print("  ".join(h.rjust(widths[h]) for h in headers))
    print("  ".join("-" * widths[h] for h in headers))

    pct_cols = {"win_rate", "total_ret", "max_dd"}
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
                cells.append(("inf" if v == float("inf") else f"{v:.2f}").rjust(widths[h]))
        print("  ".join(cells))


def main() -> None:
    bars_pool = fetch_all([u for _, u, _, _ in VARIANTS])

    rows = []
    for label, universe, relvol, target in VARIANTS:
        bars = {s: bars_pool[s] for s in universe if s in bars_pool}
        if not bars:
            print(f"  [skip] {label}: no data")
            continue
        res = run_orb_backtest(
            bars_by_symbol=bars,
            target_r=target,
            relvol_min=relvol,
            **BASE,
        )
        s = res.stats
        rows.append({
            "label": label,
            "n_trades": s["n_trades"],
            "win_rate": s["win_rate"],
            "avg_r": s["avg_r"],
            "total_ret": s["total_return"],
            "max_dd": s["max_drawdown"],
            "sharpe": s["sharpe"],
            "pf": s["profit_factor"],
        })
        print(f"  {label}: {s['n_trades']} trades, {s['total_return']:.1%} return")

    print_table(rows)
    print("""
Verdict heuristic
  - any variant with positive total_ret AND >=30 trades AND PF >=1.2  -> viable
  - all negative or trade-counts <20                                  -> ORB not retail-viable on this data
""")


if __name__ == "__main__":
    main()

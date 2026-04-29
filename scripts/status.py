#!/usr/bin/env python3
"""Quick at-a-glance status: equity, positions, recent tickets, recent audit.

Usage:
    python scripts/status.py
    python scripts/status.py --tail 30      # show last 30 audit lines
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import yaml
from dotenv import load_dotenv


def load_cfg(path: str = "config/signals.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tail", type=int, default=15,
                        help="audit log lines to show (default 15)")
    args = parser.parse_args()

    load_dotenv()

    # ---- Alpaca account ----
    print("=== Alpaca paper account ===")
    try:
        from alpaca.trading.client import TradingClient
        import os
        c = TradingClient(
            os.environ["ALPACA_API_KEY"],
            os.environ["ALPACA_API_SECRET"],
            paper=True,
        )
        a = c.get_account()
        print(f"  equity:  ${float(a.equity):,.2f}")
        print(f"  cash:    ${float(a.cash):,.2f}")
        print(f"  daytrade_count: {a.daytrade_count}")
        positions = c.get_all_positions()
        if positions:
            print(f"\n  positions ({len(positions)}):")
            for p in positions:
                print(f"    {p.symbol:>10}: {p.qty} @ avg ${p.avg_entry_price}, "
                      f"mkt_val ${p.market_value}, "
                      f"unrealized P/L ${p.unrealized_pl}")
        else:
            print("  positions: (none)")
    except Exception as e:
        print(f"  ERROR: {e}")

    # ---- Tickets ----
    print()
    print("=== Tickets ===")
    cfg = load_cfg()
    ticket_dir = Path(cfg["execution"]["ticket_dir"])
    by_status: dict[str, int] = {}
    recent: list[dict] = []
    if ticket_dir.exists():
        for p in sorted(ticket_dir.glob("*.json"))[-20:]:
            try:
                d = json.loads(p.read_text())
            except Exception:
                continue
            by_status[d.get("status", "?")] = by_status.get(d.get("status", "?"), 0) + 1
            recent.append({"path": p.name, **d})
    if not recent:
        print("  (no tickets)")
    else:
        for k, v in by_status.items():
            print(f"  {k:>10}: {v}")
        print()
        print("  most recent (up to 5):")
        for r in recent[-5:]:
            idea = r.get("idea", {})
            print(f"    {r['path']}  status={r['status']}  "
                  f"{idea.get('side')} {idea.get('symbol')} "
                  f"{r.get('sized_qty', 0):.4f}")

    # ---- Audit log tail ----
    print()
    print(f"=== Audit log (last {args.tail} entries) ===")
    audit_path = Path(cfg["execution"]["audit_log"])
    if not audit_path.exists():
        print("  (no audit log yet)")
        return 0
    with open(audit_path) as f:
        rows = list(csv.reader(f))
    if len(rows) <= 1:
        print("  (audit log empty)")
        return 0
    for row in rows[-args.tail:]:
        # row = [timestamp, event, source, symbol, side, detail]
        ts = row[0][:19] if row else ""
        event = row[1] if len(row) > 1 else ""
        sym = row[3] if len(row) > 3 else ""
        side = row[4] if len(row) > 4 else ""
        detail = (row[5][:80] + "...") if len(row) > 5 and len(row[5]) > 80 else (row[5] if len(row) > 5 else "")
        print(f"  {ts}  {event:>22}  {sym:>10}  {side:>6}  {detail}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

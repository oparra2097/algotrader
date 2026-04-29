#!/usr/bin/env python3
"""Reconcile a failed ticket against Alpaca: if the order actually filled,
flip the ticket from FAILED to EXECUTED and record the real fill data.

Use case: a JSON-serialization bug caused update_status(EXECUTED, fill=...)
to throw after the order had already submitted. Ticket got marked FAILED
but the position is real on Alpaca.

This script lists failed tickets, asks Alpaca whether the position
exists, and lets you flip each one back to EXECUTED.

Usage:
    python scripts/recover_failed_ticket.py             # interactive
    python scripts/recover_failed_ticket.py --list      # list failed only
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import yaml
from dotenv import load_dotenv

from src.audit.log import AuditLog
from src.execution.ticket_queue import TicketStatus


def load_config(path: str = "config/signals.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def list_failed(ticket_dir: Path) -> list[tuple[Path, dict]]:
    out: list[tuple[Path, dict]] = []
    for p in sorted(ticket_dir.glob("*.json")):
        try:
            d = json.loads(p.read_text())
        except Exception:
            continue
        if d.get("status") == TicketStatus.FAILED.value:
            out.append((p, d))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true",
                        help="list failed tickets and exit")
    args = parser.parse_args()

    load_dotenv()
    cfg = load_config()
    ticket_dir = Path(cfg["execution"]["ticket_dir"])
    audit = AuditLog(cfg["execution"]["audit_log"])

    failed = list_failed(ticket_dir)
    if not failed:
        print("no failed tickets found")
        return 0

    print(f"Found {len(failed)} failed ticket(s):\n")
    for p, d in failed:
        idea = d.get("idea", {})
        print(f"  {p.name}")
        print(f"    side={idea.get('side')} symbol={idea.get('symbol')} "
              f"qty={d.get('sized_qty')}")
        print(f"    notes: {d.get('notes', '')[:80]}")
    print()

    if args.list:
        return 0

    # Connect to Alpaca and look up live positions
    try:
        from src.execution.alpaca_broker import AlpacaPaperBroker
        broker = AlpacaPaperBroker()
        positions = {p: v for p, v in broker.account().open_positions.items()}
    except Exception as e:
        print(f"ERROR: could not reach Alpaca: {e}", file=sys.stderr)
        return 2

    print(f"Live positions: {positions or '(none)'}\n")

    for p, d in failed:
        idea = d.get("idea", {})
        sym = idea.get("symbol")
        side = idea.get("side")
        print(f"--- {p.name} ---")
        print(f"  ticket says: {side} {sym} qty={d.get('sized_qty')}")
        if sym in positions:
            print(f"  Alpaca shows: {sym} present (mkt_val=${positions[sym]:.2f})")
            ans = input("  flip to EXECUTED? [y/N] ").strip().lower()
            if ans == "y":
                d["status"] = TicketStatus.EXECUTED.value
                d["notes"] = (d.get("notes", "") +
                              " | reconciled-with-alpaca @ " +
                              datetime.now(timezone.utc).isoformat())
                d["fill"] = {
                    "reconciled": True,
                    "symbol": sym,
                    "market_value": positions[sym],
                }
                p.write_text(json.dumps(d, indent=2, default=str))
                audit.append("ticket_reconciled", source=idea.get("source", ""),
                             symbol=sym, side=side,
                             detail=f"flipped FAILED -> EXECUTED "
                                    f"(mkt_val=${positions[sym]:.2f})")
                print("  -> EXECUTED")
            else:
                print("  -> skipped")
        else:
            print(f"  Alpaca shows: NO position in {sym}")
            ans = input("  this ticket really did fail. Keep as FAILED? [y/N] ").strip().lower()
            if ans != "y":
                print("  -> left as-is, no change")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())

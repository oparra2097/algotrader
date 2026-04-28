#!/usr/bin/env python3
"""Review pending tickets one at a time and approve / reject / execute.

Usage:
    python scripts/execute_ticket.py            # interactive review of pending
    python scripts/execute_ticket.py --list     # list pending tickets

In paper mode this calls Alpaca paper. Until you have keys configured,
it falls back to a dry run that records the order request without
submitting.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import yaml
from dotenv import load_dotenv

from src.audit.log import AuditLog
from src.execution.ticket_queue import TicketQueue, TicketStatus


def load_config(path: str = "config/signals.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def _broker():
    try:
        from src.execution.alpaca_broker import AlpacaPaperBroker
        return AlpacaPaperBroker(), None
    except Exception as e:
        return None, str(e)


def review_one(path: Path, ticket, queue: TicketQueue, audit: AuditLog,
               dry_run: bool) -> None:
    print(f"\n--- ticket {ticket.ticket_id} ---")
    print(f"  created : {ticket.created_at}")
    idea = ticket.idea
    print(f"  source  : {idea['source']}")
    print(f"  symbol  : {idea['symbol']}  side: {idea['side']}")
    print(f"  size    : {ticket.sized_qty:.4f} units (${ticket.sized_dollars:.2f})")
    print(f"  stop    : {idea.get('stop_price')}")
    print(f"  target  : {idea.get('target_price')}")
    print(f"  why     : {idea['rationale']}")

    while True:
        ans = input("approve [a] / reject [r] / skip [s]? ").strip().lower()
        if ans in ("a", "r", "s"):
            break

    if ans == "s":
        print("  -> skipped")
        return

    if ans == "r":
        notes = input("reason: ").strip()
        queue.update_status(path, TicketStatus.REJECTED, notes=notes)
        audit.append("ticket_rejected", source=idea["source"],
                     symbol=idea["symbol"], side=idea["side"], detail=notes)
        print("  -> rejected")
        return

    # approved -> execute
    queue.update_status(path, TicketStatus.APPROVED)
    audit.append("ticket_approved", source=idea["source"],
                 symbol=idea["symbol"], side=idea["side"])

    broker, err = _broker()
    if broker is None:
        print(f"  [no broker: {err}] -> recording dry-run only")
        queue.update_status(path, TicketStatus.EXECUTED,
                            notes=f"dry-run (broker: {err})",
                            fill={"dry_run": True})
        audit.append("ticket_dry_run", source=idea["source"],
                     symbol=idea["symbol"], side=idea["side"], detail=err)
        return

    try:
        if idea["side"] == "close":
            fill = broker.close_position(idea["symbol"], dry_run=dry_run)
        else:
            fill = broker.place_market_order(
                symbol=idea["symbol"],
                qty=ticket.sized_qty,
                side=idea["side"],
                dry_run=dry_run,
            )
        queue.update_status(path, TicketStatus.EXECUTED,
                            fill=fill if isinstance(fill, dict) else {"resp": str(fill)})
        audit.append("ticket_executed", source=idea["source"],
                     symbol=idea["symbol"], side=idea["side"],
                     detail=fill if isinstance(fill, str) else "submitted")
        print("  -> executed")
    except Exception as e:
        queue.update_status(path, TicketStatus.FAILED, notes=str(e))
        audit.append("ticket_failed", source=idea["source"],
                     symbol=idea["symbol"], side=idea["side"], detail=str(e))
        print(f"  -> FAILED: {e}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true",
                        help="list pending tickets and exit")
    parser.add_argument("--dry-run", action="store_true",
                        help="don't actually submit to broker")
    args = parser.parse_args()

    load_dotenv()
    cfg = load_config()
    queue = TicketQueue(cfg["execution"]["ticket_dir"])
    audit = AuditLog(cfg["execution"]["audit_log"])

    pending = queue.list_pending()
    if args.list:
        if not pending:
            print("no pending tickets")
        for p, t in pending:
            print(f"  {p.name}  {t.idea['side']} {t.idea['symbol']}  "
                  f"{t.sized_qty:.4f} (${t.sized_dollars:.2f})")
        return 0

    if not pending:
        print("no pending tickets")
        return 0

    for p, t in pending:
        review_one(p, t, queue, audit, args.dry_run)

    return 0


if __name__ == "__main__":
    sys.exit(main())

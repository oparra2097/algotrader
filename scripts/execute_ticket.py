#!/usr/bin/env python3
"""Process pending tickets: interactively, or auto-approve all of them.

Usage:
    python scripts/execute_ticket.py                  # interactive review
    python scripts/execute_ticket.py --list           # list pending only
    python scripts/execute_ticket.py --auto-approve   # approve+execute all
    python scripts/execute_ticket.py --dry-run        # don't submit to broker

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


def _execute(path: Path, ticket, queue: TicketQueue, audit: AuditLog,
             dry_run: bool, broker) -> str:
    """Submit the order to the broker and update ticket status. Returns
    a short status string for the caller's log line."""
    idea = ticket.idea

    if broker is None:
        queue.update_status(path, TicketStatus.EXECUTED,
                            notes="dry-run (no broker)",
                            fill={"dry_run": True})
        audit.append("ticket_dry_run", source=idea["source"],
                     symbol=idea["symbol"], side=idea["side"],
                     detail="no broker")
        return "dry-run (no broker)"

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
        queue.update_status(
            path, TicketStatus.EXECUTED,
            fill=fill if isinstance(fill, dict) else {"resp": str(fill)},
        )
        audit.append("ticket_executed", source=idea["source"],
                     symbol=idea["symbol"], side=idea["side"],
                     detail="submitted")
        return "executed"
    except Exception as e:
        queue.update_status(path, TicketStatus.FAILED, notes=str(e))
        audit.append("ticket_failed", source=idea["source"],
                     symbol=idea["symbol"], side=idea["side"], detail=str(e))
        return f"FAILED: {e}"


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

    queue.update_status(path, TicketStatus.APPROVED)
    audit.append("ticket_approved", source=idea["source"],
                 symbol=idea["symbol"], side=idea["side"])

    broker, err = _broker()
    if broker is None:
        print(f"  [no broker: {err}] -> recording dry-run only")
    status = _execute(path, ticket, queue, audit, dry_run, broker)
    print(f"  -> {status}")


def auto_approve_all(pending: list, queue: TicketQueue, audit: AuditLog,
                     dry_run: bool) -> int:
    """Approve and execute every pending ticket without prompting.

    Returns the count of executed (or dry-run) tickets.
    """
    if not pending:
        return 0
    broker, err = _broker()
    if broker is None:
        print(f"[warn] no broker available: {err}; recording dry-run only")

    n_done = 0
    for path, ticket in pending:
        idea = ticket.idea
        queue.update_status(path, TicketStatus.APPROVED)
        audit.append("ticket_auto_approved", source=idea["source"],
                     symbol=idea["symbol"], side=idea["side"])
        status = _execute(path, ticket, queue, audit, dry_run, broker)
        print(f"  {path.name}: {idea['side']} {idea['symbol']} "
              f"{ticket.sized_qty:.4f} -> {status}")
        n_done += 1
    return n_done


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true",
                        help="list pending tickets and exit")
    parser.add_argument("--dry-run", action="store_true",
                        help="don't actually submit to broker")
    parser.add_argument("--auto-approve", action="store_true",
                        help="approve and execute all pending tickets without prompting")
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

    if args.auto_approve:
        n = auto_approve_all(pending, queue, audit, args.dry_run)
        print(f"\nauto-approved {n} ticket(s)")
        return 0

    for p, t in pending:
        review_one(p, t, queue, audit, args.dry_run)

    return 0


if __name__ == "__main__":
    sys.exit(main())

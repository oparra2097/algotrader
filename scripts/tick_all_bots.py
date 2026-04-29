#!/usr/bin/env python3
"""Run one tick of every configured bot, then auto-approve pending tickets.

Designed for unattended operation under launchd / cron. Sequential
execution: gold (parramacro) -> ETH (donchian) -> auto-approve all
pending. Stdout/stderr go to whatever the scheduler captures.

Usage:
    python scripts/tick_all_bots.py
    python scripts/tick_all_bots.py --dry-run   # bots run, tickets not submitted

A failure in one bot does not abort the others; each is run in its own
subprocess so a crash is contained.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYTHON = sys.executable


def run(label: str, cmd: list[str]) -> int:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"\n[{ts}] === {label} ===", flush=True)
    r = subprocess.run([PYTHON, *cmd], cwd=REPO_ROOT)
    print(f"[{ts}] === {label} exit={r.returncode} ===", flush=True)
    return r.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="bots still tick, but tickets are not submitted")
    parser.add_argument("--skip-gold", action="store_true")
    parser.add_argument("--skip-eth", action="store_true")
    parser.add_argument("--skip-execute", action="store_true",
                        help="generate tickets but don't auto-execute them")
    args = parser.parse_args()

    if not args.skip_gold:
        run("gold (parramacro)", ["scripts/run_signal_bot.py"])
    if not args.skip_eth:
        run("eth (donchian)", ["scripts/run_eth_bot.py"])
    if not args.skip_execute:
        cmd = ["scripts/execute_ticket.py", "--auto-approve"]
        if args.dry_run:
            cmd.append("--dry-run")
        run("execute pending tickets", cmd)

    return 0


if __name__ == "__main__":
    sys.exit(main())

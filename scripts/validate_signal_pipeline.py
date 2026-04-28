#!/usr/bin/env python3
"""End-to-end validator for the signal pipeline.

Boots the mock parramacro server in a subprocess, points the bot at it,
verifies that:
  - a "long" forecast (spot < p10) produces a buy ticket
  - a "neutral" forecast (spot inside the fan) produces no ticket
  - a "rich" forecast (spot > p90) in long-only mode produces a "close"
    ticket only when a position exists, otherwise nothing

Uses dry-run fallback so no Alpaca call is made.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import requests


MOCK_KEY = "devkey-validate"
MOCK_PORT = 5051
TMP_TICKETS = REPO_ROOT / "tickets-test"
TMP_AUDIT = REPO_ROOT / "results/signal_log_test.csv"


def start_mock(spot: float) -> subprocess.Popen:
    env = {
        **os.environ,
        "PARRAMACRO_API_KEY": MOCK_KEY,
        "MOCK_PORT": str(MOCK_PORT),
        "MOCK_GOLD_SPOT": str(spot),
        "PYTHONPATH": str(REPO_ROOT),
    }
    p = subprocess.Popen(
        [sys.executable, "dev/mock_parramacro.py"],
        env=env,
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # wait for healthz
    for _ in range(60):
        try:
            r = requests.get(f"http://127.0.0.1:{MOCK_PORT}/healthz", timeout=0.3)
            if r.status_code == 200:
                return p
        except Exception:
            pass
        time.sleep(0.1)
    p.terminate()
    raise RuntimeError("mock server failed to start")


def run_bot() -> str:
    env = {
        **os.environ,
        "PARRAMACRO_API_KEY": MOCK_KEY,
        "PARRAMACRO_BASE_URL": f"http://127.0.0.1:{MOCK_PORT}",
        "PYTHONPATH": str(REPO_ROOT),
    }
    out = subprocess.run(
        [sys.executable, "scripts/run_signal_bot.py"],
        env=env, cwd=REPO_ROOT,
        capture_output=True, text=True, timeout=60,
    )
    if out.returncode != 0:
        raise RuntimeError(f"bot exited {out.returncode}: {out.stderr}")
    return out.stdout


def reset_state(cfg_override_path: Path) -> None:
    if TMP_TICKETS.exists():
        shutil.rmtree(TMP_TICKETS)
    TMP_TICKETS.mkdir()
    if TMP_AUDIT.exists():
        TMP_AUDIT.unlink()
    cfg_override_path.write_text(_OVERRIDE_YAML)


_OVERRIDE_YAML = f"""\
parramacro:
  base_url: http://127.0.0.1:{MOCK_PORT}
  api_key_env: PARRAMACRO_API_KEY
  poll_interval_seconds: 900
  request_timeout_seconds: 5
  max_retries: 1
  staleness_max_hours:
    commodities: 36
    georisk: 2

risk:
  starting_equity_fallback: 1000
  risk_per_trade: 0.01
  max_position_fraction: 0.30
  max_open_positions: 3
  daily_loss_kill_pct: 0.05
  per_symbol_cooldown_hours: 24

execution:
  mode: ticket_queue
  paper_only: true
  ticket_dir: {TMP_TICKETS.name}
  audit_log: {TMP_AUDIT.relative_to(REPO_ROOT)}

policies:
  - name: gold_directional
    enabled: true
    commodity: gold
    symbol: GLD
    fan_horizon: nearest
    long_threshold: p10
    exit_threshold: p50
    stop_band: p2_5
    long_only: true
"""


def list_tickets() -> list[dict]:
    out = []
    for p in sorted(TMP_TICKETS.glob("*.json")):
        out.append(json.loads(p.read_text()))
    return out


def main() -> int:
    cfg_path = REPO_ROOT / "config/signals.yaml"
    backup = cfg_path.read_text()
    try:
        # ----- Case 1: spot below p10 -> expect a BUY ticket -----
        reset_state(cfg_path)
        proc = start_mock(spot=1850.0)
        try:
            stdout = run_bot()
        finally:
            proc.terminate(); proc.wait(timeout=5)
        tickets = list_tickets()
        assert len(tickets) == 1, f"expected 1 ticket, got {len(tickets)}: {stdout}"
        t = tickets[0]
        assert t["idea"]["side"] == "buy", t
        assert t["idea"]["symbol"] == "GLD", t
        assert t["idea"]["stop_price"] == 1800.0, t
        assert t["idea"]["target_price"] == 2050.0, t
        print("Case 1 (spot=1850, expect BUY)  OK")

        # ----- Case 2: spot inside fan -> no ticket -----
        reset_state(cfg_path)
        proc = start_mock(spot=2050.0)
        try:
            stdout = run_bot()
        finally:
            proc.terminate(); proc.wait(timeout=5)
        tickets = list_tickets()
        assert len(tickets) == 0, f"expected 0 tickets, got {len(tickets)}: {stdout}"
        print("Case 2 (spot=2050, expect NONE)  OK")

        # ----- Case 3: spot above p90, no open position, long-only -----
        # Long-only mode emits a "close", but the gate refuses (no open pos)
        # so no ticket should be written.
        reset_state(cfg_path)
        proc = start_mock(spot=2300.0)
        try:
            stdout = run_bot()
        finally:
            proc.terminate(); proc.wait(timeout=5)
        tickets = list_tickets()
        assert len(tickets) == 0, f"expected 0 tickets (close gated), got {len(tickets)}: {stdout}"
        print("Case 3 (spot=2300, no pos, expect NONE)  OK")

        print("\nAll signal-pipeline cases passed.")
        return 0
    finally:
        cfg_path.write_text(backup)
        if TMP_TICKETS.exists():
            shutil.rmtree(TMP_TICKETS)


if __name__ == "__main__":
    sys.exit(main())

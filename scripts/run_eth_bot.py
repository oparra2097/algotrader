#!/usr/bin/env python3
"""Run one ETH Donchian tick: pull bars, evaluate, gate, ticket.

Crypto trades 24/7 so this can be run any time. Recommended cadence
is once per day after the daily bar closes (UTC midnight + a small
buffer for yfinance to settle).

In ticket_queue mode (default), writes a pending ticket. Use
scripts/execute_ticket.py to review and approve. Switch
execution.mode to "auto" in config/eth.yaml only after several
manually-approved fills look correct.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import yaml
from dotenv import load_dotenv

from src.audit.log import AuditLog
from src.data.fetch import fetch_ohlcv
from src.execution.ticket_queue import Ticket, TicketQueue
from src.risk.gates import GateConfig, PortfolioState, evaluate as gate_evaluate
from src.strategy.donchian_live import evaluate as eth_evaluate


def load_config(path: str = "config/eth.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def get_portfolio_state(cfg: dict, audit: AuditLog, eth_symbol: str) -> tuple[PortfolioState, bool]:
    """Return (state, is_long_eth). Falls back to fake equity if Alpaca down."""
    try:
        from src.execution.alpaca_broker import AlpacaPaperBroker
        broker = AlpacaPaperBroker()
        snap = broker.account()
        # Alpaca returns crypto symbols as 'ETHUSD' (no slash) in get_all_positions
        # while orders are placed with 'ETH/USD'. Match either form.
        eth_pos_value = 0.0
        eth_keys = {eth_symbol, eth_symbol.replace("/", "")}
        for sym, val in snap.open_positions.items():
            if sym in eth_keys:
                eth_pos_value = val
                break
        is_long = eth_pos_value > 0
        return (
            PortfolioState(
                equity=snap.equity,
                starting_day_equity=snap.equity,
                open_positions=snap.open_positions,
                last_trade_time={},
            ),
            is_long,
        )
    except Exception as e:
        audit.append("alpaca_unreachable", detail=str(e))
        eq = float(cfg["risk"]["starting_equity_fallback"])
        return PortfolioState(eq, eq, {}, {}), False


def main() -> int:
    load_dotenv()
    cfg = load_config()

    s = cfg["strategy"]
    r = cfg["risk"]
    e = cfg["execution"]

    audit = AuditLog(e["audit_log"])
    queue = TicketQueue(e["ticket_dir"])

    # Pull recent ETH bars (daily, via yfinance)
    end = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    start = (datetime.now(timezone.utc) - timedelta(days=s["history_days"])).strftime("%Y-%m-%d")
    try:
        bars = fetch_ohlcv(s["data_symbol"], start, end, use_cache=False)
    except Exception as exc:
        audit.append("eth_data_fetch_fail", detail=str(exc))
        print(f"[error] failed to fetch {s['data_symbol']}: {exc}")
        return 2

    print(f"loaded {len(bars)} bars  ({bars.index[0].date()} -> {bars.index[-1].date()})")

    state, is_long = get_portfolio_state(cfg, audit, s["symbol"])
    print(f"equity ${state.equity:.2f}  is_long_{s['symbol']}={is_long}")

    idea = eth_evaluate(
        bars,
        symbol=s["symbol"],
        is_long=is_long,
        equity=state.equity,
        entry_lookback=s["entry_lookback"],
        exit_lookback=s["exit_lookback"],
        atr_period=s["atr_period"],
        atr_stop_multiplier=s["atr_stop_multiplier"],
        target_r_multiple=s["target_r_multiple"],
        trend_filter_ma=s["trend_filter_ma"],
        vol_target_daily=s["vol_target_daily"],
        vol_lookback=s["vol_lookback"],
        max_position_fraction=r["max_position_fraction"],
        risk_per_trade=r["risk_per_trade"],
    )

    if idea is None:
        latest_close = float(bars["close"].iloc[-1])
        if is_long:
            audit.append("no_signal_hold", source=f"donchian_live:{s['symbol']}",
                         symbol=s["symbol"], side="hold",
                         detail=f"close={latest_close:.2f}")
            print(f"[hold] long {s['symbol']}; close={latest_close:.2f} - no exit signal")
        else:
            audit.append("no_signal_flat", source=f"donchian_live:{s['symbol']}",
                         symbol=s["symbol"], side="flat",
                         detail=f"close={latest_close:.2f}")
            print(f"[no signal] flat {s['symbol']}; close={latest_close:.2f}")
        return 0

    # Vol-target sizing if entering, else size to flatten the position
    spot = idea.metadata.get("spot", float(bars["close"].iloc[-1]))

    if idea.side == "buy":
        vol = idea.metadata.get("vol")
        # Vol-target notional, capped at max_position_fraction
        if vol and vol > 0:
            target_dollars = (s["vol_target_daily"] / vol) * state.equity
        else:
            target_dollars = state.equity * r["max_position_fraction"]
        target_dollars = min(target_dollars, state.equity * r["max_position_fraction"])
        size_qty = target_dollars / spot
        sized_dollars = target_dollars
        sized_qty = size_qty
    else:  # close
        # close uses whatever position size we currently hold
        # (broker can use close_position; ticket carries notional for record)
        eth_value = state.open_positions.get(s["symbol"]) or \
                    state.open_positions.get(s["symbol"].replace("/", "")) or 0.0
        sized_dollars = eth_value
        sized_qty = eth_value / spot if spot > 0 else 0.0

    # The shared risk gates expect equity sizing; we've already done it.
    # Run lightweight gate checks for daily loss and cooldown anyway.
    gate_cfg = GateConfig(
        risk_per_trade=r["risk_per_trade"],
        max_position_fraction=r["max_position_fraction"],
        max_open_positions=r["max_open_positions"],
        daily_loss_kill_pct=r["daily_loss_kill_pct"],
        per_symbol_cooldown_hours=r["per_symbol_cooldown_hours"],
    )
    gate_result = gate_evaluate(idea, gate_cfg, state)
    audit.append("gate_decision", source=idea.source, symbol=idea.symbol,
                 side=idea.side,
                 detail=f"ok={gate_result.ok} reason={gate_result.reason!r}")
    if not gate_result.ok:
        print(f"[gated] {idea.symbol} {idea.side}: {gate_result.reason}")
        return 0

    ticket = Ticket.new(idea, sized_dollars=sized_dollars, sized_qty=sized_qty)
    path = queue.write(ticket)
    audit.append("ticket_created", source=idea.source, symbol=idea.symbol,
                 side=idea.side, detail=str(path))
    print(f"[ticket] {path.name}: {idea.side} {idea.symbol} "
          f"~{sized_qty:.6f} units (${sized_dollars:.2f})")
    print(f"         {idea.rationale}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

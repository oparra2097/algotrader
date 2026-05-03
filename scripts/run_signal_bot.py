#!/usr/bin/env python3
"""Run one signal-bot tick: poll ParraMacro, translate, gate, ticket.

In ticket_queue mode (default) the bot writes a PENDING ticket to disk
instead of submitting the order. Use scripts/execute_ticket.py to review
and approve. Once you've shipped 20 approved-as-recommended tickets,
flip execution.mode to "auto" in config/signals.yaml.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import yaml
from dotenv import load_dotenv

from src.audit.log import AuditLog
from src.execution.ticket_queue import Ticket, TicketQueue
from src.risk.gates import GateConfig, PortfolioState, evaluate
from src.signals.parramacro_client import ParraMacroClient, StaleDataError
from src.signals.translators.commodity_directional import commodity_directional


def load_config(path: str = "config/signals.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def get_portfolio_state(cfg: dict, audit: AuditLog) -> PortfolioState:
    """Try Alpaca first; fall back to a configured starting equity."""
    if not cfg["execution"]["paper_only"]:
        raise RuntimeError("live trading not implemented; paper_only must be true")

    try:
        from src.execution.alpaca_broker import AlpacaPaperBroker
        broker = AlpacaPaperBroker()
        snap = broker.account()
        return PortfolioState(
            equity=snap.equity,
            starting_day_equity=snap.equity,            # TODO: cache real day-open
            open_positions=snap.open_positions,
            last_trade_time={},
        )
    except Exception as e:
        audit.append("alpaca_unreachable", detail=str(e))
        eq = float(cfg["risk"]["starting_equity_fallback"])
        return PortfolioState(eq, eq, {}, {})


def main() -> int:
    load_dotenv()
    cfg = load_config()

    audit = AuditLog(cfg["execution"]["audit_log"])
    queue = TicketQueue(cfg["execution"]["ticket_dir"])

    # build client
    pm_cfg = cfg["parramacro"]
    base_url = os.environ.get("PARRAMACRO_BASE_URL") or pm_cfg["base_url"]
    client = ParraMacroClient.from_env(
        base_url=base_url,
        api_key_env=pm_cfg["api_key_env"],
        timeout=pm_cfg["request_timeout_seconds"],
        max_retries=pm_cfg["max_retries"],
    )

    # Freshness is per-policy. Each policy declares the parramacro series
    # it depends on via `requires:` in config; we only check those.
    # Commodities is checked per-policy via the forecast's own
    # summary.fit_at, not here.
    try:
        health = client.health()
    except Exception as e:
        audit.append("health_fetch_fail", detail=str(e))
        print(f"[error] /health unreachable: {e}")
        return 1

    enabled_policies = [p for p in cfg["policies"] if p.get("enabled", True)]
    needed_series: set[str] = set()
    for pol in enabled_policies:
        for s in pol.get("requires", []):
            needed_series.add(s)
    # commodities is checked per-policy below; skip it here even if listed
    needed_series.discard("commodities")

    skip_run = False
    for series in needed_series:
        max_h = pm_cfg["staleness_max_hours"].get(series)
        if max_h is None:
            audit.append("missing_staleness_config", detail=series)
            continue
        try:
            client.assert_fresh(series, max_h, health)
        except StaleDataError as e:
            audit.append("stale_data_skip", detail=str(e))
            print(f"[skip] stale data: {e}")
            skip_run = True
            break
    if skip_run:
        return 0

    state = get_portfolio_state(cfg, audit)
    audit.append("portfolio_snapshot", detail=f"equity=${state.equity:.2f}")
    print(f"equity ${state.equity:.2f}  open={list(state.open_positions)}")

    gate_cfg = GateConfig(
        risk_per_trade=cfg["risk"]["risk_per_trade"],
        max_position_fraction=cfg["risk"]["max_position_fraction"],
        max_open_positions=cfg["risk"]["max_open_positions"],
        daily_loss_kill_pct=cfg["risk"]["daily_loss_kill_pct"],
        per_symbol_cooldown_hours=cfg["risk"]["per_symbol_cooldown_hours"],
    )

    n_tickets = 0
    for pol in cfg["policies"]:
        if not pol.get("enabled", True):
            continue
        if pol["name"] != "gold_directional":
            audit.append("policy_skip_unknown", detail=pol["name"])
            continue

        try:
            forecast = client.commodity_forecast(pol["commodity"])
        except Exception as e:
            audit.append("forecast_fetch_fail",
                         source=pol["name"], symbol=pol["symbol"], detail=str(e))
            print(f"[error] {pol['name']}: {e}")
            continue

        # per-policy freshness check on the forecast's own fit_at
        try:
            commodity_max_h = pm_cfg["staleness_max_hours"].get("commodities", 36)
            client.assert_forecast_fresh(forecast, max_hours=commodity_max_h)
        except StaleDataError as e:
            audit.append("stale_forecast_skip",
                         source=pol["name"], symbol=pol["symbol"], detail=str(e))
            print(f"[skip stale] {pol['name']}: {e}")
            continue

        idea = commodity_directional(
            forecast,
            symbol=pol["symbol"],
            long_threshold=pol["long_threshold"],
            exit_threshold=pol["exit_threshold"],
            stop_band=pol["stop_band"],
            fan_horizon=pol["fan_horizon"],
            long_only=pol["long_only"],
        )
        if idea is None:
            audit.append("no_signal", source=pol["name"], symbol=pol["symbol"],
                         detail=f"spot={forecast.get('spot')}")
            print(f"[no signal] {pol['name']} (spot={forecast.get('spot')})")
            continue

        # If the policy is in direction_only mode, discard the model-frame
        # stop/target and re-derive them from the venue's own ATR. This
        # decouples signal generation (parramacro frame) from execution
        # (Alpaca / GLD frame).
        exec_cfg = pol.get("execution", {})
        exec_mode = exec_cfg.get("mode", "direction_only")
        if exec_mode == "direction_only" and idea.side in ("buy", "sell"):
            from src.execution.direction_only_sizing import compute_levels
            direction = "long" if idea.side == "buy" else "short"
            levels = compute_levels(
                symbol=idea.symbol,
                direction=direction,
                equity=state.equity,
                risk_per_trade=cfg["risk"]["risk_per_trade"],
                max_position_fraction=cfg["risk"]["max_position_fraction"],
                atr_period=exec_cfg.get("atr_period", 14),
                atr_stop_multiplier=exec_cfg.get("atr_stop_multiplier", 2.0),
                target_r_multiple=exec_cfg.get("target_r_multiple", 3.0),
                atr_lookback_days=exec_cfg.get("atr_lookback_days", 60),
            )
            if levels is None:
                audit.append("direction_only_sizing_fail",
                             source=idea.source, symbol=idea.symbol,
                             detail="venue bars unavailable or ATR window short")
                print(f"[error] {pol['name']}: could not size {idea.symbol} "
                      f"from venue data")
                continue
            # Preserve model-frame numbers in metadata for audit / replay
            idea.metadata["model_spot"] = idea.metadata.get("spot")
            idea.metadata["model_stop"] = idea.stop_price
            idea.metadata["model_target"] = idea.target_price
            idea.metadata["spot"] = levels.spot
            idea.metadata["atr"] = levels.atr
            idea.metadata["mode"] = "direction_only"
            idea.stop_price = levels.stop_price
            idea.target_price = levels.target_price
            idea.rationale += (
                f" | venue {idea.symbol}: spot={levels.spot:.2f} "
                f"ATR={levels.atr:.2f} stop={levels.stop_price:.2f} "
                f"target={levels.target_price:.2f}"
            )

        result = evaluate(idea, gate_cfg, state)
        audit.append("gate_decision", source=idea.source, symbol=idea.symbol,
                     side=idea.side,
                     detail=f"ok={result.ok} reason={result.reason!r} "
                            f"size=${result.sized_dollars:.2f}")
        if not result.ok:
            print(f"[gated] {idea.symbol} {idea.side}: {result.reason}")
            continue

        ticket = Ticket.new(idea, result.sized_dollars, result.sized_qty)
        path = queue.write(ticket)
        n_tickets += 1
        audit.append("ticket_created", source=idea.source, symbol=idea.symbol,
                     side=idea.side, detail=str(path))
        print(f"[ticket] {path.name}: {idea.side} {idea.symbol} "
              f"~{result.sized_qty:.4f} units (${result.sized_dollars:.2f})")
        print(f"         {idea.rationale}")

    print(f"\n{n_tickets} ticket(s) written to {queue.dir}/  "
          f"(mode={cfg['execution']['mode']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

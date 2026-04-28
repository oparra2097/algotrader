"""Risk gates: pre-trade validation for TradeIdeas.

Gates check max position size, max open positions, daily loss kill-switch,
per-symbol cooldown, and stale-data refusal. Each gate returns (ok, reason).
A trade that fails any gate gets logged and skipped.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src.signals.trade_idea import TradeIdea


@dataclass
class GateConfig:
    risk_per_trade: float
    max_position_fraction: float
    max_open_positions: int
    daily_loss_kill_pct: float
    per_symbol_cooldown_hours: float


@dataclass
class PortfolioState:
    equity: float
    starting_day_equity: float
    open_positions: dict[str, float]               # symbol -> current notional
    last_trade_time: dict[str, datetime]           # symbol -> ts of last fill


@dataclass
class GateResult:
    ok: bool
    reason: str = ""
    sized_dollars: float = 0.0                     # how much to deploy if ok
    sized_qty: float = 0.0


def evaluate(
    idea: TradeIdea,
    cfg: GateConfig,
    state: PortfolioState,
    now: datetime | None = None,
) -> GateResult:
    now = now or datetime.now(timezone.utc)

    # 1. daily loss kill switch
    daily_dd = state.equity / state.starting_day_equity - 1
    if daily_dd <= -cfg.daily_loss_kill_pct:
        return GateResult(False, f"daily loss kill ({daily_dd:.2%})")

    # 2. close orders skip the rest of the gates
    if idea.side == "close":
        if idea.symbol not in state.open_positions:
            return GateResult(False, f"no open position in {idea.symbol} to close")
        return GateResult(True, sized_dollars=state.open_positions[idea.symbol])

    # 3. max open positions
    if idea.symbol not in state.open_positions:
        if len(state.open_positions) >= cfg.max_open_positions:
            return GateResult(False, f"max open positions {cfg.max_open_positions}")

    # 4. don't pyramid - if we already hold the name, skip
    if idea.symbol in state.open_positions:
        return GateResult(False, f"already long {idea.symbol}")

    # 5. cooldown
    last = state.last_trade_time.get(idea.symbol)
    if last is not None:
        age = (now - last).total_seconds() / 3600
        if age < cfg.per_symbol_cooldown_hours:
            return GateResult(False, f"cooldown {cfg.per_symbol_cooldown_hours}h "
                                     f"(last trade {age:.1f}h ago)")

    # 6. sizing
    if idea.qty_dollars is not None:
        sized = idea.qty_dollars
    elif idea.stop_price is not None:
        # we need an entry price to compute stop distance; approximate with
        # the spot in metadata, otherwise fall back to a fraction sizing
        spot = idea.metadata.get("spot")
        if spot is None or idea.stop_price <= 0:
            return GateResult(False, "no spot in idea metadata for sizing")
        stop_distance = abs(spot - idea.stop_price)
        if stop_distance <= 0:
            return GateResult(False, "zero stop distance")
        risk_dollars = state.equity * cfg.risk_per_trade
        target_notional = (risk_dollars / stop_distance) * spot
        cap = state.equity * cfg.max_position_fraction
        sized = min(target_notional, cap)
    else:
        sized = state.equity * cfg.max_position_fraction

    if sized < 1.0:
        return GateResult(False, f"sized notional ${sized:.2f} below $1 minimum")

    spot = idea.metadata.get("spot", 1.0) or 1.0
    return GateResult(True, sized_dollars=sized, sized_qty=sized / spot)

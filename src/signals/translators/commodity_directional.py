"""Translate a commodity fan forecast into a directional TradeIdea.

Rule (gold-only starter):
    spot < p10  ->  long; stop = p2_5; target = p50
    spot > p90  ->  close any long position (long-only mode), or short
                    (when long_only=False); stop = p97_5; target = p50
    otherwise   ->  no signal.

Consumes the *normalized* forecast shape from ParraMacroClient
(see _normalize_commodity_forecast), so quarter keys are Q+N and
quantile fields are p2_5/p10/p50/p90/p97_5 regardless of which
ParraMacro path produced them.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.signals.trade_idea import TradeIdea


def _pick_quarter(fan: list[dict[str, Any]], horizon: str) -> dict[str, Any]:
    if not fan:
        raise ValueError("empty fan")
    if horizon == "nearest":
        return fan[0]
    for q in fan:
        if q.get("key") == horizon or q.get("label") == horizon:
            return q
    raise ValueError(f"horizon {horizon!r} not in fan")


def commodity_directional(
    forecast: dict[str, Any],
    *,
    symbol: str,
    long_threshold: str = "p10",
    exit_threshold: str = "p50",
    stop_band: str = "p2_5",
    fan_horizon: str = "nearest",
    long_only: bool = True,
    spot_override: float | None = None,
) -> TradeIdea | None:
    fan = forecast.get("fan", [])
    spot = forecast.get("spot") if spot_override is None else spot_override
    if spot is None:
        # backtest path without injected spot - cannot make a decision
        return None

    q = _pick_quarter(fan, fan_horizon)
    p10 = q.get(long_threshold)
    p50 = q.get(exit_threshold)
    p2_5 = q.get(stop_band)
    p90 = q.get("p90")
    p97_5 = q.get("p97_5")

    if None in (p10, p50, p2_5, p90, p97_5):
        return None

    ts = datetime.now(timezone.utc)
    commodity = forecast.get("commodity", "?")
    quarter_label = q.get("label", q.get("key", "?"))

    if spot < p10:
        return TradeIdea(
            source=f"parramacro:commodity_directional:{commodity}",
            symbol=symbol,
            side="buy",
            timestamp=ts,
            rationale=(
                f"{commodity} spot {spot:.2f} below {quarter_label} "
                f"{long_threshold}={p10:.2f} -> long, "
                f"stop {p2_5:.2f}, target p50 {p50:.2f}"
            ),
            stop_price=float(p2_5),
            target_price=float(p50),
            metadata={"commodity": commodity, "spot": spot, "quarter": q,
                      "rule": "below_p10"},
        )

    if spot > p90:
        if long_only:
            return TradeIdea(
                source=f"parramacro:commodity_directional:{commodity}",
                symbol=symbol,
                side="close",
                timestamp=ts,
                rationale=(
                    f"{commodity} spot {spot:.2f} above {quarter_label} "
                    f"p90={p90:.2f}; long-only -> close any long"
                ),
                metadata={"commodity": commodity, "spot": spot, "quarter": q,
                          "rule": "above_p90_long_only_close"},
            )
        return TradeIdea(
            source=f"parramacro:commodity_directional:{commodity}",
            symbol=symbol,
            side="sell",
            timestamp=ts,
            rationale=(
                f"{commodity} spot {spot:.2f} above {quarter_label} "
                f"p90={p90:.2f} -> short, stop {p97_5:.2f}, target p50 {p50:.2f}"
            ),
            stop_price=float(p97_5),
            target_price=float(p50),
            metadata={"commodity": commodity, "spot": spot, "quarter": q,
                      "rule": "above_p90"},
        )

    return None

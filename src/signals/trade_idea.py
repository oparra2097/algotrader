"""TradeIdea: the contract between signal sources and execution.

Every signal source (ParraMacro, Donchian, ORB, manual) emits TradeIdea
objects. Execution validates them against risk gates and either places
the order, queues a review ticket, or rejects it.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Literal


Side = Literal["buy", "sell", "close"]


@dataclass
class TradeIdea:
    source: str                          # "parramacro:gold_directional", "donchian:eth", etc.
    symbol: str                          # broker symbol, e.g. "GLD", "ETH/USD"
    side: Side
    timestamp: datetime
    rationale: str
    # sizing inputs - either provide stop_price (preferred) or qty_dollars
    stop_price: float | None = None
    target_price: float | None = None
    qty_dollars: float | None = None
    # raw signal data for audit / replay
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["timestamp"] = self.timestamp.astimezone(timezone.utc).isoformat()
        return d

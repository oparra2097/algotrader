"""File-backed ticket queue for the review-then-auto-execute workflow.

Tickets are JSON files written to a directory. The signal bot writes
PENDING tickets; the user runs scripts/execute_ticket.py to APPROVE or
REJECT each one. Once 20 consecutive APPROVED-as-recommended tickets
have shipped, the bot can be flipped to auto-mode.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from src.signals.trade_idea import TradeIdea


class TicketStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    FAILED = "failed"


@dataclass
class Ticket:
    ticket_id: str
    created_at: str
    status: TicketStatus
    idea: dict[str, Any]
    sized_dollars: float
    sized_qty: float
    notes: str = ""
    fill: dict[str, Any] | None = None

    @classmethod
    def new(cls, idea: TradeIdea, sized_dollars: float, sized_qty: float) -> "Ticket":
        return cls(
            ticket_id=uuid.uuid4().hex[:12],
            created_at=datetime.now(timezone.utc).isoformat(),
            status=TicketStatus.PENDING,
            idea=idea.to_dict(),
            sized_dollars=sized_dollars,
            sized_qty=sized_qty,
        )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d


class TicketQueue:
    def __init__(self, ticket_dir: str | Path) -> None:
        self.dir = Path(ticket_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def write(self, ticket: Ticket) -> Path:
        path = self.dir / f"{ticket.created_at[:10]}_{ticket.ticket_id}.json"
        path.write_text(json.dumps(ticket.to_dict(), indent=2))
        return path

    def list_pending(self) -> list[tuple[Path, Ticket]]:
        out = []
        for p in sorted(self.dir.glob("*.json")):
            try:
                d = json.loads(p.read_text())
                if d.get("status") == TicketStatus.PENDING.value:
                    out.append((p, _ticket_from_dict(d)))
            except Exception:
                continue
        return out

    def update_status(self, path: Path, status: TicketStatus,
                      notes: str = "", fill: dict | None = None) -> None:
        d = json.loads(path.read_text())
        d["status"] = status.value
        if notes:
            d["notes"] = notes
        if fill is not None:
            d["fill"] = fill
        # default=str handles UUID, datetime, Decimal, etc. that the alpaca
        # SDK puts in order-response dicts.
        path.write_text(json.dumps(d, indent=2, default=str))


def _ticket_from_dict(d: dict[str, Any]) -> Ticket:
    return Ticket(
        ticket_id=d["ticket_id"],
        created_at=d["created_at"],
        status=TicketStatus(d["status"]),
        idea=d["idea"],
        sized_dollars=d["sized_dollars"],
        sized_qty=d["sized_qty"],
        notes=d.get("notes", ""),
        fill=d.get("fill"),
    )

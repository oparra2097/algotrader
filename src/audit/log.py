"""Append-only CSV audit log.

Every signal pulled, every TradeIdea generated, every gate decision,
every ticket created, every fill - all of it lands here in a single
chronological log. That's the source of truth when something looks weird.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HEADERS = ["timestamp", "event", "source", "symbol", "side", "detail"]


class AuditLog:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            with open(self.path, "w", newline="") as f:
                csv.writer(f).writerow(HEADERS)

    def append(self, event: str, source: str = "", symbol: str = "",
               side: str = "", detail: Any = "") -> None:
        ts = datetime.now(timezone.utc).isoformat()
        with open(self.path, "a", newline="") as f:
            csv.writer(f).writerow([ts, event, source, symbol, side, str(detail)])

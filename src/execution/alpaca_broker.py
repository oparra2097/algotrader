"""Thin Alpaca paper-trading wrapper.

We never call live endpoints. The TradingClient is configured with
paper=True at construction time; the live key path is intentionally
absent. Order placement is split into two stages: build_order builds the
request object, place_order submits it. This makes it trivial to run a
"dry-run" pass that returns the request without sending.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv


@dataclass
class AccountSnapshot:
    equity: float
    cash: float
    open_positions: dict[str, float]            # symbol -> notional


class AlpacaPaperBroker:
    def __init__(self) -> None:
        load_dotenv()
        key = os.environ.get("ALPACA_API_KEY")
        secret = os.environ.get("ALPACA_API_SECRET")
        if not key or not secret:
            raise RuntimeError(
                "Missing ALPACA_API_KEY / ALPACA_API_SECRET. "
                "Copy .env.example to .env and fill in your paper credentials."
            )
        from alpaca.trading.client import TradingClient

        self.client = TradingClient(key, secret, paper=True)

    def account(self) -> AccountSnapshot:
        a = self.client.get_account()
        positions = self.client.get_all_positions()
        open_pos = {p.symbol: float(p.market_value) for p in positions}
        return AccountSnapshot(
            equity=float(a.equity),
            cash=float(a.cash),
            open_positions=open_pos,
        )

    def place_market_order(
        self,
        symbol: str,
        side: str,
        qty: float | None = None,
        notional: float | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Submit a market order. Supports stocks and crypto.

        Symbol convention:
          - Stocks: 'GLD', 'SPY', 'TSLA' -> DAY tif
          - Crypto: 'ETH/USD', 'BTC/USD' (slash form) -> GTC tif

        Either qty (units) or notional (dollars) must be specified.
        Crypto supports fractional via either; stocks use qty (Alpaca
        also supports notional for fractional stocks).
        """
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        if (qty is None) == (notional is None):
            raise ValueError("specify exactly one of qty / notional")

        is_crypto = "/" in symbol
        side_enum = OrderSide.BUY if side == "buy" else OrderSide.SELL
        # crypto markets are 24/7 -> GTC; equities -> DAY
        tif = TimeInForce.GTC if is_crypto else TimeInForce.DAY

        req_kwargs: dict[str, Any] = {
            "symbol": symbol,
            "side": side_enum,
            "time_in_force": tif,
        }
        if qty is not None:
            req_kwargs["qty"] = qty
        else:
            req_kwargs["notional"] = notional

        req = MarketOrderRequest(**req_kwargs)
        if dry_run:
            return _to_json_safe(
                {"dry_run": True, "request": req.model_dump()}
            )
        order = self.client.submit_order(req)
        return _to_json_safe(
            order.model_dump() if hasattr(order, "model_dump") else dict(order)
        )

    def close_position(self, symbol: str, dry_run: bool = False) -> dict[str, Any]:
        if dry_run:
            return {"dry_run": True, "close": symbol}
        order = self.client.close_position(symbol)
        return _to_json_safe(
            order.model_dump() if hasattr(order, "model_dump") else dict(order)
        )


def _to_json_safe(obj: Any) -> Any:
    """Recursively convert UUID, datetime, Decimal, Enum, etc. to JSON-native.

    The alpaca-py SDK puts UUID and datetime objects in its model_dump()
    output. Plain json.dumps chokes on those; this helper normalizes
    everything to strings/numbers/bools/lists/dicts before serialization.
    """
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _to_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_to_json_safe(v) for v in obj]
    return str(obj)

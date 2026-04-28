"""Alpaca historical 5-min bar fetcher with on-disk parquet cache.

Uses the alpaca-py SDK against the paper data endpoint. Free tier returns
IEX-only data, which is fine for backtesting ORB but excludes some
fragmented liquidity. Move to SIP feed when going live with capital.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

CACHE_DIR = Path("data/cache/alpaca")


def _client():
    from alpaca.data.historical import StockHistoricalDataClient

    load_dotenv()
    key = os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("ALPACA_API_SECRET")
    if not key or not secret:
        raise RuntimeError(
            "Missing ALPACA_API_KEY / ALPACA_API_SECRET. "
            "Copy .env.example to .env and fill in your paper credentials."
        )
    return StockHistoricalDataClient(key, secret)


def fetch_bars(
    symbol: str,
    start: str,
    end: str | None = None,
    timeframe: str = "5Min",
    feed: str = "iex",
    use_cache: bool = True,
) -> pd.DataFrame:
    """Return a DataFrame indexed by UTC timestamp with OHLCV columns."""
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    end_str = end or "now"
    cache_path = CACHE_DIR / f"{symbol}_{timeframe}_{start}_{end_str}_{feed}.parquet"
    if use_cache and cache_path.exists():
        return pd.read_parquet(cache_path)

    tf_map = {
        "1Min": TimeFrame(1, TimeFrameUnit.Minute),
        "5Min": TimeFrame(5, TimeFrameUnit.Minute),
        "15Min": TimeFrame(15, TimeFrameUnit.Minute),
        "1Hour": TimeFrame(1, TimeFrameUnit.Hour),
        "1Day": TimeFrame(1, TimeFrameUnit.Day),
    }
    if timeframe not in tf_map:
        raise ValueError(f"timeframe must be one of {list(tf_map)}")

    start_dt = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    end_dt = (
        datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
        if end
        else datetime.now(timezone.utc)
    )

    req = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=tf_map[timeframe],
        start=start_dt,
        end=end_dt,
        feed=feed,
    )
    resp = _client().get_stock_bars(req)
    df = resp.df
    if df.empty:
        raise ValueError(f"No bars returned for {symbol}")

    # alpaca-py returns a MultiIndex (symbol, timestamp); flatten it
    if isinstance(df.index, pd.MultiIndex):
        df = df.reset_index(level=0, drop=True)
    df.index = pd.to_datetime(df.index, utc=True)
    df = df[["open", "high", "low", "close", "volume"]].dropna()

    df.to_parquet(cache_path)
    return df


def fetch_universe(
    symbols: list[str],
    start: str,
    end: str | None = None,
    timeframe: str = "5Min",
) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        try:
            out[sym] = fetch_bars(sym, start, end, timeframe)
        except Exception as e:
            print(f"  [skip] {sym}: {e}")
    return out

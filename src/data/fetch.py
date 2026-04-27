"""Historical OHLCV data fetching with on-disk cache."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import yfinance as yf

CACHE_DIR = Path("data/cache")


def fetch_ohlcv(
    symbol: str,
    start: str,
    end: str | None = None,
    use_cache: bool = True,
) -> pd.DataFrame:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    safe_sym = symbol.replace("/", "_")
    cache_path = CACHE_DIR / f"{safe_sym}_{start}_{end or 'now'}.parquet"

    if use_cache and cache_path.exists():
        return pd.read_parquet(cache_path)

    df = yf.download(
        symbol,
        start=start,
        end=end,
        auto_adjust=False,
        progress=False,
    )
    if df.empty:
        raise ValueError(f"No data returned for {symbol}")

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.columns = [str(c).lower() for c in df.columns]
    df = df[["open", "high", "low", "close", "volume"]].dropna()
    df.index = pd.to_datetime(df.index)

    df.to_parquet(cache_path)
    return df

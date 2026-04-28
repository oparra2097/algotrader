"""Intraday backtester for the Opening Range Breakout strategy.

Walks 5-min bars per symbol, per session. After the opening range is
fixed, the first close that breaks the OR triggers an entry; stop sits
at the opposite OR boundary; target = R-multiple of the range; if
neither hits, flatten N minutes before the closing bell.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.strategy.orb import OpeningRange, opening_range, session_bars_by_day


@dataclass
class OrbTrade:
    symbol: str
    date: pd.Timestamp
    side: str  # "long" or "short"
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    size: float
    pnl: float
    r_multiple: float
    reason: str


@dataclass
class OrbBacktestResult:
    equity_curve: pd.Series
    trades: list[OrbTrade]
    stats: dict


def _flatten_minute(close_h: int, close_m: int, minutes_before: int) -> tuple[int, int]:
    total = close_h * 60 + close_m - minutes_before
    return total // 60, total % 60


def _simulate_session(
    symbol: str,
    session: pd.DataFrame,
    or_minutes: int,
    target_r: float,
    flatten_minute_before_close: int,
    long_only: bool,
    risk_dollars: float,
    max_position_dollars: float,
    slippage: float,
    fee_per_side: float,
) -> OrbTrade | None:
    or_ = opening_range(session, or_minutes)
    if or_ is None or or_.width <= 0:
        return None

    bar_minutes = (session.index[1] - session.index[0]).total_seconds() / 60 if len(session) > 1 else 5
    n_or_bars = max(1, int(round(or_minutes / bar_minutes)))
    after_or = session.iloc[n_or_bars:]
    if after_or.empty:
        return None

    flatten_h, flatten_m = _flatten_minute(21, 0, flatten_minute_before_close)
    flatten_minutes = flatten_h * 60 + flatten_m

    side: str | None = None
    entry_price: float | None = None
    entry_time: pd.Timestamp | None = None
    stop_price: float | None = None
    target_price: float | None = None

    for ts, bar in after_or.iterrows():
        bar_minutes_of_day = ts.hour * 60 + ts.minute

        # ---- forced flatten near the bell ----
        if entry_price is not None and bar_minutes_of_day >= flatten_minutes:
            return _close(symbol, or_, side, entry_time, entry_price,
                          bar["open"], "eod_flatten", risk_dollars,
                          slippage, fee_per_side)

        # ---- entry: first bar that closes outside the OR ----
        if entry_price is None and bar_minutes_of_day < flatten_minutes:
            if bar["close"] > or_.or_high:
                side = "long"
                fill = bar["close"] * (1 + slippage)
                size = min(risk_dollars / or_.width, max_position_dollars / fill)
                if size <= 0:
                    return None
                entry_price = fill
                entry_time = ts
                stop_price = or_.or_low
                target_price = or_.or_high + target_r * or_.width
            elif (not long_only) and bar["close"] < or_.or_low:
                side = "short"
                fill = bar["close"] * (1 - slippage)
                size = min(risk_dollars / or_.width, max_position_dollars / fill)
                if size <= 0:
                    return None
                entry_price = fill
                entry_time = ts
                stop_price = or_.or_high
                target_price = or_.or_low - target_r * or_.width
            continue

        # ---- in trade: check stop, target ----
        if entry_price is not None:
            if side == "long":
                if bar["low"] <= stop_price:
                    return _close(symbol, or_, side, entry_time, entry_price,
                                  stop_price, "stop", risk_dollars,
                                  slippage, fee_per_side)
                if bar["high"] >= target_price:
                    return _close(symbol, or_, side, entry_time, entry_price,
                                  target_price, "target", risk_dollars,
                                  slippage, fee_per_side)
            else:  # short
                if bar["high"] >= stop_price:
                    return _close(symbol, or_, side, entry_time, entry_price,
                                  stop_price, "stop", risk_dollars,
                                  slippage, fee_per_side)
                if bar["low"] <= target_price:
                    return _close(symbol, or_, side, entry_time, entry_price,
                                  target_price, "target", risk_dollars,
                                  slippage, fee_per_side)

    # session ended without exit -> flatten on last bar's close
    if entry_price is not None:
        last = after_or.iloc[-1]
        return _close(symbol, or_, side, entry_time, entry_price,
                      last["close"], "session_end", risk_dollars,
                      slippage, fee_per_side)
    return None


def _close(
    symbol: str,
    or_: OpeningRange,
    side: str,
    entry_time: pd.Timestamp,
    entry_price: float,
    raw_exit: float,
    reason: str,
    risk_dollars: float,
    slippage: float,
    fee_per_side: float,
) -> OrbTrade:
    # apply slippage in the unfavorable direction
    exit_fill = raw_exit * (1 - slippage) if side == "long" else raw_exit * (1 + slippage)
    size = risk_dollars / or_.width
    sign = 1 if side == "long" else -1
    gross = sign * (exit_fill - entry_price) * size
    fees = (entry_price + exit_fill) * size * fee_per_side
    pnl = gross - fees
    r = sign * (exit_fill - entry_price) / or_.width
    return OrbTrade(
        symbol=symbol,
        date=or_.date,
        side=side,
        entry_time=entry_time,
        exit_time=entry_time,  # caller sets if needed; reason carries it
        entry_price=entry_price,
        exit_price=exit_fill,
        size=size,
        pnl=pnl,
        r_multiple=r,
        reason=reason,
    )


def run_orb_backtest(
    bars_by_symbol: dict[str, pd.DataFrame],
    starting_equity: float,
    risk_per_trade: float,
    max_concurrent_positions: int,
    max_position_fraction: float,
    or_minutes: int,
    target_r: float,
    flatten_minute_before_close: int,
    long_only: bool,
    fee_per_side: float,
    slippage: float,
) -> OrbBacktestResult:
    # collect all sessions across symbols, ordered by date
    sessions: list[tuple[pd.Timestamp, str, pd.DataFrame]] = []
    for sym, bars in bars_by_symbol.items():
        for date, sess in session_bars_by_day(bars).items():
            sessions.append((pd.Timestamp(date), sym, sess))
    sessions.sort(key=lambda x: (x[0], x[1]))

    equity = starting_equity
    trades: list[OrbTrade] = []
    daily_equity: dict[pd.Timestamp, float] = {}

    by_date: dict[pd.Timestamp, list[tuple[str, pd.DataFrame]]] = {}
    for d, s, b in sessions:
        by_date.setdefault(d, []).append((s, b))

    for date in sorted(by_date):
        day_sessions = by_date[date]
        # cap concurrent positions per day
        traded = 0
        for sym, sess in day_sessions:
            if traded >= max_concurrent_positions:
                break
            risk_dollars = equity * risk_per_trade
            max_position_dollars = equity * max_position_fraction
            t = _simulate_session(
                symbol=sym,
                session=sess,
                or_minutes=or_minutes,
                target_r=target_r,
                flatten_minute_before_close=flatten_minute_before_close,
                long_only=long_only,
                risk_dollars=risk_dollars,
                max_position_dollars=max_position_dollars,
                slippage=slippage,
                fee_per_side=fee_per_side,
            )
            if t is not None:
                equity += t.pnl
                trades.append(t)
                traded += 1
        daily_equity[date] = equity

    eq = pd.Series(daily_equity).sort_index()
    eq.index = pd.to_datetime(eq.index)
    return OrbBacktestResult(
        equity_curve=eq,
        trades=trades,
        stats=_compute_stats(eq, trades, starting_equity),
    )


def _compute_stats(equity: pd.Series, trades: list[OrbTrade], starting_equity: float) -> dict:
    if equity.empty:
        return {"final_equity": starting_equity, "n_trades": 0}

    final = float(equity.iloc[-1])
    total_return = final / starting_equity - 1
    days = max((equity.index[-1] - equity.index[0]).days, 1)
    years = days / 365.25
    cagr = (final / starting_equity) ** (1 / years) - 1 if final > 0 else -1.0

    daily_ret = equity.pct_change().dropna()
    sharpe = (
        float(daily_ret.mean() / daily_ret.std() * np.sqrt(252))
        if daily_ret.std() > 0
        else 0.0
    )

    rolling_max = equity.cummax()
    dd = equity / rolling_max - 1
    max_dd = float(dd.min())

    n = len(trades)
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    win_rate = len(wins) / n if n else 0.0
    avg_r = float(np.mean([t.r_multiple for t in trades])) if trades else 0.0
    gross_win = sum(t.pnl for t in wins)
    gross_loss = -sum(t.pnl for t in losses)
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else float("inf")

    return {
        "final_equity": final,
        "total_return": total_return,
        "cagr": cagr,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "n_trades": n,
        "win_rate": win_rate,
        "avg_r": avg_r,
        "profit_factor": profit_factor,
    }

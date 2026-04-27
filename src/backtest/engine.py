"""Event-driven daily-bar backtester with fees, slippage, and pluggable sizing."""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from src.strategy.donchian import atr, donchian_signals, realized_vol


@dataclass
class Trade:
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    entry_price: float
    exit_price: float
    size: float
    pnl: float
    return_pct: float
    reason: str

    def to_dict(self) -> dict:
        d = asdict(self)
        d["entry_date"] = self.entry_date.date().isoformat()
        d["exit_date"] = self.exit_date.date().isoformat()
        return d


@dataclass
class BacktestResult:
    equity_curve: pd.Series
    trades: list[Trade]
    stats: dict


def run_backtest(
    df: pd.DataFrame,
    entry_lookback: int,
    exit_lookback: int,
    atr_period: int,
    atr_stop_multiplier: float,
    trend_filter_ma: int | None,
    starting_equity: float,
    max_position_fraction: float,
    fee_per_side: float,
    slippage: float,
    sizing_method: str = "vol_target",
    risk_per_trade: float = 0.01,
    vol_target_daily: float = 0.01,
    vol_lookback: int = 20,
) -> BacktestResult:
    if sizing_method not in ("vol_target", "atr"):
        raise ValueError(f"sizing_method must be 'vol_target' or 'atr', got {sizing_method!r}")

    sig = donchian_signals(df, entry_lookback, exit_lookback, trend_filter_ma)
    sig["atr"] = atr(df, atr_period)
    sig["vol"] = realized_vol(df, vol_lookback)

    equity = starting_equity
    position = 0.0
    entry_price = 0.0
    stop_price = 0.0
    entry_date: pd.Timestamp | None = None
    trades: list[Trade] = []
    equity_curve: list[tuple[pd.Timestamp, float]] = []

    for date, row in sig.iterrows():
        # not enough lookback yet
        if pd.isna(row["entry_high"]) or pd.isna(row["atr"]) or pd.isna(row["exit_low"]):
            equity_curve.append((date, equity))
            continue

        just_exited = False

        # ---- exit logic ----
        if position > 0:
            exit_price: float | None = None
            reason = ""

            # gap-down through stop fills at open
            if row["open"] <= stop_price:
                exit_price = row["open"]
                reason = "stop_gap"
            elif row["low"] <= stop_price:
                exit_price = stop_price
                reason = "stop"
            elif row["open"] <= row["exit_low"]:
                exit_price = row["open"]
                reason = "donchian_exit_gap"
            elif row["low"] <= row["exit_low"]:
                exit_price = row["exit_low"]
                reason = "donchian_exit"

            if exit_price is not None:
                fill = exit_price * (1 - slippage)
                entry_fee = entry_price * position * fee_per_side
                exit_fee = fill * position * fee_per_side
                pnl = (fill - entry_price) * position - entry_fee - exit_fee
                equity += pnl
                trades.append(
                    Trade(
                        entry_date=entry_date,
                        exit_date=date,
                        entry_price=entry_price,
                        exit_price=fill,
                        size=position,
                        pnl=pnl,
                        return_pct=(fill / entry_price) - 1,
                        reason=reason,
                    )
                )
                position = 0.0
                just_exited = True

        # ---- entry logic ----
        if position == 0 and not just_exited:
            trend_ok = True
            if trend_filter_ma and "trend_ma" in sig.columns:
                if pd.isna(row["trend_ma"]):
                    trend_ok = False
                else:
                    trend_ok = row["close"] > row["trend_ma"]

            if trend_ok and row["high"] >= row["entry_high"]:
                # gap-up fills at open, otherwise at the breakout level
                raw_fill = max(row["entry_high"], row["open"])
                fill_price = raw_fill * (1 + slippage)
                stop_distance = row["atr"] * atr_stop_multiplier

                size = 0.0
                if stop_distance > 0:
                    if sizing_method == "vol_target":
                        # target a fixed % daily portfolio vol; scale notional by 1/realized_vol
                        if not pd.isna(row["vol"]) and row["vol"] > 0:
                            target_dollars = (vol_target_daily / row["vol"]) * equity
                            target_dollars = min(target_dollars, equity * max_position_fraction)
                            size = target_dollars / fill_price
                    else:  # atr
                        risk_dollars = equity * risk_per_trade
                        size = risk_dollars / stop_distance
                        max_size = (equity * max_position_fraction) / fill_price
                        size = min(size, max_size)

                if size > 0:
                    entry_price = fill_price
                    position = size
                    stop_price = fill_price - stop_distance
                    entry_date = date

        # ---- mark-to-market equity ----
        if position > 0:
            mtm = equity + position * (row["close"] - entry_price)
            equity_curve.append((date, mtm))
        else:
            equity_curve.append((date, equity))

    # close any open position at last bar
    if position > 0:
        last_date, last_row = sig.index[-1], sig.iloc[-1]
        fill = last_row["close"] * (1 - slippage)
        entry_fee = entry_price * position * fee_per_side
        exit_fee = fill * position * fee_per_side
        pnl = (fill - entry_price) * position - entry_fee - exit_fee
        equity += pnl
        trades.append(
            Trade(
                entry_date=entry_date,
                exit_date=last_date,
                entry_price=entry_price,
                exit_price=fill,
                size=position,
                pnl=pnl,
                return_pct=(fill / entry_price) - 1,
                reason="end_of_data",
            )
        )

    eq = pd.Series(
        [v for _, v in equity_curve],
        index=pd.to_datetime([d for d, _ in equity_curve]),
    )

    return BacktestResult(
        equity_curve=eq,
        trades=trades,
        stats=compute_stats(eq, trades, starting_equity),
    )


def compute_stats(equity: pd.Series, trades: list[Trade], starting_equity: float) -> dict:
    final = float(equity.iloc[-1])
    total_return = final / starting_equity - 1

    days = max((equity.index[-1] - equity.index[0]).days, 1)
    years = days / 365.25
    cagr = (final / starting_equity) ** (1 / years) - 1 if final > 0 else -1.0

    daily_ret = equity.pct_change().dropna()
    sharpe = (
        float(daily_ret.mean() / daily_ret.std() * np.sqrt(365))
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
    avg_win = float(np.mean([t.pnl for t in wins])) if wins else 0.0
    avg_loss = float(np.mean([t.pnl for t in losses])) if losses else 0.0
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
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor,
    }

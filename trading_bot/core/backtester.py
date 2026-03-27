from __future__ import annotations

import math

import pandas as pd

from trading_bot.core.confluence_engine import evaluate_symbol


def backtest_symbol(symbol: str, daily_data: pd.DataFrame, h1_data: pd.DataFrame, m30_data: pd.DataFrame | None = None, start_index: int = 220) -> dict:
    trades: list[dict] = []
    rejected = 0

    for index in range(start_index, len(h1_data) - 5):
        daily_slice = daily_data.iloc[: min(len(daily_data), max(50, math.ceil(index / 24) + 30))].reset_index(drop=True)
        h1_slice = h1_data.iloc[: index + 1].reset_index(drop=True)
        m30_slice = m30_data.iloc[: min(len(m30_data), (index + 1) * 2)].reset_index(drop=True) if m30_data is not None else None
        result = evaluate_symbol(symbol=symbol, daily_data=daily_slice, h1_data=h1_slice, m30_data=m30_slice)

        if result["status"] != "VALID_TRADE":
            rejected += 1
            continue

        outcome = _simulate_trade(result, h1_data.iloc[index + 1 : index + 6].reset_index(drop=True))
        trades.append(outcome)

    wins = [trade for trade in trades if trade["result"] == "WIN"]
    losses = [trade for trade in trades if trade["result"] == "LOSS"]
    gross_profit = sum(max(trade["rr"], 0) for trade in trades)
    gross_loss = abs(sum(min(trade["rr"], 0) for trade in trades))
    return {
        "symbol": symbol,
        "total_trades": len(trades),
        "rejected": rejected,
        "win_rate": round((len(wins) / len(trades)) * 100, 2) if trades else 0.0,
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss else round(gross_profit, 2),
        "drawdown": round(min([trade["rr"] for trade in trades], default=0), 2),
        "trades": trades[-25:],
    }


def _simulate_trade(setup: dict, future_h1: pd.DataFrame) -> dict:
    entry = float(setup["entry"])
    sl = float(setup["sl"])
    tp = float(setup["tp"])
    buy = setup["bias"] == "BUY"
    risk = abs(entry - sl) or 1.0

    result = "OPEN"
    rr = 0.0
    for _, candle in future_h1.iterrows():
        low = float(candle["low"])
        high = float(candle["high"])
        if buy:
            if low <= sl:
                result = "LOSS"
                rr = -1.0
                break
            if high >= tp:
                result = "WIN"
                rr = round(abs(tp - entry) / risk, 2)
                break
        else:
            if high >= sl:
                result = "LOSS"
                rr = -1.0
                break
            if low <= tp:
                result = "WIN"
                rr = round(abs(entry - tp) / risk, 2)
                break

    if result == "OPEN":
        result = "LOSS"
        rr = -0.25

    return {
        "strategy": "+".join(setup.get("strategies", [])),
        "result": result,
        "rr": rr,
        "confidence": setup.get("confidence"),
    }

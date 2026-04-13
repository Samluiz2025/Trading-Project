from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC

import pandas as pd

from trading_bot.core.confluence_engine import evaluate_symbol


@dataclass(frozen=True)
class BacktestWindow:
    start: pd.Timestamp
    end: pd.Timestamp


def backtest_symbol(
    *,
    symbol: str,
    daily_data: pd.DataFrame,
    h1_data: pd.DataFrame,
    ltf_data: pd.DataFrame,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    daily_data = _normalize_time_column(daily_data)
    h1_data = _normalize_time_column(h1_data)
    ltf_data = _normalize_time_column(ltf_data)

    window = _resolve_window(h1_data=h1_data, start_date=start_date, end_date=end_date)
    filtered_h1 = h1_data[(h1_data["time"] >= window.start) & (h1_data["time"] <= window.end)].reset_index(drop=True)
    if filtered_h1.empty:
        return _empty_backtest(symbol=symbol.upper(), window=window)

    trades: list[dict] = []
    rejected_reasons: dict[str, int] = {}
    active_trade: dict | None = None
    active_signature: str | None = None

    full_h1 = h1_data.reset_index(drop=True)
    full_ltf = ltf_data.reset_index(drop=True)
    full_daily = daily_data.reset_index(drop=True)

    for _, candle in filtered_h1.iterrows():
        current_time = pd.Timestamp(candle["time"])

        if active_trade is not None:
            close_result = _advance_active_trade(active_trade, full_ltf, current_time)
            if close_result is not None:
                trades.append(close_result)
                active_trade = None
                active_signature = None

        if active_trade is not None:
            continue

        daily_slice = full_daily[full_daily["time"] <= current_time].reset_index(drop=True)
        h1_slice = full_h1[full_h1["time"] <= current_time].reset_index(drop=True)
        m15_slice = full_ltf[full_ltf["time"] <= current_time].reset_index(drop=True)

        if len(daily_slice) < 30 or len(h1_slice) < 80 or len(m15_slice) < 120:
            continue

        result = evaluate_symbol(
            symbol=symbol,
            weekly_data=None,
            daily_data=daily_slice,
            h1_data=h1_slice,
            ltf_data=m15_slice,
            h4_data=None,
        )
        if result.get("status") != "VALID_TRADE":
            reason = str(result.get("reason") or result.get("message") or "No trade")
            rejected_reasons[reason] = rejected_reasons.get(reason, 0) + 1
            continue

        signature = _trade_signature(result)
        if signature == active_signature:
            continue

        active_trade = _open_trade(result=result, signal_time=current_time)
        active_signature = signature

    if active_trade is not None:
        timeout_trade = dict(active_trade)
        timeout_trade.update(
            {
                "RESULT": "LOSS",
                "R_GAINED": -1.0,
                "CLOSED_AT": pd.Timestamp(window.end).isoformat(),
                "CLOSE_REASON": "TIMEOUT",
            }
        )
        trades.append(timeout_trade)

    return _build_backtest_payload(
        symbol=symbol.upper(),
        window=window,
        trades=trades,
        rejected_reasons=rejected_reasons,
    )


def _resolve_window(*, h1_data: pd.DataFrame, start_date: str | None, end_date: str | None) -> BacktestWindow:
    start = pd.Timestamp(start_date) if start_date else pd.Timestamp(h1_data.iloc[0]["time"])
    end = pd.Timestamp(end_date) if end_date else pd.Timestamp(h1_data.iloc[-1]["time"])
    if start.tzinfo is None:
        start = start.tz_localize("UTC")
    else:
        start = start.tz_convert("UTC")
    if end.tzinfo is None:
        end = end.tz_localize("UTC")
    else:
        end = end.tz_convert("UTC")
    return BacktestWindow(start=start, end=end)


def _normalize_time_column(data: pd.DataFrame) -> pd.DataFrame:
    normalized = data.copy()
    normalized["time"] = pd.to_datetime(normalized["time"], utc=True)
    return normalized


def _trade_signature(result: dict) -> str:
    return "|".join(
        [
            str(result.get("pair") or ""),
            str(result.get("bias") or ""),
            f"{float(result.get('entry') or 0):.4f}",
            f"{float(result.get('sl') or 0):.4f}",
            f"{float(result.get('tp') or 0):.4f}",
        ]
    )


def _open_trade(*, result: dict, signal_time: pd.Timestamp) -> dict:
    return {
        "DATE": signal_time.isoformat(),
        "PAIR": str(result.get("pair") or ""),
        "SETUP TYPE": str(result.get("setup_type") or ""),
        "ENTRY": round(float(result["entry"]), 4),
        "STOP LOSS": round(float(result["sl"]), 4),
        "TAKE PROFIT": round(float(result["tp"]), 4),
        "RR": round(float(result.get("risk_reward_ratio") or 0), 2),
        "SESSION": str(result.get("session") or "").upper(),
        "BIAS": str(result.get("bias") or ""),
        "RESULT": "OPEN",
        "R_GAINED": 0.0,
        "SIGNAL_TIME": signal_time.isoformat(),
    }


def _advance_active_trade(active_trade: dict, ltf_data: pd.DataFrame, current_time: pd.Timestamp) -> dict | None:
    signal_time = pd.Timestamp(active_trade["SIGNAL_TIME"])
    future = ltf_data[(ltf_data["time"] > signal_time) & (ltf_data["time"] <= current_time)].reset_index(drop=True)
    if future.empty:
        return None

    entry = float(active_trade["ENTRY"])
    stop_loss = float(active_trade["STOP LOSS"])
    take_profit = float(active_trade["TAKE PROFIT"])
    side = str(active_trade.get("BIAS") or "").upper()
    entry_triggered = bool(active_trade.get("ENTRY_TRIGGERED"))

    for _, candle in future.iterrows():
        candle_time = pd.Timestamp(candle["time"])
        low = float(candle["low"])
        high = float(candle["high"])

        if not entry_triggered and low <= entry <= high:
            entry_triggered = True
            active_trade["ENTRY_TRIGGERED"] = True
            active_trade["ENTRY_TRIGGERED_AT"] = candle_time.isoformat()

        if not entry_triggered:
            continue

        if side == "BUY":
            if low <= stop_loss:
                return _close_trade(active_trade, result="LOSS", r_gained=-1.0, closed_at=candle_time, reason="STOP_LOSS_HIT")
            if high >= take_profit:
                return _close_trade(active_trade, result="WIN", r_gained=float(active_trade["RR"]), closed_at=candle_time, reason="TAKE_PROFIT_HIT")
        else:
            if high >= stop_loss:
                return _close_trade(active_trade, result="LOSS", r_gained=-1.0, closed_at=candle_time, reason="STOP_LOSS_HIT")
            if low <= take_profit:
                return _close_trade(active_trade, result="WIN", r_gained=float(active_trade["RR"]), closed_at=candle_time, reason="TAKE_PROFIT_HIT")

    return None


def _close_trade(active_trade: dict, *, result: str, r_gained: float, closed_at: pd.Timestamp, reason: str) -> dict:
    trade = dict(active_trade)
    trade["RESULT"] = result
    trade["R_GAINED"] = round(float(r_gained), 2)
    trade["CLOSED_AT"] = closed_at.isoformat()
    trade["CLOSE_REASON"] = reason
    return trade


def _build_backtest_payload(*, symbol: str, window: BacktestWindow, trades: list[dict], rejected_reasons: dict[str, int]) -> dict:
    wins = [trade for trade in trades if trade["RESULT"] == "WIN"]
    losses = [trade for trade in trades if trade["RESULT"] == "LOSS"]
    total_r = round(sum(float(trade["R_GAINED"]) for trade in trades), 2)
    average_r = round(total_r / len(trades), 2) if trades else 0.0
    profit = sum(float(trade["R_GAINED"]) for trade in wins)
    loss = abs(sum(float(trade["R_GAINED"]) for trade in losses))
    profit_factor = round(profit / loss, 2) if loss else round(profit, 2)
    max_drawdown = _max_drawdown(trades)

    trade_log = [
        {
            "DATE": trade["DATE"],
            "PAIR": trade["PAIR"],
            "SETUP TYPE": trade["SETUP TYPE"],
            "ENTRY": trade["ENTRY"],
            "STOP LOSS": trade["STOP LOSS"],
            "TAKE PROFIT": trade["TAKE PROFIT"],
            "RR": trade["RR"],
            "RESULT": trade["RESULT"],
            "R_GAINED": trade["R_GAINED"],
        }
        for trade in trades
    ]

    return {
        "symbol": symbol,
        "start_date": window.start.isoformat(),
        "end_date": window.end.isoformat(),
        "trade_log": trade_log,
        "final_summary": {
            "TOTAL TRADES": len(trades),
            "WINS": len(wins),
            "LOSSES": len(losses),
            "WIN RATE": round((len(wins) / len(trades)) * 100, 2) if trades else 0.0,
            "TOTAL R GAINED": total_r,
            "AVERAGE R PER TRADE": average_r,
            "MAX DRAWDOWN": max_drawdown,
            "PROFIT FACTOR": profit_factor,
        },
        "rejected_reasons": dict(sorted(rejected_reasons.items(), key=lambda item: item[1], reverse=True)),
    }


def _max_drawdown(trades: list[dict]) -> float:
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for trade in trades:
        equity += float(trade["R_GAINED"])
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity - peak)
    return round(abs(max_drawdown), 2)


def _empty_backtest(*, symbol: str, window: BacktestWindow) -> dict:
    return {
        "symbol": symbol,
        "start_date": window.start.isoformat(),
        "end_date": window.end.isoformat(),
        "trade_log": [],
        "final_summary": {
            "TOTAL TRADES": 0,
            "WINS": 0,
            "LOSSES": 0,
            "WIN RATE": 0.0,
            "TOTAL R GAINED": 0.0,
            "AVERAGE R PER TRADE": 0.0,
            "MAX DRAWDOWN": 0.0,
            "PROFIT FACTOR": 0.0,
        },
        "rejected_reasons": {},
    }

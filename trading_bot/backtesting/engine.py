"""Backtesting engine for concepts and strategy combinations."""

from __future__ import annotations

from typing import Callable

import pandas as pd

from trading_bot.concepts.base import ConceptSignal
from trading_bot.strategies.builder import (
    StrategyDefinition,
    collect_concept_signals,
    default_strategy_definitions,
    evaluate_strategies,
    infer_htf_bias,
)


def run_concept_backtest(
    dataframe: pd.DataFrame,
    symbol: str,
    timeframe: str,
    concept_name: str,
) -> dict:
    """Backtest a single concept detector across historical data."""

    concept_signals = collect_concept_signals(dataframe, symbol=symbol, timeframe=timeframe).get(concept_name, [])
    trades = [
        _simulate_trade(signal, dataframe)
        for signal in concept_signals
        if signal.index < len(dataframe) - 2
    ]
    return _build_backtest_summary(name=concept_name, trades=trades, mode="concept")


def run_strategy_backtest(
    dataframe: pd.DataFrame,
    symbol: str,
    timeframe: str,
    strategy_definitions: list[StrategyDefinition] | None = None,
) -> list[dict]:
    """Backtest each configured strategy combination on the dataframe."""

    definitions = strategy_definitions or default_strategy_definitions()
    results: list[dict] = []

    for strategy in definitions:
        trades: list[dict] = []
        for index in range(40, len(dataframe) - 2):
            window = dataframe.iloc[: index + 1].reset_index(drop=True)
            htf_bias = infer_htf_bias(window)
            evaluations = evaluate_strategies(
                dataframe=window,
                symbol=symbol,
                timeframe=timeframe,
                strategy_definitions=[strategy],
                htf_bias=htf_bias,
            )
            if not evaluations:
                continue

            evaluation = evaluations[-1]
            signal = _strategy_evaluation_to_signal(evaluation, index=index)
            trades.append(_simulate_trade(signal, dataframe))

        results.append(_build_backtest_summary(name=strategy.name, trades=trades, mode="strategy"))

    return results


def _strategy_evaluation_to_signal(evaluation: dict, index: int) -> ConceptSignal:
    signal_template = evaluation["signals"][-1]
    return ConceptSignal(
        concept=evaluation["strategy"],
        signal=evaluation["signal"],
        index=index,
        time=signal_template.time,
        entry=evaluation["entry"],
        stop_loss=evaluation["stop_loss"],
        take_profit=evaluation["take_profit"],
        confidence=evaluation["confidence"],
        metadata={"concepts": evaluation["concepts"]},
    )


def _simulate_trade(signal: ConceptSignal, dataframe: pd.DataFrame, lookahead: int = 10) -> dict:
    entry = signal.entry
    stop_loss = signal.stop_loss
    take_profit = signal.take_profit
    risk = abs(entry - stop_loss)
    rr = abs(take_profit - entry) / risk if risk else 0

    outcome = "open"
    pnl_r = 0.0
    exit_price = entry

    future_slice = dataframe.iloc[signal.index + 1 : signal.index + 1 + lookahead]
    for _, candle in future_slice.iterrows():
        high_price = float(candle["high"])
        low_price = float(candle["low"])

        if signal.signal == "BUY":
            if low_price <= stop_loss:
                outcome = "loss"
                pnl_r = -1.0
                exit_price = stop_loss
                break
            if high_price >= take_profit:
                outcome = "win"
                pnl_r = rr
                exit_price = take_profit
                break
        else:
            if high_price >= stop_loss:
                outcome = "loss"
                pnl_r = -1.0
                exit_price = stop_loss
                break
            if low_price <= take_profit:
                outcome = "win"
                pnl_r = rr
                exit_price = take_profit
                break

    if outcome == "open":
        final_close = float(future_slice.iloc[-1]["close"]) if not future_slice.empty else entry
        direction = 1 if signal.signal == "BUY" else -1
        pnl_r = direction * ((final_close - entry) / risk) if risk else 0.0
        outcome = "win" if pnl_r > 0 else "loss" if pnl_r < 0 else "breakeven"
        exit_price = final_close

    return {
        "signal": signal.signal,
        "time": signal.time,
        "entry": entry,
        "exit_price": round(exit_price, 4),
        "risk_reward_ratio": rr,
        "outcome": outcome,
        "pnl_r": pnl_r,
    }


def _build_backtest_summary(name: str, trades: list[dict], mode: str) -> dict:
    total_trades = len(trades)
    if total_trades == 0:
        return {
            "name": name,
            "mode": mode,
            "win_rate": 0.0,
            "risk_reward_ratio": 0.0,
            "profit_factor": 0.0,
            "drawdown": 0.0,
            "total_trades": 0,
            "net_r": 0.0,
        }

    wins = [trade for trade in trades if trade["outcome"] == "win"]
    losses = [trade for trade in trades if trade["outcome"] == "loss"]
    gross_profit = sum(max(trade["pnl_r"], 0) for trade in trades)
    gross_loss = abs(sum(min(trade["pnl_r"], 0) for trade in trades))
    average_rr = sum(trade["risk_reward_ratio"] for trade in trades) / total_trades
    profit_factor = gross_profit / gross_loss if gross_loss else gross_profit
    equity_curve = _build_equity_curve(trades)
    drawdown = _calculate_max_drawdown(equity_curve)

    return {
        "name": name,
        "mode": mode,
        "win_rate": round(len(wins) / total_trades * 100, 2),
        "risk_reward_ratio": round(average_rr, 2),
        "profit_factor": round(profit_factor, 2),
        "drawdown": round(drawdown, 2),
        "total_trades": total_trades,
        "net_r": round(sum(trade["pnl_r"] for trade in trades), 2),
    }


def _build_equity_curve(trades: list[dict]) -> list[float]:
    equity = 0.0
    curve = []
    for trade in trades:
        equity += float(trade["pnl_r"])
        curve.append(equity)
    return curve


def _calculate_max_drawdown(equity_curve: list[float]) -> float:
    peak = 0.0
    max_drawdown = 0.0
    for value in equity_curve:
        peak = max(peak, value)
        drawdown = peak - value
        max_drawdown = max(max_drawdown, drawdown)
    return max_drawdown

"""Strategy builder that combines multiple concept detectors."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

from trading_bot.concepts.base import ConceptSignal
from trading_bot.concepts.bos import detect_bos_signals
from trading_bot.concepts.fvg import detect_fvg_signals
from trading_bot.concepts.liquidity import detect_liquidity_sweep_signals
from trading_bot.concepts.mss import detect_mss_signals
from trading_bot.concepts.order_block import detect_order_block_signals
from trading_bot.core.market_structure import detect_market_structure, validate_ohlc_dataframe


ConceptDetector = Callable[..., list[ConceptSignal]]


@dataclass(frozen=True)
class StrategyDefinition:
    """Declarative strategy composed from one or more concepts."""

    name: str
    concepts: list[str]
    require_htf_alignment: bool = False
    minimum_confirmations: int = 2
    metadata: dict[str, str] = field(default_factory=dict)


def evaluate_strategies(
    dataframe: pd.DataFrame,
    symbol: str,
    timeframe: str,
    strategy_definitions: list[StrategyDefinition],
    htf_bias: str | None = None,
) -> list[dict]:
    """Evaluate configured concept combinations on the latest market state."""

    validate_ohlc_dataframe(dataframe)
    concept_signals = collect_concept_signals(dataframe=dataframe, symbol=symbol, timeframe=timeframe)
    evaluations: list[dict] = []

    for strategy in strategy_definitions:
        matched_signals = [concept_signals[concept] for concept in strategy.concepts if concept in concept_signals]
        if len(matched_signals) < strategy.minimum_confirmations:
            continue

        latest_signals = [signals[-1] for signals in matched_signals if signals]
        if len(latest_signals) < strategy.minimum_confirmations:
            continue

        directions = {signal.signal for signal in latest_signals}
        if len(directions) != 1:
            continue

        signal_side = next(iter(directions))
        if strategy.require_htf_alignment and htf_bias is not None:
            if signal_side == "BUY" and "bullish" not in htf_bias:
                continue
            if signal_side == "SELL" and "bearish" not in htf_bias:
                continue

        confidence = int(round(sum(signal.confidence for signal in latest_signals) / len(latest_signals)))
        reference_signal = latest_signals[-1]
        evaluations.append(
            {
                "strategy": strategy.name,
                "signal": signal_side,
                "confidence": confidence,
                "entry": reference_signal.entry,
                "stop_loss": reference_signal.stop_loss,
                "take_profit": reference_signal.take_profit,
                "concepts": strategy.concepts,
                "signals": latest_signals,
            }
        )

    return evaluations


def collect_concept_signals(dataframe: pd.DataFrame, symbol: str, timeframe: str) -> dict[str, list[ConceptSignal]]:
    """Run all available concept detectors on a dataframe."""

    return {
        "BOS": detect_bos_signals(dataframe),
        "MSS": detect_mss_signals(dataframe),
        "FVG": detect_fvg_signals(dataframe),
        "Liquidity": detect_liquidity_sweep_signals(dataframe),
        "OrderBlock": detect_order_block_signals(dataframe, symbol=symbol, timeframe=timeframe),
    }


def default_strategy_definitions() -> list[StrategyDefinition]:
    """Provide a starter library of strategy combinations."""

    return [
        StrategyDefinition(name="FVG + MSS", concepts=["FVG", "MSS"], minimum_confirmations=2),
        StrategyDefinition(name="BOS + Order Block", concepts=["BOS", "OrderBlock"], minimum_confirmations=2),
        StrategyDefinition(
            name="Liquidity Sweep + MSS + HTF Bias",
            concepts=["Liquidity", "MSS"],
            minimum_confirmations=2,
            require_htf_alignment=True,
        ),
    ]


def infer_htf_bias(dataframe: pd.DataFrame) -> str:
    """Infer a simple higher-timeframe bias from the same dataframe slice."""

    if len(dataframe) < 40:
        return detect_market_structure(dataframe)["trend"]
    sample = dataframe.iloc[-40:].reset_index(drop=True)
    return detect_market_structure(sample)["trend"]

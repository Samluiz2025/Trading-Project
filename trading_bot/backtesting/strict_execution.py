from __future__ import annotations

import pandas as pd

from trading_bot.core.strategy_execution_engine import ExecutionConfig, evaluate_strict_execution_setup


def backtest_strict_strategy(
    symbol: str,
    source: str = "auto",
    start_window: int = 120,
    end_window: int = 260,
    step: int = 5,
) -> dict:
    """
    Walk the strict execution engine through rolling windows.

    This is a lightweight interface for comparing how often the full confluence
    model appears and how often each concept participates in a valid setup.
    """

    results: list[dict] = []
    concept_hits = {
        "order_blocks": 0,
        "liquidity_sweeps": 0,
        "fvg": 0,
        "mss": 0,
        "bos": 0,
    }

    for primary_limit in range(start_window, end_window + 1, step):
        result = evaluate_strict_execution_setup(
            ExecutionConfig(
                symbol=symbol,
                source=source,
                primary_limit=primary_limit,
            )
        )
        results.append(result)

        details = result.get("details", {})
        if details.get("order_block", {}).get("confirmed") or details.get("breaker_block", {}).get("confirmed"):
            concept_hits["order_blocks"] += 1
        if details.get("liquidity", {}).get("confirmed"):
            concept_hits["liquidity_sweeps"] += 1
        if details.get("fvg", {}).get("confirmed"):
            concept_hits["fvg"] += 1
        if details.get("mss", {}).get("confirmed"):
            concept_hits["mss"] += 1
        if details.get("bos", {}).get("confirmed"):
            concept_hits["bos"] += 1

    high_setups = [result for result in results if result.get("setup") == "HIGH_PROBABILITY"]
    confidences = [float(result.get("confidence", 0)) for result in high_setups]

    return {
        "symbol": symbol.upper(),
        "tests_run": len(results),
        "high_probability_setups": len(high_setups),
        "average_confidence": round(sum(confidences) / len(confidences), 2) if confidences else 0.0,
        "concept_metrics": concept_hits,
    }

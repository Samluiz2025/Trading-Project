from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from trading_bot.core.filter_engine import filter_trade
from trading_bot.core.strategy_smc import SmcConfig, detect_daily_bias, generate_trade_setup as generate_smc_setup


def evaluate_symbol(symbol: str, daily_data: pd.DataFrame, h1_data: pd.DataFrame, m30_data: pd.DataFrame | None = None) -> dict:
    smc_result = generate_smc_setup(symbol=symbol, daily_data=daily_data, h1_data=h1_data, m30_data=m30_data, config=SmcConfig(symbol=symbol))
    daily_bias = detect_daily_bias(daily_data)["bias"]
    refinement = smc_result.get("details", {}).get("refinement", {})
    refinement_used = bool(refinement.get("used"))
    refinement_aligned = bool(refinement.get("aligned"))
    confidence_label, confidence_score = _refinement_confidence(refinement_used, refinement_aligned)

    if smc_result.get("status") != "VALID_TRADE":
        no_trade = filter_trade(smc_result)
        no_trade.update(
            {
                "pair": symbol.upper(),
                "daily_bias": daily_bias,
                "h1_bias": smc_result.get("details", {}).get("h1_structure", {}).get("trend"),
                "latest_price": round(float(h1_data.iloc[-1]["close"]), 4),
                "strategies_checked": ["SMC Continuation", "M30 Refinement"],
                "strategy_results": {
                    "smc": smc_result,
                    "refinement": refinement,
                },
                "confidence": confidence_label,
                "confidence_score": 0,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )
        return no_trade

    confluences = list(smc_result.get("confluences", []))
    strategies = ["SMC Continuation"]
    if refinement_used:
        strategies.append("M30 Refinement")
        if refinement_aligned:
            confluences.append("M30 Refinement Aligned")
        else:
            confluences.append("M30 Refinement Observed")

    payload = {
        "status": "VALID_TRADE",
        "message": "Valid swing setup available",
        "pair": symbol.upper(),
        "bias": smc_result["bias"],
        "entry": smc_result["entry"],
        "sl": smc_result["sl"],
        "tp": smc_result["tp"],
        "confidence": confidence_label,
        "confidence_score": confidence_score,
        "strategies": strategies,
        "confluences": _unique_ordered(confluences),
        "missing": [],
        "daily_bias": daily_bias,
        "h1_bias": smc_result.get("details", {}).get("h1_structure", {}).get("trend"),
        "latest_price": round(float(h1_data.iloc[-1]["close"]), 4),
        "strategy_results": {
            "smc": smc_result,
            "refinement": refinement,
        },
        "timestamp": datetime.now(UTC).isoformat(),
    }
    return payload


def _refinement_confidence(refinement_used: bool, refinement_aligned: bool) -> tuple[str, int]:
    if refinement_used and refinement_aligned:
        return "HIGH", 82
    return "LOW", 68


def _unique_ordered(items: list[str]) -> list[str]:
    seen = set()
    ordered: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered

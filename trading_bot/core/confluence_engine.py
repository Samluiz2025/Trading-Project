from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from trading_bot.core.filter_engine import filter_trade
from trading_bot.core.strategy_breakout import generate_trade_setup as generate_breakout_setup
from trading_bot.core.strategy_smc import SmcConfig, detect_daily_bias, generate_trade_setup as generate_smc_setup
from trading_bot.core.strategy_trend import generate_trade_setup as generate_trend_setup


def evaluate_symbol(symbol: str, daily_data: pd.DataFrame, h1_data: pd.DataFrame, m30_data: pd.DataFrame | None = None) -> dict:
    smc_result = generate_smc_setup(symbol=symbol, daily_data=daily_data, h1_data=h1_data, m30_data=m30_data, config=SmcConfig(symbol=symbol))
    if smc_result.get("status") != "VALID_TRADE":
        no_trade = filter_trade(smc_result)
        no_trade.update(
            {
                "pair": symbol.upper(),
                "daily_bias": detect_daily_bias(daily_data)["bias"],
                "h1_bias": smc_result.get("details", {}).get("h1_structure", {}).get("trend"),
                "latest_price": round(float(h1_data.iloc[-1]["close"]), 4),
                "strategies_checked": ["SMC", "TREND", "BREAKOUT"],
                "strategy_results": {
                    "smc": smc_result,
                    "trend": generate_trend_setup(symbol, h1_data, detect_daily_bias(daily_data)["bias"], m30_data),
                    "breakout": generate_breakout_setup(symbol, h1_data, detect_daily_bias(daily_data)["bias"], m30_data),
                },
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )
        return no_trade

    daily_bias = detect_daily_bias(daily_data)["bias"]
    trend_result = generate_trend_setup(symbol, h1_data, daily_bias, m30_data)
    breakout_result = generate_breakout_setup(symbol, h1_data, daily_bias, m30_data)

    aligned_valid = [smc_result]
    for item in (trend_result, breakout_result):
        if item.get("status") == "VALID_TRADE" and item.get("bias") == smc_result.get("bias"):
            aligned_valid.append(item)

    confidence_label = _confidence_label(len(aligned_valid))
    confidence_score = {"LOW": 60, "HIGH": 82, "VERY HIGH": 92}[confidence_label]

    confluences = list(smc_result.get("confluences", []))
    strategies = ["SMC"]
    if trend_result in aligned_valid:
        strategies.append("TREND")
        confluences.extend(trend_result.get("confluences", []))
    if breakout_result in aligned_valid:
        strategies.append("BREAKOUT")
        confluences.extend(breakout_result.get("confluences", []))

    payload = {
        "status": "VALID_TRADE",
        "message": "Valid multi-strategy setup available",
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
            "trend": trend_result,
            "breakout": breakout_result,
        },
        "timestamp": datetime.now(UTC).isoformat(),
    }
    return payload


def _confidence_label(strategy_count: int) -> str:
    if strategy_count >= 3:
        return "VERY HIGH"
    if strategy_count == 2:
        return "HIGH"
    return "LOW"


def _unique_ordered(items: list[str]) -> list[str]:
    seen = set()
    ordered: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered

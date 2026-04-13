from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from trading_bot.core.filter_engine import filter_trade
from trading_bot.core.strategy_htf_zone import build_htf_zone_reaction_config, generate_htf_zone_reaction_setup
from trading_bot.core.strategy_pullback import build_pullback_continuation_config, generate_pullback_continuation_setup
from trading_bot.core.strategy_registry import HTF_ZONE_STRATEGY, PULLBACK_STRATEGY, PRIMARY_STRATEGY
from trading_bot.core.strategy_strict_liquidity import build_strict_liquidity_config, generate_strict_liquidity_setup


def evaluate_symbol(
    symbol: str,
    weekly_data: pd.DataFrame | None,
    daily_data: pd.DataFrame,
    h1_data: pd.DataFrame,
    ltf_data: pd.DataFrame | None = None,
    h4_data: pd.DataFrame | None = None,
) -> dict:
    strict_result = generate_strict_liquidity_setup(
        symbol=symbol,
        daily_data=daily_data,
        h1_data=h1_data,
        m15_data=ltf_data,
        config=build_strict_liquidity_config(symbol),
    )
    pullback_result = generate_pullback_continuation_setup(
        symbol=symbol,
        daily_data=daily_data,
        h1_data=h1_data,
        m15_data=ltf_data,
        config=build_pullback_continuation_config(symbol),
    )
    htf_zone_result = generate_htf_zone_reaction_setup(
        symbol=symbol,
        daily_data=daily_data,
        h1_data=h1_data,
        m15_data=ltf_data,
        config=build_htf_zone_reaction_config(symbol),
    )
    strategy_results = {
        "strict_liquidity": strict_result,
        "pullback": pullback_result,
        "htf_zone": htf_zone_result,
    }
    latest_price = round(float((ltf_data if ltf_data is not None else h1_data).iloc[-1]["close"]), 4)
    selected = _select_best_result(strict_result, pullback_result, htf_zone_result)

    if selected.get("status") != "VALID_TRADE":
        candidate = filter_trade(selected)
        candidate.update(
            {
                "status": selected.get("status", candidate.get("status")),
                "message": selected.get("message", candidate.get("message")),
                "pair": str(symbol).upper(),
                "daily_bias": selected.get("daily_bias"),
                "h1_bias": selected.get("h1_bias"),
                "latest_price": latest_price,
                "strategies_checked": [PRIMARY_STRATEGY, PULLBACK_STRATEGY, HTF_ZONE_STRATEGY],
                "strategy_results": strategy_results,
                "confidence": selected.get("confidence", candidate.get("confidence", "LOW")),
                "confidence_score": selected.get("confidence_score", candidate.get("confidence_score", 0)),
                "risk_reward_ratio": selected.get("risk_reward_ratio"),
                "lifecycle": selected.get("lifecycle", candidate.get("lifecycle", "no_trade")),
                "stalker": selected.get("stalker"),
                "timestamp": datetime.now(UTC).isoformat(),
                "session": selected.get("session"),
                "setup_grade": selected.get("setup_grade"),
                "setup_type": selected.get("setup_type"),
                "invalidation": selected.get("invalidation"),
                "reason": selected.get("reason") or selected.get("message"),
                "analysis_context": selected.get("analysis_context", {}),
                "details": selected.get("details", {}),
                "confluences": selected.get("confluences", []),
                "strategies": selected.get("strategies", [selected.get("strategy") or PRIMARY_STRATEGY]),
                "strategy": selected.get("strategy", PRIMARY_STRATEGY),
                "bias": selected.get("bias"),
                "entry": selected.get("entry"),
                "sl": selected.get("sl"),
                "tp": selected.get("tp"),
            }
        )
        return candidate

    return {
        "status": "VALID_TRADE",
        "message": "Valid trade setup available",
        "pair": str(symbol).upper(),
        "bias": selected["bias"],
        "entry": selected["entry"],
        "sl": selected["sl"],
        "tp": selected["tp"],
        "risk_reward_ratio": selected.get("risk_reward_ratio"),
        "confidence": selected.get("confidence"),
        "confidence_score": selected.get("confidence_score"),
        "strategies": selected.get("strategies", [selected.get("strategy") or PRIMARY_STRATEGY]),
        "strategy": selected.get("strategy", PRIMARY_STRATEGY),
        "confluences": selected.get("confluences", []),
        "missing": [],
        "lifecycle": selected.get("lifecycle", "entry_reached"),
        "stalker": None,
        "daily_bias": selected.get("daily_bias"),
        "h1_bias": selected.get("h1_bias"),
        "latest_price": latest_price,
        "strategy_results": strategy_results,
        "strategies_checked": [PRIMARY_STRATEGY, PULLBACK_STRATEGY, HTF_ZONE_STRATEGY],
        "timestamp": datetime.now(UTC).isoformat(),
        "session": selected.get("session"),
        "setup_grade": selected.get("setup_grade"),
        "setup_type": selected.get("setup_type"),
        "invalidation": selected.get("invalidation"),
        "reason": selected.get("reason"),
        "analysis_context": selected.get("analysis_context", {}),
        "details": selected.get("details", {}),
    }


def _select_best_result(*results: dict) -> dict:
    ranked = sorted(results, key=_result_rank, reverse=True)
    return ranked[0]


def _result_rank(result: dict) -> tuple[int, float, float, float, int]:
    status = str(result.get("status") or "NO TRADE").upper()
    status_rank = {
        "VALID_TRADE": 3,
        "WAIT_CONFIRMATION": 2,
        "NO TRADE": 1,
    }.get(status, 0)
    grade_rank = {
        "A+": 4,
        "A": 3,
        "B": 2,
        "C": 1,
    }.get(str(result.get("setup_grade") or "").upper(), 0)
    confidence_score = float(result.get("confidence_score") or 0.0)
    risk_reward = float(result.get("risk_reward_ratio") or 0.0)
    confluence_count = len(result.get("confluences") or [])
    stalker_score = float((result.get("stalker") or {}).get("score") or 0.0)
    return (
        status_rank,
        grade_rank,
        confidence_score + stalker_score * 0.2,
        risk_reward,
        confluence_count,
    )

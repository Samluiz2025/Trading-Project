"""Ranking and adaptive weighting for strategy research."""

from __future__ import annotations


def rank_backtest_results(results: list[dict]) -> list[dict]:
    """Rank strategies by a blended profitability score."""

    ranked = []
    for result in results:
        ranking_score = _compute_ranking_score(result)
        ranked.append({**result, "ranking_score": round(ranking_score, 2)})

    ranked.sort(key=lambda item: item["ranking_score"], reverse=True)
    return ranked


def filter_top_ranked_strategies(
    ranked_results: list[dict],
    minimum_score: float = 0.75,
    minimum_trades: int = 5,
) -> list[dict]:
    """Keep only strategies that clear basic research thresholds."""

    return [
        result
        for result in ranked_results
        if result.get("ranking_score", 0) >= minimum_score and result.get("total_trades", 0) >= minimum_trades
    ]


def apply_adaptive_weights(ranked_results: list[dict]) -> list[dict]:
    """Increase weight for profitable strategies and decrease it for poor ones."""

    if not ranked_results:
        return []

    top_score = max(result["ranking_score"] for result in ranked_results) or 1
    adapted = []
    for result in ranked_results:
        normalized = result["ranking_score"] / top_score
        adaptive_weight = round(max(0.1, normalized), 2)
        adapted.append({**result, "adaptive_weight": adaptive_weight})
    return adapted


def _compute_ranking_score(result: dict) -> float:
    profit_factor = float(result.get("profit_factor", 0))
    win_rate = float(result.get("win_rate", 0)) / 100
    drawdown = float(result.get("drawdown", 0))
    total_trades = float(result.get("total_trades", 0))
    risk_reward = float(result.get("risk_reward_ratio", 0))

    trade_quality = min(total_trades / 20, 1.0)
    drawdown_penalty = 1 + max(drawdown, 0)
    return ((profit_factor * 0.45) + (win_rate * 0.35) + (risk_reward * 0.2)) * trade_quality / drawdown_penalty

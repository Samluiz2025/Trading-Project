"""Example usage for the strategy research and optimization engine."""

from __future__ import annotations

from pprint import pprint

from trading_bot.backtesting.engine import run_concept_backtest, run_strategy_backtest
from trading_bot.core.data_fetcher import FetchConfig, fetch_ohlc
from trading_bot.performance.ranking import apply_adaptive_weights, filter_top_ranked_strategies, rank_backtest_results
from trading_bot.performance.repository import save_performance_results
from trading_bot.strategies.builder import default_strategy_definitions, evaluate_strategies, infer_htf_bias


def main() -> None:
    dataframe = fetch_ohlc(FetchConfig(symbol="EURUSD", interval="1h", limit=400, source="yfinance"))

    print("Concept Backtest: FVG")
    concept_result = run_concept_backtest(dataframe, symbol="EURUSD", timeframe="1h", concept_name="FVG")
    pprint(concept_result)

    print("\nStrategy Backtests")
    strategy_results = run_strategy_backtest(
        dataframe,
        symbol="EURUSD",
        timeframe="1h",
        strategy_definitions=default_strategy_definitions(),
    )
    ranked = rank_backtest_results(strategy_results)
    filtered = filter_top_ranked_strategies(ranked)
    weighted = apply_adaptive_weights(filtered)
    save_performance_results(weighted)
    pprint(weighted)

    print("\nLatest Live Research Evaluation")
    live_evaluations = evaluate_strategies(
        dataframe=dataframe,
        symbol="EURUSD",
        timeframe="1h",
        strategy_definitions=default_strategy_definitions(),
        htf_bias=infer_htf_bias(dataframe),
    )
    pprint(live_evaluations)


if __name__ == "__main__":
    main()

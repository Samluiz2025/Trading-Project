"""Strategy combination utilities for research."""

from trading_bot.strategies.builder import StrategyDefinition, evaluate_strategies
from trading_bot.strategies.daily_h1_continuation import (
    ContinuationConfig,
    detect_bos,
    detect_daily_bias,
    detect_fvg,
    detect_inducement,
    detect_last_structure_break,
    detect_multiple_bos,
    detect_order_block,
    generate_trade_setup,
)

__all__ = [
    "StrategyDefinition",
    "evaluate_strategies",
    "ContinuationConfig",
    "detect_daily_bias",
    "detect_bos",
    "detect_multiple_bos",
    "detect_last_structure_break",
    "detect_inducement",
    "detect_order_block",
    "detect_fvg",
    "generate_trade_setup",
]

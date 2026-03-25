from __future__ import annotations

import argparse
from pprint import pprint

from trading_bot.core.strategy_execution_engine import (
    ExecutionConfig,
    evaluate_strict_execution_setup,
    format_high_setup_alert,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a single-symbol strict strategy simulation.")
    parser.add_argument("--symbol", default="EURUSD", help="Instrument to evaluate.")
    parser.add_argument(
        "--mode",
        default="test",
        choices=["test", "live"],
        help="Use mock data in test mode or live provider resolution in live mode.",
    )
    parser.add_argument(
        "--source",
        default=None,
        help="Optional explicit source override. Example: auto, yfinance, binance, mock",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source = args.source or ("mock" if args.mode == "test" else "auto")
    result = evaluate_strict_execution_setup(
        ExecutionConfig(
            symbol=args.symbol,
            source=source,
        )
    )

    pprint(result)
    alert_message = format_high_setup_alert(result)
    if alert_message:
        print()
        print(alert_message)
    else:
        print()
        print("[INFO] No full high-probability setup is active right now.")


if __name__ == "__main__":
    main()

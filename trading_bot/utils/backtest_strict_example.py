from __future__ import annotations

from pprint import pprint

from trading_bot.backtesting.strict_execution import backtest_strict_strategy


def main() -> None:
    result = backtest_strict_strategy(symbol="EURUSD", source="auto")
    pprint(result)


if __name__ == "__main__":
    main()

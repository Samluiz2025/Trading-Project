from __future__ import annotations

from pprint import pprint

from trading_bot.core.strategy_execution_engine import (
    ExecutionConfig,
    evaluate_strict_execution_setup,
    format_high_setup_alert,
)


def main() -> None:
    result = evaluate_strict_execution_setup(
        ExecutionConfig(
            symbol="EURUSD",
            source="auto",
        )
    )
    pprint(result)

    alert_message = format_high_setup_alert(result)
    if alert_message:
        print()
        print(alert_message)


if __name__ == "__main__":
    main()

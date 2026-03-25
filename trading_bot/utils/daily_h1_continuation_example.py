from __future__ import annotations

from pprint import pprint

from trading_bot.strategies.daily_h1_continuation import ContinuationConfig, generate_trade_setup


def main() -> None:
    result = generate_trade_setup(
        ContinuationConfig(
            symbol="EURUSD",
            source="auto",
        )
    )
    pprint(result)

    if result.get("entry") is not None and result.get("is_new_alert"):
        print("\n[ALERT]")
        pprint(
            {
                "pair": result["pair"],
                "bias": result["bias"],
                "entry": result["entry"],
                "sl": result["sl"],
                "tp": result["tp"],
                "confluences": result["confluences"],
                "confidence": result["confidence"],
                "timestamp": result["timestamp"],
            }
        )


if __name__ == "__main__":
    main()

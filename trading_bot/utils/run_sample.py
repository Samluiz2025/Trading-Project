"""Simple command-line runner for Phase 1 validation."""

from __future__ import annotations

from pprint import pprint

from trading_bot.core.data_fetcher import FetchConfig, fetch_ohlc
from trading_bot.core.market_structure import detect_market_structure


def main() -> None:
    """Fetch sample candles and print a clear market-bias summary."""

    candles = fetch_ohlc(FetchConfig(symbol="BTCUSDT", interval="1h", limit=120, source="auto"))
    structure = detect_market_structure(candles)

    print("Phase 1 Sample Run")
    print(f"Candles loaded: {len(candles)}")
    print(f"Latest close: {round(float(candles.iloc[-1]['close']), 4)}")
    print(f"Trend: {structure['trend']}")
    print("Structure summary:")
    pprint(
        {
            "last_HH": structure["last_HH"],
            "last_HL": structure["last_HL"],
            "last_LH": structure["last_LH"],
            "last_LL": structure["last_LL"],
            "swing_count": structure["swing_count"],
        }
    )


if __name__ == "__main__":
    main()

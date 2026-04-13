from __future__ import annotations

from pprint import pprint

from trading_bot.core.confluence_engine import evaluate_symbol
from trading_bot.core.data_fetcher import FetchConfig, fetch_ohlc


def main() -> None:
    symbol = "EURUSD"
    source = "auto"
    weekly_data = fetch_ohlc(FetchConfig(symbol=symbol, interval="1w", limit=160, source=source))
    daily_data = fetch_ohlc(FetchConfig(symbol=symbol, interval="1d", limit=220, source=source))
    h4_data = fetch_ohlc(FetchConfig(symbol=symbol, interval="4h", limit=220, source=source))
    h1_data = fetch_ohlc(FetchConfig(symbol=symbol, interval="1h", limit=320, source=source))
    ltf_data = fetch_ohlc(FetchConfig(symbol=symbol, interval="15m", limit=320, source=source))
    result = evaluate_symbol(symbol=symbol, weekly_data=weekly_data, daily_data=daily_data, h1_data=h1_data, ltf_data=ltf_data, h4_data=h4_data)
    pprint(result)


if __name__ == "__main__":
    main()

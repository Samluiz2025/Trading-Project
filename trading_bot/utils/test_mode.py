from __future__ import annotations

from pprint import pprint

from trading_bot.core.confluence_engine import evaluate_symbol
from trading_bot.core.data_fetcher import FetchConfig, fetch_ohlc


def main() -> None:
    symbol = "EURUSD"
    source = "auto"
    daily_data = fetch_ohlc(FetchConfig(symbol=symbol, interval="1d", limit=220, source=source))
    h1_data = fetch_ohlc(FetchConfig(symbol=symbol, interval="1h", limit=320, source=source))
    m30_data = fetch_ohlc(FetchConfig(symbol=symbol, interval="30m", limit=240, source=source))
    result = evaluate_symbol(symbol=symbol, daily_data=daily_data, h1_data=h1_data, m30_data=m30_data)
    pprint(result)


if __name__ == "__main__":
    main()

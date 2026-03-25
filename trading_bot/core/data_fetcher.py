from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd
import requests


BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"


@dataclass(frozen=True)
class FetchConfig:
    symbol: str = "BTCUSDT"
    interval: str = "1h"
    limit: int = 200
    source: Literal["binance", "mock", "auto"] = "auto"
    timeout_seconds: int = 10


class DataFetchError(Exception):
    """Raised when OHLC data cannot be retrieved from the requested source."""


def fetch_ohlc(config: FetchConfig | None = None) -> pd.DataFrame:
    """
    Fetch OHLC candles and return a normalized dataframe.

    The dataframe columns are:
    time, open, high, low, close

    If `source="auto"`, the function first tries Binance and falls back to
    deterministic mock data so the service remains runnable in restricted
    environments.
    """

    active_config = config or FetchConfig()

    if active_config.source == "mock":
        return _build_mock_ohlc(limit=active_config.limit)

    if active_config.source in {"binance", "auto"}:
        try:
            return _fetch_binance_ohlc(active_config)
        except Exception as exc:
            if active_config.source == "binance":
                raise DataFetchError("Failed to fetch OHLC data from Binance.") from exc

    return _build_mock_ohlc(limit=active_config.limit)


def _fetch_binance_ohlc(config: FetchConfig) -> pd.DataFrame:
    response = requests.get(
        BINANCE_KLINES_URL,
        params={
            "symbol": config.symbol.upper(),
            "interval": config.interval,
            "limit": config.limit,
        },
        timeout=config.timeout_seconds,
    )
    response.raise_for_status()

    raw_rows = response.json()
    if not isinstance(raw_rows, list) or not raw_rows:
        raise DataFetchError("Binance returned an empty or invalid response.")

    normalized_rows = [
        {
            "time": pd.to_datetime(row[0], unit="ms", utc=True),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
        }
        for row in raw_rows
    ]

    return pd.DataFrame(normalized_rows)


def _build_mock_ohlc(limit: int = 200) -> pd.DataFrame:
    """
    Create deterministic candles with alternating impulses and pullbacks.

    This keeps local development and tests stable even when external network
    access is unavailable.
    """

    rows: list[dict[str, float | pd.Timestamp]] = []
    start_time = pd.Timestamp("2026-01-01T00:00:00Z")
    base_price = 100.0

    for index in range(limit):
        trend_component = index * 0.35
        pullback_component = -0.9 if index % 6 in {3, 4} else 0.4
        wave_component = (index % 5) * 0.15

        candle_open = base_price + trend_component + (wave_component / 2)
        candle_close = candle_open + pullback_component + (0.1 if index % 2 == 0 else -0.05)
        candle_high = max(candle_open, candle_close) + 0.45
        candle_low = min(candle_open, candle_close) - 0.45

        rows.append(
            {
                "time": start_time + pd.Timedelta(hours=index),
                "open": round(candle_open, 4),
                "high": round(candle_high, 4),
                "low": round(candle_low, 4),
                "close": round(candle_close, 4),
            }
        )

    return pd.DataFrame(rows)

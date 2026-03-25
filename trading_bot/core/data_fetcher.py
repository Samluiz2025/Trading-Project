from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

import pandas as pd
import requests


BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
OANDA_CANDLES_URL_TEMPLATE = "{base_url}/v3/instruments/{instrument}/candles"
OANDA_DEFAULT_BASE_URL = "https://api-fxpractice.oanda.com"

SupportedSource = Literal["binance", "mock", "auto", "yfinance", "oanda"]


@dataclass(frozen=True)
class FetchConfig:
    symbol: str = "BTCUSDT"
    interval: str = "1h"
    limit: int = 200
    source: SupportedSource = "auto"
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

    if active_config.source == "yfinance":
        return _fetch_yfinance_ohlc(active_config)

    if active_config.source == "oanda":
        return _fetch_oanda_ohlc(active_config)

    if active_config.source in {"binance", "auto"}:
        try:
            return _fetch_binance_ohlc(active_config)
        except Exception as exc:
            if active_config.source == "binance":
                raise DataFetchError("Failed to fetch OHLC data from Binance.") from exc

    if active_config.source == "auto":
        for fallback_fetcher in (_fetch_yfinance_ohlc, _fetch_oanda_ohlc):
            try:
                return fallback_fetcher(active_config)
            except Exception:
                continue

    return _build_mock_ohlc(limit=active_config.limit)


def _fetch_binance_ohlc(config: FetchConfig) -> pd.DataFrame:
    response = requests.get(
        BINANCE_KLINES_URL,
        params={
            "symbol": _normalize_binance_symbol(config.symbol),
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


def _fetch_yfinance_ohlc(config: FetchConfig) -> pd.DataFrame:
    """Fetch OHLC data from Yahoo Finance via the yfinance package."""

    try:
        import yfinance as yf
    except ImportError as exc:
        raise DataFetchError("yfinance is not installed. Run: pip install yfinance") from exc

    ticker = _normalize_yfinance_symbol(config.symbol)
    fetch_interval, resample_interval = _map_yfinance_interval(config.interval)
    history = yf.Ticker(ticker).history(period="max", interval=fetch_interval, auto_adjust=False)
    if history.empty:
        raise DataFetchError(f"Yahoo Finance returned no data for symbol '{ticker}'.")

    history = history.tail(config.limit).reset_index()
    time_column = "Datetime" if "Datetime" in history.columns else "Date"
    dataframe = history.rename(
        columns={
            time_column: "time",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
        }
    )[["time", "open", "high", "low", "close"]].copy()

    dataframe["time"] = pd.to_datetime(dataframe["time"], utc=True)
    for column in ["open", "high", "low", "close"]:
        dataframe[column] = dataframe[column].astype(float)

    if resample_interval is not None:
        dataframe = _resample_ohlc(dataframe, resample_interval)

    dataframe = dataframe.tail(config.limit).reset_index(drop=True)
    return dataframe


def _fetch_oanda_ohlc(config: FetchConfig) -> pd.DataFrame:
    """
    Fetch OHLC data from OANDA's v20 REST API.

    This requires an OANDA personal access token. The default base URL uses the
    practice environment; override with OANDA_API_URL for live accounts.
    """

    token = os.getenv("OANDA_ACCESS_TOKEN")
    if not token:
        raise DataFetchError("OANDA_ACCESS_TOKEN is not set.")

    instrument = _normalize_oanda_symbol(config.symbol)
    granularity = _map_oanda_interval(config.interval)
    base_url = os.getenv("OANDA_API_URL", OANDA_DEFAULT_BASE_URL).rstrip("/")
    url = OANDA_CANDLES_URL_TEMPLATE.format(base_url=base_url, instrument=instrument)

    response = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept-Datetime-Format": "RFC3339",
        },
        params={
            "price": "M",
            "granularity": granularity,
            "count": min(config.limit, 5000),
        },
        timeout=config.timeout_seconds,
    )
    response.raise_for_status()

    payload = response.json()
    candles = payload.get("candles", [])
    if not candles:
        raise DataFetchError(f"OANDA returned no candles for instrument '{instrument}'.")

    normalized_rows = [
        {
            "time": pd.to_datetime(candle["time"], utc=True),
            "open": float(candle["mid"]["o"]),
            "high": float(candle["mid"]["h"]),
            "low": float(candle["mid"]["l"]),
            "close": float(candle["mid"]["c"]),
        }
        for candle in candles
        if candle.get("complete") and candle.get("mid")
    ]
    if not normalized_rows:
        raise DataFetchError(f"OANDA returned no completed candles for instrument '{instrument}'.")

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


def _normalize_binance_symbol(symbol: str) -> str:
    return symbol.replace("/", "").replace("-", "").replace("_", "").upper()


def _normalize_yfinance_symbol(symbol: str) -> str:
    cleaned = symbol.strip().upper()
    if cleaned.endswith("=X"):
        return cleaned
    if "_" in cleaned:
        base, quote = cleaned.split("_", maxsplit=1)
        return f"{base}{quote}=X"
    if "/" in cleaned:
        base, quote = cleaned.split("/", maxsplit=1)
        return f"{base}{quote}=X"
    if len(cleaned) == 6 and cleaned.isalpha():
        return f"{cleaned}=X"
    return cleaned


def _normalize_oanda_symbol(symbol: str) -> str:
    cleaned = symbol.strip().upper()
    if "_" in cleaned:
        return cleaned
    if "/" in cleaned:
        base, quote = cleaned.split("/", maxsplit=1)
        return f"{base}_{quote}"
    if len(cleaned) == 6 and cleaned.isalpha():
        return f"{cleaned[:3]}_{cleaned[3:]}"
    return cleaned


def _map_yfinance_interval(interval: str) -> tuple[str, str | None]:
    mapping = {
        "1m": ("1m", None),
        "5m": ("5m", None),
        "15m": ("15m", None),
        "30m": ("30m", None),
        "1h": ("60m", None),
        "4h": ("60m", "4h"),
        "1d": ("1d", None),
        "1w": ("1d", "1w"),
    }
    if interval not in mapping:
        raise DataFetchError(f"Interval '{interval}' is not supported by yfinance.")
    return mapping[interval]


def _map_oanda_interval(interval: str) -> str:
    mapping = {
        "1m": "M1",
        "5m": "M5",
        "15m": "M15",
        "30m": "M30",
        "1h": "H1",
        "4h": "H4",
        "1d": "D",
        "1w": "W",
    }
    if interval not in mapping:
        raise DataFetchError(f"Interval '{interval}' is not supported by OANDA.")
    return mapping[interval]


def _resample_ohlc(dataframe: pd.DataFrame, frequency: str) -> pd.DataFrame:
    """Aggregate OHLC candles to a higher timeframe."""

    resampled = (
        dataframe.set_index("time")
        .resample(frequency)
        .agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
            }
        )
        .dropna()
        .reset_index()
    )
    return resampled

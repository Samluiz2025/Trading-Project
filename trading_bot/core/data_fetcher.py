from __future__ import annotations

import os
from io import StringIO
from dataclasses import dataclass
from typing import Literal

import pandas as pd
import requests


BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
OANDA_CANDLES_URL_TEMPLATE = "{base_url}/v3/instruments/{instrument}/candles"
OANDA_DEFAULT_BASE_URL = "https://api-fxpractice.oanda.com"
ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"
TWELVE_DATA_URL = "https://api.twelvedata.com/time_series"

SupportedSource = Literal["binance", "mock", "auto", "yfinance", "oanda", "alphavantage", "twelvedata", "stooq"]


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

    if active_config.source == "alphavantage":
        return _fetch_alphavantage_ohlc(active_config)

    if active_config.source == "twelvedata":
        return _fetch_twelvedata_ohlc(active_config)

    if active_config.source == "stooq":
        return _fetch_stooq_ohlc(active_config)

    if active_config.source in {"binance", "auto"}:
        try:
            return _fetch_binance_ohlc(active_config)
        except Exception as exc:
            if active_config.source == "binance":
                raise DataFetchError("Failed to fetch OHLC data from Binance.") from exc

    if active_config.source == "auto":
        for fallback_fetcher in (
            _fetch_yfinance_ohlc,
            _fetch_stooq_ohlc,
            _fetch_twelvedata_ohlc,
            _fetch_alphavantage_ohlc,
            _fetch_oanda_ohlc,
        ):
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


def _fetch_alphavantage_ohlc(config: FetchConfig) -> pd.DataFrame:
    """Fetch OHLC candles from Alpha Vantage."""

    api_key = os.getenv("ALPHA_VANTAGE_API_KEY")
    if not api_key:
        raise DataFetchError("ALPHA_VANTAGE_API_KEY is not set.")

    asset_type = _detect_asset_type(config.symbol)
    if asset_type == "forex":
        return _fetch_alphavantage_forex(config, api_key)
    if asset_type == "crypto":
        return _fetch_alphavantage_crypto(config, api_key)
    return _fetch_alphavantage_symbol_time_series(config, api_key)


def _fetch_twelvedata_ohlc(config: FetchConfig) -> pd.DataFrame:
    """Fetch OHLC candles from Twelve Data."""

    api_key = os.getenv("TWELVEDATA_API_KEY")
    if not api_key:
        raise DataFetchError("TWELVEDATA_API_KEY is not set.")

    symbol = _normalize_twelvedata_symbol(config.symbol)
    interval = _map_twelvedata_interval(config.interval)

    response = requests.get(
        TWELVE_DATA_URL,
        params={
            "symbol": symbol,
            "interval": interval,
            "outputsize": config.limit,
            "timezone": "UTC",
            "apikey": api_key,
        },
        timeout=config.timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") == "error":
        raise DataFetchError(payload.get("message", f"Twelve Data failed for symbol '{symbol}'."))

    values = payload.get("values", [])
    if not values:
        raise DataFetchError(f"Twelve Data returned no data for symbol '{symbol}'.")

    normalized_rows = [
        {
            "time": pd.to_datetime(item["datetime"], utc=True),
            "open": float(item["open"]),
            "high": float(item["high"]),
            "low": float(item["low"]),
            "close": float(item["close"]),
        }
        for item in reversed(values)
    ]
    return pd.DataFrame(normalized_rows)


def _fetch_stooq_ohlc(config: FetchConfig) -> pd.DataFrame:
    """
    Fetch OHLC data from Stooq's public CSV download.

    This source is best suited for daily or higher intervals.
    """

    interval = _map_stooq_interval(config.interval)
    symbol = _normalize_stooq_symbol(config.symbol)
    response = requests.get(
        "https://stooq.com/q/d/l/",
        params={
            "s": symbol,
            "i": interval,
        },
        timeout=config.timeout_seconds,
    )
    response.raise_for_status()

    dataframe = pd.read_csv(StringIO(response.text))
    if dataframe.empty or "Date" not in dataframe.columns:
        raise DataFetchError(f"Stooq returned no data for symbol '{symbol}'.")

    dataframe = dataframe.rename(
        columns={
            "Date": "time",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
        }
    )[["time", "open", "high", "low", "close"]].copy()
    dataframe = dataframe[dataframe["open"] != 0]
    if dataframe.empty:
        raise DataFetchError(f"Stooq returned unusable data for symbol '{symbol}'.")

    dataframe["time"] = pd.to_datetime(dataframe["time"], utc=True)
    for column in ["open", "high", "low", "close"]:
        dataframe[column] = dataframe[column].astype(float)

    return dataframe.tail(config.limit).reset_index(drop=True)


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
    commodity_mapping = {
        "XAUUSD": "GC=F",
        "XAU/USD": "GC=F",
        "XAU_USD": "GC=F",
        "USOIL": "CL=F",
        "WTI": "CL=F",
        "XTIUSD": "CL=F",
        "BRENT": "BZ=F",
        "UKOIL": "BZ=F",
        "XBRUSD": "BZ=F",
    }
    index_mapping = {
        "SPX": "^GSPC",
        "SP500": "^GSPC",
        "US500": "^GSPC",
        "DJI": "^DJI",
        "US30": "^DJI",
        "NASDAQ": "^IXIC",
        "NAS100": "^NDX",
        "NDX": "^NDX",
        "GER40": "^GDAXI",
        "DAX": "^GDAXI",
        "UK100": "^FTSE",
        "FTSE": "^FTSE",
        "JP225": "^N225",
        "NIKKEI": "^N225",
    }
    if cleaned in commodity_mapping:
        return commodity_mapping[cleaned]
    if cleaned in index_mapping:
        return index_mapping[cleaned]
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


def _normalize_twelvedata_symbol(symbol: str) -> str:
    cleaned = symbol.strip().upper()
    mapping = {
        "XAUUSD": "XAU/USD",
        "XAU_USD": "XAU/USD",
        "USOIL": "USOIL",
        "WTI": "USOIL",
        "SPX": "SPX",
        "SP500": "SPX",
        "US500": "SPX",
        "DJI": "DJI",
        "US30": "DJI",
        "NASDAQ": "IXIC",
        "NAS100": "NDX",
        "GER40": "GDAXI",
        "UK100": "FTSE",
        "JP225": "N225",
    }
    if cleaned in mapping:
        return mapping[cleaned]
    if "_" in cleaned:
        base, quote = cleaned.split("_", maxsplit=1)
        return f"{base}/{quote}"
    return cleaned


def _normalize_stooq_symbol(symbol: str) -> str:
    cleaned = symbol.strip().upper()
    mapping = {
        "SPX": "^SPX",
        "SP500": "^SPX",
        "US500": "^SPX",
        "DJI": "^DJI",
        "US30": "^DJI",
        "NASDAQ": "^IXIC",
        "NAS100": "^NDX",
        "NDX": "^NDX",
        "DAX": "^DAX",
        "GER40": "^DAX",
        "FTSE": "^UKX",
        "UK100": "^UKX",
        "NIKKEI": "^NKX",
        "JP225": "^NKX",
        "XAUUSD": "gold",
        "XAU/USD": "gold",
        "XAU_USD": "gold",
        "USOIL": "cl.f",
        "WTI": "cl.f",
        "BRENT": "cb.f",
    }
    if cleaned in mapping:
        return mapping[cleaned].lower()
    if len(cleaned) == 6 and cleaned.isalpha():
        return cleaned.lower()
    return cleaned.lower()


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


def _map_twelvedata_interval(interval: str) -> str:
    mapping = {
        "1m": "1min",
        "5m": "5min",
        "15m": "15min",
        "30m": "30min",
        "1h": "1h",
        "4h": "4h",
        "1d": "1day",
        "1w": "1week",
    }
    if interval not in mapping:
        raise DataFetchError(f"Interval '{interval}' is not supported by Twelve Data.")
    return mapping[interval]


def _map_stooq_interval(interval: str) -> str:
    mapping = {
        "1d": "d",
        "1w": "w",
    }
    if interval not in mapping:
        raise DataFetchError("Stooq currently supports only 1d and 1w intervals in this backend.")
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


def _detect_asset_type(symbol: str) -> str:
    cleaned = symbol.strip().upper().replace("/", "").replace("_", "")
    if cleaned.endswith("USDT"):
        return "crypto"
    if len(cleaned) == 6 and cleaned.isalpha():
        return "forex"
    return "symbol"


def _split_base_quote(symbol: str) -> tuple[str, str]:
    cleaned = symbol.strip().upper().replace("/", "").replace("_", "")
    if cleaned.endswith("USDT"):
        return cleaned[:-4], "USD"
    if len(cleaned) >= 6:
        return cleaned[:3], cleaned[3:6]
    raise DataFetchError(f"Unable to infer base and quote currencies from symbol '{symbol}'.")


def _fetch_alphavantage_forex(config: FetchConfig, api_key: str) -> pd.DataFrame:
    base_symbol, quote_symbol = _split_base_quote(config.symbol)
    function, interval, resample_interval = _map_alphavantage_forex_request(config.interval)

    response = requests.get(
        ALPHA_VANTAGE_URL,
        params={
            "function": function,
            "from_symbol": base_symbol,
            "to_symbol": quote_symbol,
            **({"interval": interval} if interval else {}),
            "outputsize": "compact",
            "apikey": api_key,
        },
        timeout=config.timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    series_key = _find_alphavantage_series_key(payload)
    if series_key is None:
        raise DataFetchError(payload.get("Note") or payload.get("Information") or "Alpha Vantage forex request failed.")

    dataframe = _alphavantage_series_to_dataframe(payload[series_key], limit=config.limit)
    if resample_interval is not None:
        dataframe = _resample_ohlc(dataframe, resample_interval).tail(config.limit).reset_index(drop=True)
    return dataframe


def _fetch_alphavantage_crypto(config: FetchConfig, api_key: str) -> pd.DataFrame:
    base_symbol, quote_symbol = _split_base_quote(config.symbol)
    function, interval, resample_interval = _map_alphavantage_crypto_request(config.interval)

    response = requests.get(
        ALPHA_VANTAGE_URL,
        params={
            "function": function,
            "symbol": base_symbol,
            "market": quote_symbol,
            **({"interval": interval} if interval else {}),
            "outputsize": "compact",
            "apikey": api_key,
        },
        timeout=config.timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    series_key = _find_alphavantage_series_key(payload)
    if series_key is None:
        raise DataFetchError(payload.get("Note") or payload.get("Information") or "Alpha Vantage crypto request failed.")

    dataframe = _alphavantage_series_to_dataframe(payload[series_key], limit=config.limit)
    if resample_interval is not None:
        dataframe = _resample_ohlc(dataframe, resample_interval).tail(config.limit).reset_index(drop=True)
    return dataframe


def _fetch_alphavantage_symbol_time_series(config: FetchConfig, api_key: str) -> pd.DataFrame:
    function, interval, resample_interval = _map_alphavantage_symbol_request(config.interval)

    response = requests.get(
        ALPHA_VANTAGE_URL,
        params={
            "function": function,
            "symbol": _normalize_alphavantage_symbol(config.symbol),
            **({"interval": interval} if interval else {}),
            "outputsize": "compact",
            "apikey": api_key,
        },
        timeout=config.timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    series_key = _find_alphavantage_series_key(payload)
    if series_key is None:
        raise DataFetchError(payload.get("Note") or payload.get("Information") or "Alpha Vantage symbol request failed.")

    dataframe = _alphavantage_series_to_dataframe(payload[series_key], limit=config.limit)
    if resample_interval is not None:
        dataframe = _resample_ohlc(dataframe, resample_interval).tail(config.limit).reset_index(drop=True)
    return dataframe


def _normalize_alphavantage_symbol(symbol: str) -> str:
    cleaned = symbol.strip().upper()
    if cleaned in {"SPX", "SP500", "US500"}:
        return "SPY"
    if cleaned in {"DJI", "US30"}:
        return "DIA"
    if cleaned in {"NASDAQ", "NAS100", "NDX"}:
        return "QQQ"
    if cleaned in {"GER40", "DAX"}:
        return "EXS1.DE"
    if cleaned in {"UK100", "FTSE"}:
        return "ISF.LON"
    if cleaned in {"JP225", "NIKKEI"}:
        return "EWJ"
    if cleaned in {"XAUUSD", "XAU/USD", "XAU_USD"}:
        return "GLD"
    if cleaned in {"USOIL", "WTI"}:
        return "USO"
    return cleaned


def _map_alphavantage_symbol_request(interval: str) -> tuple[str, str | None, str | None]:
    mapping = {
        "15m": ("TIME_SERIES_INTRADAY", "15min", None),
        "30m": ("TIME_SERIES_INTRADAY", "30min", None),
        "1h": ("TIME_SERIES_INTRADAY", "60min", None),
        "4h": ("TIME_SERIES_INTRADAY", "60min", "4h"),
        "1d": ("TIME_SERIES_DAILY", None, None),
        "1w": ("TIME_SERIES_WEEKLY", None, None),
    }
    if interval not in mapping:
        raise DataFetchError(f"Interval '{interval}' is not supported by Alpha Vantage symbols.")
    return mapping[interval]


def _map_alphavantage_forex_request(interval: str) -> tuple[str, str | None, str | None]:
    mapping = {
        "15m": ("FX_INTRADAY", "15min", None),
        "30m": ("FX_INTRADAY", "30min", None),
        "1h": ("FX_INTRADAY", "60min", None),
        "4h": ("FX_INTRADAY", "60min", "4h"),
        "1d": ("FX_DAILY", None, None),
        "1w": ("FX_WEEKLY", None, None),
    }
    if interval not in mapping:
        raise DataFetchError(f"Interval '{interval}' is not supported by Alpha Vantage forex.")
    return mapping[interval]


def _map_alphavantage_crypto_request(interval: str) -> tuple[str, str | None, str | None]:
    mapping = {
        "15m": ("CRYPTO_INTRADAY", "15min", None),
        "30m": ("CRYPTO_INTRADAY", "30min", None),
        "1h": ("CRYPTO_INTRADAY", "60min", None),
        "4h": ("CRYPTO_INTRADAY", "60min", "4h"),
        "1d": ("DIGITAL_CURRENCY_DAILY", None, None),
        "1w": ("DIGITAL_CURRENCY_WEEKLY", None, None),
    }
    if interval not in mapping:
        raise DataFetchError(f"Interval '{interval}' is not supported by Alpha Vantage crypto.")
    return mapping[interval]


def _find_alphavantage_series_key(payload: dict) -> str | None:
    for key in payload:
        normalized = key.lower()
        if "time series" in normalized or "digital currency" in normalized:
            return key
    return None


def _alphavantage_series_to_dataframe(series_payload: dict, limit: int) -> pd.DataFrame:
    rows = []
    for timestamp, values in series_payload.items():
        rows.append(
            {
                "time": pd.to_datetime(timestamp, utc=True),
                "open": float(_get_alphavantage_value(values, "open")),
                "high": float(_get_alphavantage_value(values, "high")),
                "low": float(_get_alphavantage_value(values, "low")),
                "close": float(_get_alphavantage_value(values, "close")),
            }
        )

    if not rows:
        raise DataFetchError("Alpha Vantage returned an empty time series.")

    dataframe = pd.DataFrame(rows).sort_values("time").tail(limit).reset_index(drop=True)
    return dataframe


def _get_alphavantage_value(values: dict, field_name: str) -> str:
    for key, value in values.items():
        if field_name in key.lower():
            return value
    raise DataFetchError(f"Alpha Vantage response is missing '{field_name}'.")

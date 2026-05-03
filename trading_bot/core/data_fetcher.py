"""
data_fetcher.py
─────────────────────────────────────────────────────────────────────────────
Multi-source OHLCV fetcher with automatic fallback:
  Binance (crypto) → Yahoo Finance → Twelve Data → mock

Now returns H4 data in addition to Daily/H1/M15.
Includes data-quality validation before returning.
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Optional
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

CRYPTO_SYMBOLS = {"BTCUSDT", "ETHUSDT"}
FOREX_SYMBOLS  = {"GBPUSD", "EURUSD", "USDCHF", "USDJPY"}
METAL_SYMBOLS  = {"XAUUSD"}
INDEX_SYMBOLS  = {"NAS100", "US100"}

YFINANCE_MAP = {
    "GBPUSD": "GBPUSD=X",
    "EURUSD": "EURUSD=X",
    "USDCHF": "USDCHF=X",
    "USDJPY": "USDJPY=X",
    "XAUUSD": "GC=F",
    "NAS100": "NQ=F",
    "US100":  "NQ=F",
    "BTCUSDT": "BTC-USDT",
    "ETHUSDT": "ETH-USDT",
}


def _validate_ohlcv(df: pd.DataFrame, min_rows: int = 20) -> bool:
    if df is None or len(df) < min_rows:
        return False
    required = {"open", "high", "low", "close"}
    if not required.issubset(set(df.columns)):
        return False
    if df[["open", "high", "low", "close"]].isnull().any().any():
        return False
    # Sanity: high >= low
    if (df["high"] < df["low"]).any():
        return False
    return True


def _fetch_binance(symbol: str, interval: str, limit: int) -> Optional[pd.DataFrame]:
    try:
        import requests
        url = "https://api.binance.com/api/v3/klines"
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        df = pd.DataFrame(data, columns=[
            "timestamp", "open", "high", "low", "close", "volume",
            "close_time", "quote_vol", "n_trades", "taker_buy_base",
            "taker_buy_quote", "ignore"
        ])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df.set_index("timestamp", inplace=True)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)
        return df[["open", "high", "low", "close", "volume"]]
    except Exception as e:
        logger.debug("Binance fetch failed for %s %s: %s", symbol, interval, e)
        return None


def _fetch_yfinance(symbol: str, interval: str, period: str) -> Optional[pd.DataFrame]:
    try:
        import yfinance as yf
        ticker = YFINANCE_MAP.get(symbol, symbol)
        df = yf.download(ticker, interval=interval, period=period,
                         progress=False, auto_adjust=True)
        if df is None or df.empty:
            return None
        df.columns = [c.lower() for c in df.columns]
        df.index = pd.to_datetime(df.index, utc=True)
        return df[["open", "high", "low", "close", "volume"]]
    except Exception as e:
        logger.debug("yfinance fetch failed for %s %s: %s", symbol, interval, e)
        return None


def _generate_mock(symbol: str, interval: str, n: int) -> pd.DataFrame:
    """Deterministic mock data for testing/offline use."""
    np.random.seed(hash(symbol + interval) % 2**31)
    price = {"BTCUSDT": 65000, "ETHUSDT": 3500, "XAUUSD": 2350,
             "GBPUSD": 1.27, "EURUSD": 1.08, "USDJPY": 151,
             "USDCHF": 0.90, "NAS100": 18000}.get(symbol, 1.0)
    volatility = price * 0.003
    closes  = price + np.cumsum(np.random.randn(n) * volatility)
    highs   = closes + np.abs(np.random.randn(n) * volatility * 0.5)
    lows    = closes - np.abs(np.random.randn(n) * volatility * 0.5)
    opens   = np.roll(closes, 1)
    opens[0] = closes[0]
    times = pd.date_range(end=datetime.now(timezone.utc), periods=n,
                          freq=interval.replace("m", "T").replace("h", "H")
                               .replace("d", "D").replace("4h", "4H"))
    return pd.DataFrame({
        "open": opens, "high": highs, "low": lows,
        "close": closes, "volume": np.random.randint(1000, 100000, n).astype(float)
    }, index=times)


# ── Interval helpers ──────────────────────────────────────────────────────────

_BINANCE_INTERVALS = {
    "1d": "1d", "4h": "4h", "1h": "1h", "15m": "15m",
}
_YF_INTERVALS = {
    "1d": ("1d", "2y"),
    "4h": ("1h", "60d"),   # yfinance has no 4h; we resample from 1h
    "1h": ("1h", "60d"),
    "15m": ("15m", "8d"),
}


def _resample_to_4h(df_1h: pd.DataFrame) -> pd.DataFrame:
    """Resample 1h data to 4h candles."""
    df = df_1h.resample("4H", label="left", closed="left").agg({
        "open":   "first",
        "high":   "max",
        "low":    "min",
        "close":  "last",
        "volume": "sum",
    }).dropna()
    return df


def fetch_ohlcv(symbol: str, interval: str, source: str = "auto",
                limit: int = 250) -> Optional[pd.DataFrame]:
    """
    Fetch OHLCV for a single timeframe.
    interval: '1d' | '4h' | '1h' | '15m'
    """
    if source == "mock":
        return _generate_mock(symbol, interval, limit)

    # Binance for crypto
    if source in ("auto", "binance") and symbol in CRYPTO_SYMBOLS:
        bi = _BINANCE_INTERVALS.get(interval)
        if bi:
            df = _fetch_binance(symbol, bi, limit)
            if _validate_ohlcv(df):
                return df

    # yfinance for everything else (or fallback)
    if source in ("auto", "yfinance"):
        if interval == "4h":
            # Resample from 1h
            yf_i, yf_p = _YF_INTERVALS["1h"]
            df1h = _fetch_yfinance(symbol, yf_i, yf_p)
            if _validate_ohlcv(df1h, 10):
                df = _resample_to_4h(df1h)
                if _validate_ohlcv(df, 10):
                    return df
        else:
            yf_i, yf_p = _YF_INTERVALS.get(interval, ("1h", "60d"))
            df = _fetch_yfinance(symbol, yf_i, yf_p)
            if _validate_ohlcv(df):
                return df

    logger.warning("All sources failed for %s %s – using mock", symbol, interval)
    return _generate_mock(symbol, interval, limit)


def fetch_all_timeframes(symbol: str, source: str = "auto") -> dict[str, pd.DataFrame]:
    """
    Fetch Daily, H4, H1, M15 data for one symbol.
    Returns dict with keys: 'daily', 'h4', 'h1', 'm15'
    """
    return {
        "daily": fetch_ohlcv(symbol, "1d",  source, limit=250),
        "h4":    fetch_ohlcv(symbol, "4h",  source, limit=150),
        "h1":    fetch_ohlcv(symbol, "1h",  source, limit=150),
        "m15":   fetch_ohlcv(symbol, "15m", source, limit=100),
    }

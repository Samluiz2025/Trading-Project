"""
data_fetcher.py
Multi-source OHLCV fetcher: Binance → yfinance → mock fallback.
Fetches Daily / H4 / H1 / M15 for any approved symbol.
"""
from __future__ import annotations
import logging
import time
from datetime import datetime, timezone
from typing import Optional
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

CRYPTO_SYMBOLS = {"BTCUSDT", "ETHUSDT"}
FOREX_SYMBOLS  = {"GBPUSD", "EURUSD", "USDCHF", "USDJPY"}
METAL_SYMBOLS  = {"XAUUSD"}
INDEX_SYMBOLS  = {"NAS100", "US100"}

YFINANCE_MAP = {
    "GBPUSD":  "GBPUSD=X",
    "EURUSD":  "EURUSD=X",
    "USDCHF":  "USDCHF=X",
    "USDJPY":  "USDJPY=X",
    "XAUUSD":  "GC=F",
    "NAS100":  "NQ=F",
    "US100":   "NQ=F",
    "BTCUSDT": "BTC-USD",
    "ETHUSDT": "ETH-USD",
}

_YF_INTERVALS = {
    "1d":  ("1d",  "2y"),
    "4h":  ("1h",  "60d"),
    "1h":  ("1h",  "60d"),
    "15m": ("15m", "8d"),
}


def _is_weekend_closed(symbol: str) -> bool:
    if symbol.upper() in CRYPTO_SYMBOLS:
        return False
    return datetime.now(timezone.utc).weekday() >= 5


def _validate(df: pd.DataFrame, min_rows: int = 20) -> bool:
    if df is None or len(df) < min_rows:
        return False
    required = {"open", "high", "low", "close"}
    if not required.issubset(set(df.columns)):
        return False
    if df[["open", "high", "low", "close"]].isnull().all().any():
        return False
    return True


def _fetch_binance(symbol: str, interval: str, limit: int) -> Optional[pd.DataFrame]:
    try:
        import requests
        url = "https://api.binance.com/api/v3/klines"
        r = requests.get(url, params={"symbol": symbol, "interval": interval, "limit": limit}, timeout=10)
        r.raise_for_status()
        cols = ["timestamp","open","high","low","close","volume",
                "close_time","quote_vol","n_trades","taker_base","taker_quote","ignore"]
        df = pd.DataFrame(r.json(), columns=cols)
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df.set_index("timestamp", inplace=True)
        for c in ["open","high","low","close","volume"]:
            df[c] = df[c].astype(float)
        return df[["open","high","low","close","volume"]]
    except Exception as e:
        logger.debug("Binance failed %s %s: %s", symbol, interval, e)
        return None


def _fetch_yfinance(symbol: str, interval: str, period: str) -> Optional[pd.DataFrame]:
    try:
        import yfinance as yf
        ticker = YFINANCE_MAP.get(symbol.upper(), symbol)
        df = yf.download(ticker, interval=interval, period=period,
                         progress=False, auto_adjust=True, multi_level_index=False)
        if df is None or df.empty:
            return None
        df.columns = [c.lower() for c in df.columns]
        df.index = pd.to_datetime(df.index, utc=True)
        needed = [c for c in ["open","high","low","close","volume"] if c in df.columns]
        return df[needed].dropna(how="all")
    except Exception as e:
        logger.debug("yfinance failed %s %s: %s", symbol, interval, e)
        return None


def _resample_4h(df_1h: pd.DataFrame) -> pd.DataFrame:
    return df_1h.resample("4h", label="left", closed="left").agg(
        {"open":"first","high":"max","low":"min","close":"last","volume":"sum"}
    ).dropna()


def _mock(symbol: str, interval: str, n: int) -> pd.DataFrame:
    np.random.seed(hash(symbol + interval) % 2**31)
    base = {"BTCUSDT":65000,"ETHUSDT":3500,"XAUUSD":2350,
            "GBPUSD":1.27,"EURUSD":1.08,"USDJPY":151,
            "USDCHF":0.90,"NAS100":18000}.get(symbol.upper(), 1.0)
    vol = base * 0.003
    closes = base + np.cumsum(np.random.randn(n) * vol)
    highs  = closes + np.abs(np.random.randn(n) * vol * 0.5)
    lows   = closes - np.abs(np.random.randn(n) * vol * 0.5)
    opens  = np.roll(closes, 1); opens[0] = closes[0]
    freq   = {"15m":"15min","1h":"1h","4h":"4h","1d":"1D"}.get(interval,"1h")
    times  = pd.date_range(end=datetime.now(timezone.utc), periods=n, freq=freq)
    return pd.DataFrame({"open":opens,"high":highs,"low":lows,
                          "close":closes,"volume":np.random.randint(1000,100000,n).astype(float)},
                         index=times)


def fetch_ohlcv(symbol: str, interval: str, source: str = "auto",
                limit: int = 250) -> Optional[pd.DataFrame]:
    sym = symbol.upper()
    if source == "mock":
        return _mock(sym, interval, limit)

    # Binance for crypto
    if source in ("auto","binance") and sym in CRYPTO_SYMBOLS:
        bi_map = {"1d":"1d","4h":"4h","1h":"1h","15m":"15m"}
        bi = bi_map.get(interval)
        if bi:
            df = _fetch_binance(sym, bi, limit)
            if _validate(df):
                return df

    # yfinance
    if source in ("auto","yfinance"):
        if interval == "4h":
            yf_i, yf_p = _YF_INTERVALS["1h"]
            df1h = _fetch_yfinance(sym, yf_i, yf_p)
            if _validate(df1h, 10):
                df = _resample_4h(df1h)
                if _validate(df, 10):
                    return df
        else:
            yf_i, yf_p = _YF_INTERVALS.get(interval, ("1h","60d"))
            df = _fetch_yfinance(sym, yf_i, yf_p)
            if _validate(df):
                return df

    # Weekend / all-sources-failed — return mock so dashboard stays alive
    logger.warning("All live sources failed for %s %s — using mock data", sym, interval)
    return _mock(sym, interval, limit)


def fetch_all_timeframes(symbol: str, source: str = "auto") -> dict[str, Optional[pd.DataFrame]]:
    sym = symbol.upper()
    return {
        "daily": fetch_ohlcv(sym, "1d",  source, 250),
        "h4":    fetch_ohlcv(sym, "4h",  source, 150),
        "h1":    fetch_ohlcv(sym, "1h",  source, 150),
        "m15":   fetch_ohlcv(sym, "15m", source, 100),
    }
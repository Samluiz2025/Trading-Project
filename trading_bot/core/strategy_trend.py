from __future__ import annotations

import pandas as pd

from trading_bot.core.market_structure import validate_ohlc_dataframe


def generate_trade_setup(symbol: str, h1_data: pd.DataFrame, daily_bias: str, m30_data: pd.DataFrame | None = None) -> dict:
    validate_ohlc_dataframe(h1_data)
    dataframe = h1_data.copy()
    dataframe["ema50"] = dataframe["close"].ewm(span=50, adjust=False).mean()
    dataframe["ema200"] = dataframe["close"].ewm(span=200, adjust=False).mean()

    latest = dataframe.iloc[-1]
    ema50 = float(latest["ema50"])
    ema200 = float(latest["ema200"])
    close_price = float(latest["close"])

    bullish = ema50 > ema200 and "bullish" in daily_bias
    bearish = ema50 < ema200 and "bearish" in daily_bias
    pullback_tolerance = max(close_price * 0.0015, 0.0005)

    if bullish and abs(close_price - ema50) <= pullback_tolerance:
        return {
            "status": "VALID_TRADE",
            "strategy": "TREND",
            "pair": symbol.upper(),
            "bias": "BUY",
            "entry": round(close_price, 4),
            "sl": round(close_price - max(abs(close_price - ema200) * 0.35, pullback_tolerance), 4),
            "tp": round(close_price + max(abs(close_price - ema200), pullback_tolerance * 3), 4),
            "confluences": ["EMA50 > EMA200", "Pullback to EMA50"],
            "confidence_score": 58,
            "details": {
                "ema50": round(ema50, 4),
                "ema200": round(ema200, 4),
            },
        }

    if bearish and abs(close_price - ema50) <= pullback_tolerance:
        return {
            "status": "VALID_TRADE",
            "strategy": "TREND",
            "pair": symbol.upper(),
            "bias": "SELL",
            "entry": round(close_price, 4),
            "sl": round(close_price + max(abs(close_price - ema200) * 0.35, pullback_tolerance), 4),
            "tp": round(close_price - max(abs(close_price - ema200), pullback_tolerance * 3), 4),
            "confluences": ["EMA50 < EMA200", "Pullback to EMA50"],
            "confidence_score": 58,
            "details": {
                "ema50": round(ema50, 4),
                "ema200": round(ema200, 4),
            },
        }

    return {
        "status": "NO TRADE",
        "strategy": "TREND",
        "message": "No valid setup available",
        "missing": ["EMA alignment or pullback"],
        "details": {
            "ema50": round(ema50, 4),
            "ema200": round(ema200, 4),
        },
    }

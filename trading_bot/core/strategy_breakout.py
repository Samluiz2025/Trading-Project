from __future__ import annotations

import pandas as pd

from trading_bot.core.market_structure import validate_ohlc_dataframe


def generate_trade_setup(symbol: str, h1_data: pd.DataFrame, daily_bias: str, m30_data: pd.DataFrame | None = None) -> dict:
    validate_ohlc_dataframe(h1_data)
    range_window = h1_data.tail(18).reset_index(drop=True)
    if len(range_window) < 18:
        return {
            "status": "NO TRADE",
            "strategy": "BREAKOUT",
            "message": "No valid setup available",
            "missing": ["Not enough H1 candles"],
        }

    pre_break = range_window.iloc[:-3]
    breakout_zone_high = float(pre_break["high"].max())
    breakout_zone_low = float(pre_break["low"].min())
    zone_size = breakout_zone_high - breakout_zone_low
    last = range_window.iloc[-1]
    middle = range_window.iloc[-2]
    breakout_body = abs(float(middle["close"]) - float(middle["open"]))
    average_body = float((pre_break["close"] - pre_break["open"]).abs().mean())

    if zone_size <= 0 or average_body <= 0:
        return {
            "status": "NO TRADE",
            "strategy": "BREAKOUT",
            "message": "No valid setup available",
            "missing": ["Invalid consolidation range"],
        }

    bullish_break = (
        "bullish" in daily_bias
        and float(middle["close"]) > breakout_zone_high
        and breakout_body >= average_body * 1.5
        and float(last["low"]) <= breakout_zone_high <= float(last["close"])
    )
    bearish_break = (
        "bearish" in daily_bias
        and float(middle["close"]) < breakout_zone_low
        and breakout_body >= average_body * 1.5
        and float(last["high"]) >= breakout_zone_low >= float(last["close"])
    )

    if bullish_break:
        entry = round(float(last["close"]), 4)
        sl = round(breakout_zone_low, 4)
        tp = round(entry + max(zone_size * 1.5, abs(entry - sl) * 2), 4)
        return {
            "status": "VALID_TRADE",
            "strategy": "BREAKOUT",
            "pair": symbol.upper(),
            "bias": "BUY",
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "confluences": ["Consolidation", "Strong breakout candle", "Retest holds"],
            "confidence_score": 55,
            "details": {"range_high": round(breakout_zone_high, 4), "range_low": round(breakout_zone_low, 4)},
        }

    if bearish_break:
        entry = round(float(last["close"]), 4)
        sl = round(breakout_zone_high, 4)
        tp = round(entry - max(zone_size * 1.5, abs(entry - sl) * 2), 4)
        return {
            "status": "VALID_TRADE",
            "strategy": "BREAKOUT",
            "pair": symbol.upper(),
            "bias": "SELL",
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "confluences": ["Consolidation", "Strong breakout candle", "Retest holds"],
            "confidence_score": 55,
            "details": {"range_high": round(breakout_zone_high, 4), "range_low": round(breakout_zone_low, 4)},
        }

    return {
        "status": "NO TRADE",
        "strategy": "BREAKOUT",
        "message": "No valid setup available",
        "missing": ["Breakout or retest"],
        "details": {"range_high": round(breakout_zone_high, 4), "range_low": round(breakout_zone_low, 4)},
    }

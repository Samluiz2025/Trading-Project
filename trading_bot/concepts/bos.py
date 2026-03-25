"""Break of structure concept detector."""

from __future__ import annotations

import pandas as pd

from trading_bot.concepts.base import ConceptSignal
from trading_bot.core.market_structure import detect_swings, validate_ohlc_dataframe


def detect_bos_signals(dataframe: pd.DataFrame, swing_window: int = 2) -> list[ConceptSignal]:
    """Detect bullish or bearish break-of-structure signals."""

    validate_ohlc_dataframe(dataframe)
    swings = detect_swings(dataframe, swing_window=swing_window)
    if len(swings) < 2:
        return []

    signals: list[ConceptSignal] = []
    recent_high = None
    recent_low = None

    swing_lookup = {int(swing["index"]): swing for swing in swings}
    for index in range(len(dataframe)):
        if index in swing_lookup:
            swing = swing_lookup[index]
            if swing["type"] == "high":
                recent_high = float(swing["price"])
            else:
                recent_low = float(swing["price"])

        candle = dataframe.iloc[index]
        close_price = float(candle["close"])
        high_price = float(candle["high"])
        low_price = float(candle["low"])

        if recent_high is not None and close_price > recent_high:
            stop_loss = low_price
            risk = max(close_price - stop_loss, close_price * 0.001)
            signals.append(
                ConceptSignal(
                    concept="BOS",
                    signal="BUY",
                    index=index,
                    time=pd.Timestamp(candle["time"]).isoformat(),
                    entry=close_price,
                    stop_loss=round(stop_loss, 4),
                    take_profit=round(close_price + (risk * 2), 4),
                    confidence=72,
                    metadata={"broken_level": recent_high},
                )
            )
            recent_high = close_price

        if recent_low is not None and close_price < recent_low:
            stop_loss = high_price
            risk = max(stop_loss - close_price, close_price * 0.001)
            signals.append(
                ConceptSignal(
                    concept="BOS",
                    signal="SELL",
                    index=index,
                    time=pd.Timestamp(candle["time"]).isoformat(),
                    entry=close_price,
                    stop_loss=round(stop_loss, 4),
                    take_profit=round(close_price - (risk * 2), 4),
                    confidence=72,
                    metadata={"broken_level": recent_low},
                )
            )
            recent_low = close_price

    return signals

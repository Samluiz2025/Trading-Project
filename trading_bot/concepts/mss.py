"""Market structure shift concept detector."""

from __future__ import annotations

import pandas as pd

from trading_bot.concepts.base import ConceptSignal
from trading_bot.core.market_structure import detect_market_structure, validate_ohlc_dataframe


def detect_mss_signals(dataframe: pd.DataFrame) -> list[ConceptSignal]:
    """Detect simple market-structure-shift events from recent trend reversals."""

    validate_ohlc_dataframe(dataframe)
    if len(dataframe) < 20:
        return []

    signals: list[ConceptSignal] = []
    previous_trend = None

    for index in range(10, len(dataframe)):
        window = dataframe.iloc[: index + 1]
        trend = detect_market_structure(window)["trend"]
        if previous_trend is None:
            previous_trend = trend
            continue

        candle = dataframe.iloc[index]
        close_price = float(candle["close"])
        high_price = float(candle["high"])
        low_price = float(candle["low"])

        if previous_trend == "bearish" and trend == "bullish":
            risk = max(close_price - low_price, close_price * 0.001)
            signals.append(
                ConceptSignal(
                    concept="MSS",
                    signal="BUY",
                    index=index,
                    time=pd.Timestamp(candle["time"]).isoformat(),
                    entry=close_price,
                    stop_loss=round(low_price, 4),
                    take_profit=round(close_price + (risk * 2), 4),
                    confidence=74,
                    metadata={"previous_trend": previous_trend, "new_trend": trend},
                )
            )

        if previous_trend == "bullish" and trend == "bearish":
            risk = max(high_price - close_price, close_price * 0.001)
            signals.append(
                ConceptSignal(
                    concept="MSS",
                    signal="SELL",
                    index=index,
                    time=pd.Timestamp(candle["time"]).isoformat(),
                    entry=close_price,
                    stop_loss=round(high_price, 4),
                    take_profit=round(close_price - (risk * 2), 4),
                    confidence=74,
                    metadata={"previous_trend": previous_trend, "new_trend": trend},
                )
            )

        previous_trend = trend

    return signals

"""Fair value gap concept detector."""

from __future__ import annotations

import pandas as pd

from trading_bot.concepts.base import ConceptSignal
from trading_bot.core.market_structure import validate_ohlc_dataframe


def detect_fvg_signals(dataframe: pd.DataFrame) -> list[ConceptSignal]:
    """Detect simple three-candle fair value gap imbalances."""

    validate_ohlc_dataframe(dataframe)
    if len(dataframe) < 3:
        return []

    signals: list[ConceptSignal] = []
    for index in range(2, len(dataframe)):
        first_candle = dataframe.iloc[index - 2]
        third_candle = dataframe.iloc[index]

        bullish_gap = float(third_candle["low"]) > float(first_candle["high"])
        bearish_gap = float(third_candle["high"]) < float(first_candle["low"])

        if bullish_gap:
            entry = float(third_candle["close"])
            stop_loss = float(third_candle["low"])
            risk = max(entry - stop_loss, entry * 0.001)
            signals.append(
                ConceptSignal(
                    concept="FVG",
                    signal="BUY",
                    index=index,
                    time=pd.Timestamp(third_candle["time"]).isoformat(),
                    entry=entry,
                    stop_loss=round(stop_loss, 4),
                    take_profit=round(entry + (risk * 2), 4),
                    confidence=68,
                    metadata={
                        "gap_start": round(float(first_candle["high"]), 4),
                        "gap_end": round(float(third_candle["low"]), 4),
                    },
                )
            )

        if bearish_gap:
            entry = float(third_candle["close"])
            stop_loss = float(third_candle["high"])
            risk = max(stop_loss - entry, entry * 0.001)
            signals.append(
                ConceptSignal(
                    concept="FVG",
                    signal="SELL",
                    index=index,
                    time=pd.Timestamp(third_candle["time"]).isoformat(),
                    entry=entry,
                    stop_loss=round(stop_loss, 4),
                    take_profit=round(entry - (risk * 2), 4),
                    confidence=68,
                    metadata={
                        "gap_start": round(float(third_candle["high"]), 4),
                        "gap_end": round(float(first_candle["low"]), 4),
                    },
                )
            )

    return signals

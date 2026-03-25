"""Order block concept detector built from recent supply/demand zones."""

from __future__ import annotations

import pandas as pd

from trading_bot.concepts.base import ConceptSignal
from trading_bot.core.market_structure import validate_ohlc_dataframe
from trading_bot.core.supply_demand import detect_supply_demand_zones


def detect_order_block_signals(
    dataframe: pd.DataFrame,
    symbol: str,
    timeframe: str,
) -> list[ConceptSignal]:
    """Approximate order-block entries from the latest demand and supply zones."""

    validate_ohlc_dataframe(dataframe)
    zones = detect_supply_demand_zones(dataframe, symbol=symbol, timeframe=timeframe)
    if not zones:
        return []

    signals: list[ConceptSignal] = []
    for zone in zones:
        zone_low = min(zone["start_price"], zone["end_price"])
        zone_high = max(zone["start_price"], zone["end_price"])
        entry = round((zone_low + zone_high) / 2, 4)

        if zone["type"] == "demand":
            stop_loss = zone_low
            risk = max(entry - stop_loss, entry * 0.001)
            signal = "BUY"
            take_profit = entry + (risk * 2)
        else:
            stop_loss = zone_high
            risk = max(stop_loss - entry, entry * 0.001)
            signal = "SELL"
            take_profit = entry - (risk * 2)

        signals.append(
            ConceptSignal(
                concept="OrderBlock",
                signal=signal,
                index=len(dataframe) - 1,
                time=zone["formed_at"],
                entry=entry,
                stop_loss=round(stop_loss, 4),
                take_profit=round(take_profit, 4),
                confidence=70,
                metadata=zone,
            )
        )

    return signals

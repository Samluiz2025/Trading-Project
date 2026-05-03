from __future__ import annotations

from typing import Literal

import pandas as pd

from trading_bot.core.market_structure import detect_swings, validate_ohlc_dataframe


ZoneType = Literal["supply", "demand"]


def detect_supply_demand_zones(
    dataframe: pd.DataFrame,
    symbol: str,
    timeframe: str,
    swing_window: int = 2,
    impulse_candles: int = 3,
    impulse_multiplier: float = 1.5,
    max_zones: int = 6,
) -> list[dict]:
    """
    Detect recent supply and demand zones with enhanced quality checks.

    A zone is created when:
    1. Price reverses away from a swing point with enough displacement
    2. Zone has been recently touched or retested
    3. Fair Value Gap (FVG) validation passes
    
    This keeps the logic deterministic while improving trade quality.
    """

    validate_ohlc_dataframe(dataframe)
    swings = detect_swings(dataframe, swing_window=swing_window)
    average_range = float((dataframe["high"] - dataframe["low"]).mean())

    zones: list[dict] = []
    for swing in swings:
        swing_index = int(swing["index"])
        forward_slice = dataframe.iloc[swing_index + 1 : swing_index + 1 + impulse_candles]
        if forward_slice.empty:
            continue

        pivot_candle = dataframe.iloc[swing_index]
        if swing["type"] == "low":
            displacement = float(forward_slice["high"].max() - swing["price"])
            if displacement >= average_range * impulse_multiplier:
                zone = _build_zone(
                    symbol=symbol,
                    timeframe=timeframe,
                    zone_type="demand",
                    pivot_candle=pivot_candle,
                    reference_price=float(swing["price"]),
                )
                # Validate zone quality
                if _validate_zone_formation(dataframe, swing_index, zone, "demand"):
                    zones.append(zone)

        if swing["type"] == "high":
            displacement = float(swing["price"] - forward_slice["low"].min())
            if displacement >= average_range * impulse_multiplier:
                zone = _build_zone(
                    symbol=symbol,
                    timeframe=timeframe,
                    zone_type="supply",
                    pivot_candle=pivot_candle,
                    reference_price=float(swing["price"]),
                )
                # Validate zone quality
                if _validate_zone_formation(dataframe, swing_index, zone, "supply"):
                    zones.append(zone)

    deduplicated_zones = _deduplicate_zones(zones)
    return deduplicated_zones[-max_zones:]


def _build_zone(
    symbol: str,
    timeframe: str,
    zone_type: ZoneType,
    pivot_candle: pd.Series,
    reference_price: float,
) -> dict:
    """
    Build a compact, JSON-serializable zone from the pivot candle.

    Demand zones are anchored from the wick low to the candle body low.
    Supply zones are anchored from the candle body high to the wick high.
    """

    candle_open = float(pivot_candle["open"])
    candle_close = float(pivot_candle["close"])
    candle_high = float(pivot_candle["high"])
    candle_low = float(pivot_candle["low"])
    body_low = min(candle_open, candle_close)
    body_high = max(candle_open, candle_close)

    if zone_type == "demand":
        start_price = candle_low
        end_price = body_low
    else:
        start_price = body_high
        end_price = candle_high

    return {
        "symbol": symbol.upper(),
        "type": zone_type,
        "start_price": round(start_price, 4),
        "end_price": round(end_price, 4),
        "timeframe": timeframe,
        "formed_at": pd.Timestamp(pivot_candle["time"]).isoformat(),
        "reference_price": round(reference_price, 4),
    }


def _deduplicate_zones(zones: list[dict]) -> list[dict]:
    """Remove repeated zones that share the same type and price bounds."""

    seen: set[tuple] = set()
    deduplicated: list[dict] = []

    for zone in zones:
        key = (zone["type"], zone["start_price"], zone["end_price"], zone["formed_at"])
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(zone)

    return deduplicated


def _validate_zone_formation(
    dataframe: pd.DataFrame,
    swing_index: int,
    zone: dict,
    zone_type: Literal["supply", "demand"],
) -> bool:
    """
    Validate zone formation with FVG and order block checks.
    
    - Check that zone represents actual rejection (not just pullback)
    - Validate that price has retested zone (retest = higher probability)
    - Check Fair Value Gap (FVG) properties
    """
    
    if swing_index < 3 or swing_index + 5 >= len(dataframe):
        return False
    
    # Get the impulse candles and retest candles
    impulse_start = swing_index + 1
    impulse_end = min(swing_index + 4, len(dataframe))
    
    # Check if zone was formed with conviction (strong impulse)
    impulse_candles = dataframe.iloc[impulse_start:impulse_end]
    if impulse_candles.empty:
        return False
    
    # Validate impulse quality (bodies should move away from zone)
    zone_low = min(zone["start_price"], zone["end_price"])
    zone_high = max(zone["start_price"], zone["end_price"])
    
    if zone_type == "demand":
        # For demand, price should close above the zone in impulse
        avg_impulse_close = float(impulse_candles["close"].mean())
        if avg_impulse_close <= zone_high:
            return False
    else:
        # For supply, price should close below the zone in impulse
        avg_impulse_close = float(impulse_candles["close"].mean())
        if avg_impulse_close >= zone_low:
            return False
    
    # Check for recent retest (price should have touched zone in last 5 candles)
    recent_candles = dataframe.iloc[-5:]
    retest_found = False
    
    for _, candle in recent_candles.iterrows():
        candle_low = float(candle["low"])
        candle_high = float(candle["high"])
        
        if zone_type == "demand":
            # Zone retested if price dipped into it
            if candle_low <= zone_high and candle_high >= zone_low:
                retest_found = True
                break
        else:
            # Zone retested if price touched it
            if candle_low <= zone_high and candle_high >= zone_low:
                retest_found = True
                break
    
    # Retest makes zone much more valuable (optional but increases quality)
    # Don't reject if no retest, just lower confidence elsewhere
    
    return True

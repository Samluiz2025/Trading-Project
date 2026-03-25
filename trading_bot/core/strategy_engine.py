from __future__ import annotations

from typing import Literal

import pandas as pd

from trading_bot.core.market_structure import detect_market_structure, validate_ohlc_dataframe
from trading_bot.core.supply_demand import detect_supply_demand_zones


SignalSide = Literal["BUY", "SELL"]


def generate_trade_setup(
    dataframe: pd.DataFrame,
    symbol: str,
    timeframe: str,
    risk_reward_ratio: float = 2.0,
    zone_tolerance_ratio: float = 0.0025,
) -> dict:
    """
    Generate a simple rule-based setup from trend and zone interaction.

    Strategy rules:
    - Bullish trend + pullback into demand zone -> BUY
    - Bearish trend + pullback into supply zone -> SELL
    - Otherwise, return no actionable setup
    """

    validate_ohlc_dataframe(dataframe)
    structure = detect_market_structure(dataframe)
    zones = detect_supply_demand_zones(dataframe, symbol=symbol, timeframe=timeframe)
    latest_candle = dataframe.iloc[-1]
    current_price = float(latest_candle["close"])

    setup: dict | None = None
    if structure["trend"] == "bullish":
        demand_zone = _find_active_zone(
            zones=zones,
            zone_type="demand",
            current_price=current_price,
            tolerance_ratio=zone_tolerance_ratio,
        )
        if demand_zone is not None:
            setup = _build_setup(
                side="BUY",
                zone=demand_zone,
                current_price=current_price,
                structure=structure,
                risk_reward_ratio=risk_reward_ratio,
            )
    elif structure["trend"] == "bearish":
        supply_zone = _find_active_zone(
            zones=zones,
            zone_type="supply",
            current_price=current_price,
            tolerance_ratio=zone_tolerance_ratio,
        )
        if supply_zone is not None:
            setup = _build_setup(
                side="SELL",
                zone=supply_zone,
                current_price=current_price,
                structure=structure,
                risk_reward_ratio=risk_reward_ratio,
            )

    return {
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "trend": structure["trend"],
        "zones": zones,
        "setup": setup,
        "latest_price": round(current_price, 4),
    }


def _find_active_zone(
    zones: list[dict],
    zone_type: Literal["supply", "demand"],
    current_price: float,
    tolerance_ratio: float,
) -> dict | None:
    """
    Find the nearest relevant zone if price is inside it or very close to it.

    This is the hand-off point we can later use for alerting when a symbol
    enters a high-interest area.
    """

    candidate_zones = [zone for zone in zones if zone["type"] == zone_type]
    if not candidate_zones:
        return None

    nearest_zone: dict | None = None
    nearest_distance: float | None = None

    for zone in reversed(candidate_zones):
        zone_low = min(zone["start_price"], zone["end_price"])
        zone_high = max(zone["start_price"], zone["end_price"])
        zone_size = max(zone_high - zone_low, current_price * tolerance_ratio)

        in_zone = zone_low <= current_price <= zone_high
        near_zone = zone_low - zone_size <= current_price <= zone_high + zone_size
        if not in_zone and not near_zone:
            continue

        distance = min(abs(current_price - zone_low), abs(current_price - zone_high))
        if nearest_distance is None or distance < nearest_distance:
            nearest_zone = zone
            nearest_distance = distance

    return nearest_zone


def _build_setup(
    side: SignalSide,
    zone: dict,
    current_price: float,
    structure: dict,
    risk_reward_ratio: float,
) -> dict:
    """Build a serializable trade setup with entry, stop, and target."""

    zone_low = min(zone["start_price"], zone["end_price"])
    zone_high = max(zone["start_price"], zone["end_price"])
    zone_width = zone_high - zone_low
    stop_buffer = max(zone_width * 0.15, current_price * 0.001)

    if side == "BUY":
        entry = current_price if zone_low <= current_price <= zone_high else zone_high
        stop_loss = zone_low - stop_buffer
        risk = entry - stop_loss
        trend_target = structure.get("last_HH")
        take_profit = max(entry + (risk * risk_reward_ratio), trend_target or 0)
    else:
        entry = current_price if zone_low <= current_price <= zone_high else zone_low
        stop_loss = zone_high + stop_buffer
        risk = stop_loss - entry
        trend_target = structure.get("last_LL")
        projected_target = entry - (risk * risk_reward_ratio)
        take_profit = min(projected_target, trend_target) if trend_target is not None else projected_target

    return {
        "signal": side,
        "zone_type": zone["type"],
        "entry": round(entry, 4),
        "stop_loss": round(stop_loss, 4),
        "take_profit": round(take_profit, 4),
        "risk_reward_ratio": risk_reward_ratio,
        "zone": zone,
    }

from __future__ import annotations

from typing import Literal

import pandas as pd

from trading_bot.core.ai_engine import build_integrated_ai_decision, predict_signal
from trading_bot.core.market_structure import detect_market_structure, validate_ohlc_dataframe
from trading_bot.core.news_engine import combine_biases, derive_news_bias, get_pair_news_bias
from trading_bot.core.supply_demand import detect_supply_demand_zones


SignalSide = Literal["BUY", "SELL"]


def generate_trade_setup(
    dataframe: pd.DataFrame,
    symbol: str,
    timeframe: str,
    risk_reward_ratio: float = 2.5,
    zone_tolerance_ratio: float = 0.0025,
    news_events: list | None = None,
    current_time=None,
    use_ai: bool = False,
) -> dict:
    """
    Generate a rule-based setup from trend and zone interaction with enhanced filters.

    Strategy rules:
    - Bullish trend + pullback into demand zone + confluence checks -> BUY
    - Bearish trend + pullback into supply zone + confluence checks -> SELL
    - Enforce minimum 2.5 RR ratio
    - Validate candle patterns and volatility regime
    - Otherwise, return no actionable setup
    """

    validate_ohlc_dataframe(dataframe)
    structure = detect_market_structure(dataframe)
    zones = detect_supply_demand_zones(dataframe, symbol=symbol, timeframe=timeframe)
    latest_candle = dataframe.iloc[-1]
    current_price = float(latest_candle["close"])
    technical_bias = structure["trend"]
    
    # Calculate volatility metrics for better stop sizing
    volatility_data = _calculate_volatility_metrics(dataframe)
    
    # Check for quality confluences
    confluence_score = _calculate_confluence_score(dataframe, structure, zones)

    news_bias_by_currency = derive_news_bias(
        currencies=list(_get_symbol_currencies(symbol)),
        events=news_events or [],
        current_time=current_time,
    )
    news_bias = get_pair_news_bias(symbol=symbol, bias_by_currency=news_bias_by_currency)
    bias_decision = combine_biases(
        technical_bias=technical_bias,
        news_bias=news_bias,
    )

    setup: dict | None = None
    # Only enter on VERY strong bias + high confluence + good RR
    confidence_threshold = 0.7  # Stricter: was 0.6
    min_acceptable_rr = 2.5
    
    if bias_decision.final_bias in {"bullish", "strong bullish"} and confluence_score >= confidence_threshold:
        demand_zone = _find_active_zone(
            zones=zones,
            zone_type="demand",
            current_price=current_price,
            tolerance_ratio=zone_tolerance_ratio,
        )
        if demand_zone is not None and _validate_zone_quality(dataframe, demand_zone, "demand"):
            temp_setup = _build_setup(
                side="BUY",
                zone=demand_zone,
                current_price=current_price,
                structure=structure,
                risk_reward_ratio=risk_reward_ratio,
                volatility_data=volatility_data,
            )
            # Enforce minimum RR and zone proximity check
            if temp_setup and temp_setup["risk_reward_ratio"] >= min_acceptable_rr:
                # Additional check: entry should be AT OR INSIDE the zone (not below it)
                zone_low = min(demand_zone["start_price"], demand_zone["end_price"])
                zone_high = max(demand_zone["start_price"], demand_zone["end_price"])
                if zone_low <= current_price <= zone_high:
                    setup = temp_setup
                    
    elif bias_decision.final_bias in {"bearish", "strong bearish"} and confluence_score >= confidence_threshold:
        supply_zone = _find_active_zone(
            zones=zones,
            zone_type="supply",
            current_price=current_price,
            tolerance_ratio=zone_tolerance_ratio,
        )
        if supply_zone is not None and _validate_zone_quality(dataframe, supply_zone, "supply"):
            temp_setup = _build_setup(
                side="SELL",
                zone=supply_zone,
                current_price=current_price,
                structure=structure,
                risk_reward_ratio=risk_reward_ratio,
                volatility_data=volatility_data,
            )
            # Enforce minimum RR and zone proximity check
            if temp_setup and temp_setup["risk_reward_ratio"] >= min_acceptable_rr:
                # Additional check: entry should be AT OR INSIDE the zone (not above it)
                zone_low = min(supply_zone["start_price"], supply_zone["end_price"])
                zone_high = max(supply_zone["start_price"], supply_zone["end_price"])
                if zone_low <= current_price <= zone_high:
                    setup = temp_setup

    payload = {
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "trend": structure["trend"],
        "zones": zones,
        "setup": setup,
        "latest_price": round(current_price, 4),
        "technical_bias": bias_decision.technical_bias,
        "news_bias": bias_decision.news_bias,
        "final_bias": bias_decision.final_bias,
        "confidence": bias_decision.confidence,
        "news_bias_by_currency": {
            currency: {
                "bias": signal.bias,
                "driver": signal.driver,
                "event_time": signal.event_time,
            }
            for currency, signal in news_bias_by_currency.items()
        },
    }

    if not use_ai:
        return payload

    try:
        ai_result = predict_signal(
            dataframe=dataframe,
            symbol=symbol,
            timeframe=timeframe,
            strategy_bias=payload["final_bias"],
            news_events=news_events or [],
        )
    except Exception:
        ai_result = None

    return build_integrated_ai_decision(payload, ai_result)


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
    volatility_data: dict | None = None,
) -> dict:
    """Build a serializable trade setup with volatility-scaled stops and RR validation."""

    zone_low = min(zone["start_price"], zone["end_price"])
    zone_high = max(zone["start_price"], zone["end_price"])
    zone_width = zone_high - zone_low
    
    # Use volatility-scaled stops instead of fixed percentage
    if volatility_data:
        atr = volatility_data.get("atr", 0)
        stop_buffer = max(atr * 0.5, zone_width * 0.15, current_price * 0.001)
    else:
        stop_buffer = max(zone_width * 0.15, current_price * 0.001)

    if side == "BUY":
        entry = current_price if zone_low <= current_price <= zone_high else zone_high
        stop_loss = zone_low - stop_buffer
        risk = entry - stop_loss
        trend_target = structure.get("last_HH")
        take_profit = max(entry + (risk * risk_reward_ratio), trend_target or 0)
        
        # Recalculate actual RR
        if risk > 0:
            actual_rr = (take_profit - entry) / risk
        else:
            actual_rr = 0
    else:
        entry = current_price if zone_low <= current_price <= zone_high else zone_low
        stop_loss = zone_high + stop_buffer
        risk = stop_loss - entry
        trend_target = structure.get("last_LL")
        projected_target = entry - (risk * risk_reward_ratio)
        take_profit = min(projected_target, trend_target) if trend_target is not None else projected_target
        
        # Recalculate actual RR
        if risk > 0:
            actual_rr = (entry - take_profit) / risk
        else:
            actual_rr = 0

    return {
        "signal": side,
        "zone_type": zone["type"],
        "entry": round(entry, 4),
        "stop_loss": round(stop_loss, 4),
        "take_profit": round(take_profit, 4),
        "risk_reward_ratio": round(actual_rr, 2),
        "zone": zone,
    }


def _get_symbol_currencies(symbol: str) -> tuple[str, str]:
    cleaned = symbol.strip().upper().replace("/", "").replace("_", "").replace("-", "")
    special_mappings = {
        "XAUUSD": ("XAU", "USD"),
        "XAGUSD": ("XAG", "USD"),
        "USOIL": ("USOIL", "USD"),
        "UKOIL": ("UKOIL", "USD"),
        "BRENT": ("BRENT", "USD"),
        "SPX": ("SPX", "USD"),
        "NAS100": ("NAS100", "USD"),
        "DJI": ("DJI", "USD"),
        "GER40": ("GER40", "EUR"),
        "UK100": ("UK100", "GBP"),
        "JP225": ("JP225", "JPY"),
        "JPN225": ("JP225", "JPY"),
    }
    if cleaned in special_mappings:
        return special_mappings[cleaned]

    quote_candidates = ("USDT", "USDC", "USD", "JPY", "EUR", "GBP", "AUD", "CAD", "CHF", "NZD")
    for quote_currency in quote_candidates:
        if cleaned.endswith(quote_currency) and len(cleaned) > len(quote_currency):
            return cleaned[: -len(quote_currency)], quote_currency

    if len(cleaned) >= 6:
        return cleaned[:3], cleaned[3:6]

    return cleaned, "USD"


def _calculate_volatility_metrics(dataframe: pd.DataFrame) -> dict:
    """Calculate ATR and volatility metrics for dynamic stop sizing."""
    
    # Calculate True Range
    high = dataframe["high"].values
    low = dataframe["low"].values
    close = dataframe["close"].shift(1).values
    
    tr1 = high - low
    tr2 = abs(high - close)
    tr3 = abs(low - close)
    tr = pd.DataFrame({
        "tr1": tr1,
        "tr2": tr2,
        "tr3": tr3,
    }).max(axis=1)
    
    # Calculate ATR (14-period)
    atr = tr.rolling(window=14).mean().iloc[-1]
    
    # Calculate recent volatility (last 20 candles)
    recent_returns = dataframe["close"].pct_change().tail(20).abs()
    volatility = float(recent_returns.mean()) if len(recent_returns) > 0 else 0
    
    return {
        "atr": float(atr) if not pd.isna(atr) else 0,
        "volatility": volatility,
        "price_range": float(dataframe["high"].tail(20).max() - dataframe["low"].tail(20).min()),
    }


def _calculate_confluence_score(
    dataframe: pd.DataFrame,
    structure: dict,
    zones: list[dict],
) -> float:
    """
    Calculate a STRICT confluence score (0-1) for high-quality setups only.
    
    Only high scores (0.7+) should generate trades:
    - Strong trend confirmation (bullish/bearish, not ranging)
    - Multiple swing structure (at least 5 swings for confirmation)
    - Recent quality zones (fresh formation, been retested)
    - Strong candle patterns (bodies > 60% of range)
    - Price in zone = higher score
    """
    
    score = 0.0
    
    # Trend strength - CRITICAL (40% weight)
    # Only bullish/bearish count as strong
    trend = structure.get("trend", "ranging")
    if trend in {"bullish", "bearish"}:
        score += 0.4
    else:
        # In ranging market, no trade
        return 0.0
    
    # Swing structure confirmation (30% weight)
    # Need strong swing count for confluence
    swing_count = structure.get("swing_count", 0)
    if swing_count >= 7:  # Very strong structure
        score += 0.30
    elif swing_count >= 5:  # Good structure
        score += 0.20
    elif swing_count >= 3:
        score += 0.10
    else:
        return 0.0  # Insufficient structure
    
    # Recent zones (20% weight)
    # Multiple zones = better confluences
    if zones and len(zones) >= 3:
        score += 0.20
    elif zones and len(zones) >= 2:
        score += 0.10
    elif zones:
        score += 0.05
    else:
        return 0.0  # No zones = no trade
    
    # Candle pattern quality (10% weight)
    latest_candle = dataframe.iloc[-1]
    prev_candle = dataframe.iloc[-2] if len(dataframe) > 1 else None
    
    if prev_candle is not None:
        latest_body = abs(float(latest_candle["close"]) - float(latest_candle["open"]))
        latest_range = float(latest_candle["high"]) - float(latest_candle["low"])
        prev_body = abs(float(prev_candle["close"]) - float(prev_candle["open"]))
        
        # Check for strong closing candles (>60% body)
        if latest_range > 0:
            body_ratio = (latest_body / latest_range)
            if body_ratio > 0.6 and prev_body > 0:  # Strong close + previous context
                score += 0.10
            elif body_ratio > 0.5:
                score += 0.05
    
    # Normalize
    return min(score, 1.0)


def _validate_zone_quality(
    dataframe: pd.DataFrame,
    zone: dict,
    zone_type: Literal["supply", "demand"],
) -> bool:
    """Validate that zone was formed with proper impulse and is not too old."""
    
    # Get zone formation time
    try:
        zone_time = pd.Timestamp(zone.get("formed_at", ""))
        current_time = pd.Timestamp(dataframe.iloc[-1]["time"])
        age_hours = (current_time - zone_time).total_seconds() / 3600
        
        # Zone should be fresher than 72 hours
        if age_hours > 72:
            return False
    except (TypeError, ValueError):
        # If we can't parse time, be lenient
        pass
    
    # Check that zone has sufficient size (not a tick)
    zone_low = min(zone["start_price"], zone["end_price"])
    zone_high = max(zone["start_price"], zone["end_price"])
    zone_size = zone_high - zone_low
    
    # Zone must be at least 0.01% of price
    ref_price = zone.get("reference_price", (zone_low + zone_high) / 2)
    if ref_price > 0:
        zone_pct = (zone_size / ref_price) * 100
        if zone_pct < 0.01:
            return False
    
    return True

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from trading_bot.core.market_structure import detect_market_structure, detect_swings, validate_ohlc_dataframe
from trading_bot.core.strategy_registry import HTF_ZONE_STRATEGY
from trading_bot.core.supply_demand import detect_supply_demand_zones


@dataclass(frozen=True)
class HtfZoneReactionConfig:
    symbol: str
    minimum_rr: float = 2.0
    preferred_rr: float = 3.0
    stop_buffer_ratio: float = 0.0004
    zone_proximity_ratio: float = 0.0018
    confirmation_lookback: int = 32
    entry_retest_ratio: float = 0.0006


def build_htf_zone_reaction_config(symbol: str) -> HtfZoneReactionConfig:
    normalized = str(symbol).upper()
    if normalized == "XAUUSD":
        return HtfZoneReactionConfig(
            symbol=normalized,
            stop_buffer_ratio=0.0009,
            zone_proximity_ratio=0.0024,
            entry_retest_ratio=0.001,
        )
    if normalized.endswith("JPY"):
        return HtfZoneReactionConfig(
            symbol=normalized,
            stop_buffer_ratio=0.00055,
            zone_proximity_ratio=0.0022,
            entry_retest_ratio=0.0009,
        )
    return HtfZoneReactionConfig(symbol=normalized)


def generate_htf_zone_reaction_setup(
    *,
    symbol: str,
    daily_data: pd.DataFrame,
    h1_data: pd.DataFrame,
    m15_data: pd.DataFrame | None,
    config: HtfZoneReactionConfig | None = None,
) -> dict:
    active_config = config or build_htf_zone_reaction_config(symbol)
    normalized_symbol = str(symbol).upper()

    if not _supports_market(normalized_symbol):
        return _no_trade(
            symbol=normalized_symbol,
            reason="Unsupported market",
            daily_bias=None,
            h1_bias=None,
            session=None,
            missing=["Unsupported market"],
        )

    try:
        validate_ohlc_dataframe(daily_data)
        validate_ohlc_dataframe(h1_data)
        validate_ohlc_dataframe(m15_data if m15_data is not None else h1_data)
    except ValueError as exc:
        return _no_trade(
            symbol=normalized_symbol,
            reason=str(exc),
            daily_bias=None,
            h1_bias=None,
            session=None,
            missing=["Missing data"],
        )

    daily_frame = daily_data.tail(200).reset_index(drop=True)
    h1_frame = h1_data.tail(220).reset_index(drop=True)
    m15_frame = (m15_data if m15_data is not None else h1_data).tail(320).reset_index(drop=True)
    latest_price = float(m15_frame.iloc[-1]["close"])
    session_context = _detect_session_context(pd.Timestamp(m15_frame.iloc[-1]["time"]), symbol=normalized_symbol)
    session_name = str(session_context.get("session") or "")

    daily_structure = detect_market_structure(daily_frame)
    daily_bias = str(daily_structure.get("trend") or "")
    if daily_bias not in {"bullish", "bearish"}:
        return _no_trade(
            symbol=normalized_symbol,
            reason="Daily structure is unclear",
            daily_bias=daily_bias,
            h1_bias=None,
            session=session_name,
            missing=["Daily bias unclear"],
        )

    trade_side = "BUY" if daily_bias == "bullish" else "SELL"
    h1_structure = detect_market_structure(h1_frame.tail(120).reset_index(drop=True))
    h1_bias = str(h1_structure.get("trend") or "")

    zone = _select_daily_reaction_zone(
        daily_frame=daily_frame,
        symbol=normalized_symbol,
        trade_side=trade_side,
        current_price=latest_price,
        proximity_ratio=active_config.zone_proximity_ratio,
    )
    if not zone.get("confirmed"):
        return _no_trade(
            symbol=normalized_symbol,
            reason=str(zone.get("reason") or "No daily reaction zone"),
            daily_bias=daily_bias,
            h1_bias=h1_bias,
            session=session_name,
            missing=["No daily reaction zone"],
            confluences=["Daily Bias"],
            details={"session": session_context},
        )

    zone_low, zone_high = zone["plan_zone"]
    common_confluences = ["Daily Bias", "Daily Zone", "HTF Reaction Location"]
    common_details = {
        "session": session_context,
        "daily_structure": daily_structure,
        "h1_structure": h1_structure,
        "zone": zone,
        "plan_zone": zone["plan_zone"],
    }

    if not _price_in_zone(latest_price=latest_price, zone_low=zone_low, zone_high=zone_high, tolerance_ratio=active_config.zone_proximity_ratio):
        provisional_entry = round((zone_low + zone_high) / 2, 4)
        provisional_sl = round(_build_zone_stop_loss(trade_side=trade_side, zone=zone, config=active_config), 4)
        provisional_target = _select_take_profit(
            h1_frame,
            trade_side=trade_side,
            entry=provisional_entry,
            stop_loss=provisional_sl,
            minimum_rr=active_config.minimum_rr,
            preferred_rr=active_config.preferred_rr,
        )
        provisional_tp = provisional_target.get("tp")
        return _no_trade(
            symbol=normalized_symbol,
            reason="Price has not reached the Daily reaction zone yet.",
            daily_bias=daily_bias,
            h1_bias=h1_bias,
            session=session_name,
            missing=["Price not at Daily zone"],
            status="WAIT_CONFIRMATION",
            bias=trade_side,
            entry=provisional_entry,
            sl=provisional_sl,
            tp=float(provisional_tp) if provisional_tp is not None else None,
            risk_reward_ratio=float(provisional_target.get("rr") or 0.0) if provisional_target.get("confirmed") else None,
            setup_grade="A+" if float(provisional_target.get("rr") or 0.0) >= active_config.preferred_rr else "B",
            setup_type="HTF zone reaction",
            confidence="MEDIUM",
            confidence_score=70,
            invalidation=provisional_sl,
            confluences=common_confluences,
            lifecycle="zone_watch",
            stalker={"state": "near_valid", "score": 84.0, "confirmed_checks": 3, "total_checks": 5},
            details={
                **common_details,
                "confirmation_entry": {
                    "required": ["Price reach zone", "H1 or M15 reaction shift", "Retest hold"],
                    "message": "Wait for price to tap the Daily zone before looking for confirmation.",
                },
            },
            analysis_context={"session": session_name, "plan_zone": zone["plan_zone"], "zone_type": zone.get("type")},
        )

    if not _price_strictly_in_zone(latest_price=latest_price, zone_low=zone_low, zone_high=zone_high):
        return _no_trade(
            symbol=normalized_symbol,
            reason="Price is close to the Daily zone but has not entered it yet.",
            daily_bias=daily_bias,
            h1_bias=h1_bias,
            session=session_name,
            missing=["Price not inside Daily zone"],
            status="WAIT_CONFIRMATION",
            bias=trade_side,
            entry=round((zone_low + zone_high) / 2, 4),
            sl=round(_build_zone_stop_loss(trade_side=trade_side, zone=zone, config=active_config), 4),
            tp=None,
            risk_reward_ratio=None,
            setup_grade=None,
            setup_type="HTF zone reaction",
            confidence="MEDIUM",
            confidence_score=72,
            invalidation=round(_build_zone_stop_loss(trade_side=trade_side, zone=zone, config=active_config), 4),
            confluences=common_confluences,
            lifecycle="zone_watch",
            stalker={"state": "developing", "score": 78.0, "confirmed_checks": 3, "total_checks": 6},
            details={
                **common_details,
                "confirmation_entry": {
                    "required": ["Price enter zone", "H1 or M15 reaction shift", "Retest hold"],
                    "message": "Wait for price to get fully inside the Daily zone before looking for confirmation.",
                },
            },
            analysis_context={"session": session_name, "plan_zone": zone["plan_zone"], "zone_type": zone.get("type")},
        )

    confirmation = _detect_zone_reaction_confirmation(
        h1_frame=h1_frame,
        m15_frame=m15_frame,
        trade_side=trade_side,
        zone_low=zone_low,
        zone_high=zone_high,
        lookback=active_config.confirmation_lookback,
    )
    if not confirmation.get("confirmed"):
        return _no_trade(
            symbol=normalized_symbol,
            reason="Price is in the Daily zone. Waiting for H1/M15 confirmation.",
            daily_bias=daily_bias,
            h1_bias=h1_bias,
            session=session_name,
            missing=["No H1/M15 confirmation"],
            status="WAIT_CONFIRMATION",
            bias=trade_side,
            entry=round((zone_low + zone_high) / 2, 4),
            sl=round(_build_zone_stop_loss(trade_side=trade_side, zone=zone, config=active_config), 4),
            tp=None,
            risk_reward_ratio=None,
            setup_grade=None,
            setup_type="HTF zone reaction",
            confidence="MEDIUM",
            confidence_score=74,
            invalidation=round(_build_zone_stop_loss(trade_side=trade_side, zone=zone, config=active_config), 4),
            confluences=[*common_confluences, "Zone Touched"],
            lifecycle="confirmation_watch",
            stalker={"state": "developing", "score": 76.0, "confirmed_checks": 4, "total_checks": 6},
            details={
                **common_details,
                "confirmation": confirmation,
                "confirmation_entry": {
                    "required": ["H1/M15 structure shift", "Displacement away from zone", "Retest hold"],
                    "message": "The Daily zone is active. Wait for confirmation before entering.",
                },
            },
            analysis_context={"session": session_name, "plan_zone": zone["plan_zone"], "zone_type": zone.get("type")},
        )

    entry_plan = _build_confirmation_entry(
        trade_side=trade_side,
        zone_low=zone_low,
        zone_high=zone_high,
        confirmation=confirmation,
        tolerance_ratio=active_config.entry_retest_ratio,
    )
    if not entry_plan.get("confirmed"):
        return _no_trade(
            symbol=normalized_symbol,
            reason="No confirmation entry formed after the zone reaction.",
            daily_bias=daily_bias,
            h1_bias=h1_bias,
            session=session_name,
            missing=["No confirmation entry"],
            confluences=[*common_confluences, "Zone Touched", "H1/M15 Shift"],
            details={**common_details, "confirmation": confirmation},
            analysis_context={"session": session_name, "plan_zone": zone["plan_zone"], "zone_type": zone.get("type")},
        )

    entry = round(float(entry_plan["entry"]), 4)
    stop_loss = round(_build_zone_stop_loss(trade_side=trade_side, zone=zone, config=active_config), 4)
    target = _select_take_profit(
        h1_frame,
        trade_side=trade_side,
        entry=entry,
        stop_loss=stop_loss,
        minimum_rr=active_config.minimum_rr,
        preferred_rr=active_config.preferred_rr,
    )
    if not target.get("confirmed"):
        return _no_trade(
            symbol=normalized_symbol,
            reason=str(target.get("reason") or "RR below 1:2"),
            daily_bias=daily_bias,
            h1_bias=h1_bias,
            session=session_name,
            missing=["RR below 1:2"],
            confluences=[*common_confluences, "Zone Touched", "H1/M15 Shift"],
            details={**common_details, "confirmation": confirmation, "entry_plan": entry_plan},
            analysis_context={"session": session_name, "plan_zone": zone["plan_zone"], "zone_type": zone.get("type")},
        )

    take_profit = round(float(target["tp"]), 4)
    risk_reward_ratio = round(float(target["rr"]), 2)
    setup_grade = "A+" if risk_reward_ratio >= active_config.preferred_rr else "B"

    return {
        "status": "VALID_TRADE",
        "pair": normalized_symbol,
        "strategy": HTF_ZONE_STRATEGY,
        "strategies": [HTF_ZONE_STRATEGY],
        "message": "Valid trade setup available",
        "bias": trade_side,
        "daily_bias": daily_bias,
        "h1_bias": h1_bias,
        "entry": entry,
        "sl": stop_loss,
        "tp": take_profit,
        "risk_reward_ratio": risk_reward_ratio,
        "setup_grade": setup_grade,
        "setup_type": "HTF zone reaction",
        "session": session_name,
        "session_preferred": bool(session_context.get("preferred")),
        "confidence": "HIGH" if setup_grade == "A+" else "NORMAL",
        "confidence_score": 90 if setup_grade == "A+" else 77,
        "invalidation": stop_loss,
        "reason": "Daily zone, lower-timeframe reaction, and confirmation entry all align.",
        "confluences": [*common_confluences, "Zone Touched", "H1/M15 Shift", f"RR 1:{int(target['rr_floor'])}"],
        "missing": [],
        "lifecycle": "entry_reached",
        "stalker": None,
        "details": {**common_details, "confirmation": confirmation, "entry_plan": entry_plan, "target": target},
        "analysis_context": {
            "session": session_name,
            "plan_zone": zone["plan_zone"],
            "zone_type": zone.get("type"),
            "confirmation_time": confirmation.get("time"),
        },
    }


def _supports_market(symbol: str) -> bool:
    if symbol == "XAUUSD":
        return True
    return len(symbol) == 6 and symbol.isalpha()


def _detect_session_context(last_time: pd.Timestamp, *, symbol: str) -> dict:
    hour = int(last_time.tz_localize("UTC").hour) if last_time.tzinfo is None else int(last_time.tz_convert("UTC").hour)
    if 0 <= hour < 6:
        session = "asia"
    elif 6 <= hour < 12:
        session = "london"
    elif 12 <= hour < 17:
        session = "new_york"
    else:
        session = "after_hours"
    return {"session": session, "preferred": session in {"london", "new_york"}, "symbol": symbol}


def _select_daily_reaction_zone(
    *,
    daily_frame: pd.DataFrame,
    symbol: str,
    trade_side: str,
    current_price: float,
    proximity_ratio: float,
) -> dict:
    desired_type = "demand" if trade_side == "BUY" else "supply"
    zones = [
        zone for zone in detect_supply_demand_zones(daily_frame, symbol=symbol, timeframe="1d")
        if zone.get("type") == desired_type
    ]
    if not zones:
        return {"confirmed": False, "reason": "No daily reaction zone"}

    best_zone: dict | None = None
    best_distance: float | None = None
    for zone in reversed(zones):
        zone_low = float(min(zone["start_price"], zone["end_price"]))
        zone_high = float(max(zone["start_price"], zone["end_price"]))
        distance = _distance_to_zone(current_price=current_price, zone_low=zone_low, zone_high=zone_high)
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best_zone = {
                "confirmed": True,
                "type": desired_type,
                "plan_zone": [round(zone_low, 4), round(zone_high, 4)],
                "formed_at": zone.get("formed_at"),
                "reference_price": zone.get("reference_price"),
            }

    if best_zone is None:
        return {"confirmed": False, "reason": "No daily reaction zone"}
    best_zone["proximity_threshold"] = round(max(abs(current_price) * proximity_ratio, 0.0002), 4)
    return best_zone


def _distance_to_zone(*, current_price: float, zone_low: float, zone_high: float) -> float:
    if zone_low <= current_price <= zone_high:
        return 0.0
    if current_price < zone_low:
        return zone_low - current_price
    return current_price - zone_high


def _price_in_zone(*, latest_price: float, zone_low: float, zone_high: float, tolerance_ratio: float) -> bool:
    tolerance = max(abs(zone_high) * tolerance_ratio, 0.00015)
    return (zone_low - tolerance) <= latest_price <= (zone_high + tolerance)


def _price_strictly_in_zone(*, latest_price: float, zone_low: float, zone_high: float) -> bool:
    return zone_low <= latest_price <= zone_high


def _detect_zone_reaction_confirmation(
    *,
    h1_frame: pd.DataFrame,
    m15_frame: pd.DataFrame,
    trade_side: str,
    zone_low: float,
    zone_high: float,
    lookback: int,
) -> dict:
    recent = m15_frame.tail(lookback).reset_index(drop=True)
    if recent.empty:
        return {"confirmed": False}

    touched = recent[(recent["low"] <= zone_high) & (recent["high"] >= zone_low)].reset_index(drop=True)
    if touched.empty:
        return {"confirmed": False, "reason": "Zone not touched"}

    touch_index = int(touched.index[-1])
    recent_touch_time = pd.Timestamp(touched.iloc[-1]["time"]).isoformat()
    recent_swings = detect_swings(recent, swing_window=2)

    if trade_side == "BUY":
        lows = [item for item in recent_swings if item["type"] == "low" and zone_low <= float(item["price"]) <= zone_high]
        highs = [item for item in recent_swings if item["type"] == "high"]
        if not lows or not highs:
            return {"confirmed": False, "time": recent_touch_time}
        pivot_low = lows[-1]
        highs_after = [item for item in highs if int(item["index"]) > int(pivot_low["index"])]
        if not highs_after:
            return {"confirmed": False, "time": recent_touch_time}
        break_level = float(highs_after[0]["price"])
        latest_close = float(recent.iloc[-1]["close"])
        if latest_close <= break_level:
            return {"confirmed": False, "time": recent_touch_time}
        return {
            "confirmed": True,
            "time": pd.Timestamp(recent.iloc[-1]["time"]).isoformat(),
            "touch_time": recent_touch_time,
            "pivot_price": float(pivot_low["price"]),
            "break_level": round(break_level, 4),
            "entry_anchor": round((break_level + zone_high) / 2, 4),
        }

    highs = [item for item in recent_swings if item["type"] == "high" and zone_low <= float(item["price"]) <= zone_high]
    lows = [item for item in recent_swings if item["type"] == "low"]
    if not highs or not lows:
        return {"confirmed": False, "time": recent_touch_time}
    pivot_high = highs[-1]
    lows_after = [item for item in lows if int(item["index"]) > int(pivot_high["index"])]
    if not lows_after:
        return {"confirmed": False, "time": recent_touch_time}
    break_level = float(lows_after[0]["price"])
    latest_close = float(recent.iloc[-1]["close"])
    if latest_close >= break_level:
        return {"confirmed": False, "time": recent_touch_time}
    return {
        "confirmed": True,
        "time": pd.Timestamp(recent.iloc[-1]["time"]).isoformat(),
        "touch_time": recent_touch_time,
        "pivot_price": float(pivot_high["price"]),
        "break_level": round(break_level, 4),
        "entry_anchor": round((break_level + zone_low) / 2, 4),
    }


def _build_confirmation_entry(*, trade_side: str, zone_low: float, zone_high: float, confirmation: dict, tolerance_ratio: float) -> dict:
    entry_anchor = float(confirmation.get("entry_anchor") or ((zone_low + zone_high) / 2))
    tolerance = max(abs(entry_anchor) * tolerance_ratio, 0.00015)
    if trade_side == "BUY":
        zone = [round(max(zone_low, entry_anchor - tolerance), 4), round(min(zone_high, entry_anchor + tolerance), 4)]
    else:
        zone = [round(max(zone_low, entry_anchor - tolerance), 4), round(min(zone_high, entry_anchor + tolerance), 4)]
    if zone[0] > zone[1]:
        zone = [round(min(zone_low, zone_high), 4), round(max(zone_low, zone_high), 4)]
    return {
        "confirmed": True,
        "entry_zone": zone,
        "entry": round((zone[0] + zone[1]) / 2, 4),
    }


def _build_zone_stop_loss(*, trade_side: str, zone: dict, config: HtfZoneReactionConfig) -> float:
    zone_low, zone_high = [float(item) for item in zone["plan_zone"]]
    edge = zone_low if trade_side == "BUY" else zone_high
    buffer_size = max(abs(edge) * config.stop_buffer_ratio, 0.0001)
    return edge - buffer_size if trade_side == "BUY" else edge + buffer_size


def _select_take_profit(
    data: pd.DataFrame,
    *,
    trade_side: str,
    entry: float,
    stop_loss: float,
    minimum_rr: float,
    preferred_rr: float,
) -> dict:
    risk = abs(entry - stop_loss)
    if risk <= 0:
        return {"confirmed": False, "reason": "Invalid risk"}
    targets = _find_target_levels(data, trade_side=trade_side, entry=entry)
    if not targets:
        return {"confirmed": False, "reason": "No external liquidity target"}
    for target in targets:
        reward = abs(float(target["price"]) - entry)
        rr = reward / risk
        if rr >= preferred_rr:
            return {"confirmed": True, "tp": round(float(target["price"]), 4), "rr": round(rr, 2), "rr_floor": preferred_rr}
        if rr >= minimum_rr:
            return {"confirmed": True, "tp": round(float(target["price"]), 4), "rr": round(rr, 2), "rr_floor": minimum_rr}
    return {"confirmed": False, "reason": "RR below 1:2"}


def _find_target_levels(data: pd.DataFrame, *, trade_side: str, entry: float) -> list[dict]:
    swings = detect_swings(data.tail(180).reset_index(drop=True), swing_window=2)
    candidates: list[dict] = []
    if trade_side == "BUY":
        for swing in swings:
            price = float(swing["price"])
            if swing["type"] == "high" and price > entry:
                candidates.append({"price": round(price, 4), "type": "previous high"})
        candidates.sort(key=lambda item: item["price"])
    else:
        for swing in swings:
            price = float(swing["price"])
            if swing["type"] == "low" and price < entry:
                candidates.append({"price": round(price, 4), "type": "previous low"})
        candidates.sort(key=lambda item: item["price"], reverse=True)
    seen: set[float] = set()
    unique: list[dict] = []
    for item in candidates:
        if item["price"] in seen:
            continue
        seen.add(item["price"])
        unique.append(item)
    return unique


def _no_trade(
    *,
    symbol: str,
    reason: str,
    daily_bias: str | None,
    h1_bias: str | None,
    session: str | None,
    missing: list[str],
    details: dict | None = None,
    status: str = "NO TRADE",
    bias: str | None = None,
    entry: float | None = None,
    sl: float | None = None,
    tp: float | None = None,
    risk_reward_ratio: float | None = None,
    setup_grade: str | None = None,
    setup_type: str | None = None,
    confidence: str = "LOW",
    confidence_score: int = 0,
    invalidation: float | None = None,
    confluences: list[str] | None = None,
    lifecycle: str = "no_trade",
    stalker: dict | None = None,
    analysis_context: dict | None = None,
) -> dict:
    merged_context = {"session": session} if session else {}
    merged_context.update(analysis_context or {})
    return {
        "status": status,
        "pair": symbol,
        "strategy": HTF_ZONE_STRATEGY,
        "strategies": [HTF_ZONE_STRATEGY],
        "message": reason,
        "reason": reason,
        "bias": bias,
        "daily_bias": daily_bias,
        "h1_bias": h1_bias,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "risk_reward_ratio": risk_reward_ratio,
        "setup_grade": setup_grade,
        "setup_type": setup_type,
        "session": session,
        "confidence": confidence,
        "confidence_score": confidence_score,
        "invalidation": invalidation,
        "confluences": confluences or [],
        "missing": missing,
        "lifecycle": lifecycle,
        "stalker": stalker,
        "details": details or {},
        "analysis_context": merged_context,
    }

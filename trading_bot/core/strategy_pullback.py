from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from trading_bot.core.market_structure import detect_market_structure, detect_swings, validate_ohlc_dataframe
from trading_bot.core.strategy_registry import PULLBACK_STRATEGY
from trading_bot.core.supply_demand import detect_supply_demand_zones


@dataclass(frozen=True)
class PullbackContinuationConfig:
    symbol: str
    minimum_rr: float = 2.0
    preferred_rr: float = 3.0
    stop_buffer_ratio: float = 0.00035
    zone_proximity_ratio: float = 0.0018
    zone_entry_ratio: float = 0.35
    max_zone_multiple: float = 6.0
    impulse_body_ratio: float = 1.2
    confirmation_body_ratio: float = 0.95


def build_pullback_continuation_config(symbol: str) -> PullbackContinuationConfig:
    normalized = str(symbol).upper()
    if normalized == "XAUUSD":
        return PullbackContinuationConfig(
            symbol=normalized,
            stop_buffer_ratio=0.0009,
            zone_proximity_ratio=0.0025,
            zone_entry_ratio=0.4,
            max_zone_multiple=7.0,
            impulse_body_ratio=1.1,
            confirmation_body_ratio=0.85,
        )
    if normalized.endswith("JPY"):
        return PullbackContinuationConfig(
            symbol=normalized,
            stop_buffer_ratio=0.00055,
            zone_proximity_ratio=0.0022,
            zone_entry_ratio=0.38,
            max_zone_multiple=6.5,
            impulse_body_ratio=1.15,
            confirmation_body_ratio=0.9,
        )
    return PullbackContinuationConfig(symbol=normalized)


def generate_pullback_continuation_setup(
    *,
    symbol: str,
    daily_data: pd.DataFrame,
    h1_data: pd.DataFrame,
    m15_data: pd.DataFrame | None,
    config: PullbackContinuationConfig | None = None,
) -> dict:
    active_config = config or build_pullback_continuation_config(symbol)
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

    m15_frame = (m15_data if m15_data is not None else h1_data).tail(360).reset_index(drop=True)
    h1_frame = h1_data.tail(260).reset_index(drop=True)
    daily_frame = daily_data.tail(180).reset_index(drop=True)
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

    h1_structure = detect_market_structure(h1_frame.tail(140).reset_index(drop=True))
    h1_bias = str(h1_structure.get("trend") or "")
    trade_side = "BUY" if daily_bias == "bullish" else "SELL"
    if h1_bias not in {daily_bias, "ranging"}:
        return _no_trade(
            symbol=normalized_symbol,
            reason="H1 structure opposes the daily bias",
            daily_bias=daily_bias,
            h1_bias=h1_bias,
            session=session_name,
            missing=["H1 bias mismatch"],
            confluences=["Daily Trend"],
        )

    impulse = _find_recent_impulse(
        h1_frame,
        trade_side=trade_side,
        minimum_body_ratio=active_config.impulse_body_ratio,
    ) or _find_recent_impulse(
        m15_frame,
        trade_side=trade_side,
        minimum_body_ratio=max(1.05, active_config.impulse_body_ratio - 0.1),
    )
    if not impulse.get("confirmed"):
        return _no_trade(
            symbol=normalized_symbol,
            reason="No continuation impulse in the trend direction",
            daily_bias=daily_bias,
            h1_bias=h1_bias,
            session=session_name,
            missing=["No trend impulse"],
            confluences=["Daily Trend"],
            details={"session": session_context},
        )

    zone = _select_pullback_zone(
        symbol=normalized_symbol,
        trade_side=trade_side,
        m15_frame=m15_frame,
        current_price=latest_price,
        impulse=impulse,
        config=active_config,
    )
    if not zone.get("confirmed"):
        return _no_trade(
            symbol=normalized_symbol,
            reason=str(zone.get("reason") or "No clean pullback zone"),
            daily_bias=daily_bias,
            h1_bias=h1_bias,
            session=session_name,
            missing=["No continuation zone"],
            confluences=["Daily Trend", "Recent Impulse"],
            details={"session": session_context, "impulse": impulse},
        )

    plan = _build_trade_plan(
        trade_side=trade_side,
        zone=zone,
        config=active_config,
    )
    if not plan.get("confirmed"):
        return _no_trade(
            symbol=normalized_symbol,
            reason="Invalid pullback plan",
            daily_bias=daily_bias,
            h1_bias=h1_bias,
            session=session_name,
            missing=["Invalid pullback plan"],
            confluences=["Daily Trend", "Recent Impulse", "Pullback Zone"],
            details={"session": session_context, "impulse": impulse, "order_block": zone.get("order_block")},
        )

    entry = round(float(plan["entry"]), 4)
    stop_loss = round(float(plan["sl"]), 4)
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
            confluences=["Daily Trend", "Recent Impulse", "Pullback Zone"],
            details={"session": session_context, "impulse": impulse, "order_block": zone.get("order_block")},
        )

    take_profit = round(float(target["tp"]), 4)
    risk_reward_ratio = round(float(target["rr"]), 2)
    setup_grade = "A+" if risk_reward_ratio >= active_config.preferred_rr and h1_bias == daily_bias else "B"
    setup_type = "Trend pullback continuation"
    confluences = [
        "Daily Trend",
        "Recent Impulse",
        "Pullback Zone",
        "Trend Alignment" if h1_bias == daily_bias else "H1 Pullback Context",
        f"RR 1:{int(target['rr_floor'])}",
    ]
    common_details = {
        "session": session_context,
        "daily_structure": daily_structure,
        "h1_structure": h1_structure,
        "impulse": impulse,
        "order_block": zone.get("order_block"),
        "zone": zone,
        "plan_zone": zone.get("plan_zone"),
        "target": target,
    }
    common_context = {
        "session": session_name,
        "impulse_time": impulse.get("time"),
        "plan_zone": zone.get("plan_zone"),
        "zone_timeframe": zone.get("timeframe"),
    }

    zone_low, zone_high = zone.get("plan_zone", [None, None])
    if not _price_is_inside_zone(latest_price=latest_price, zone_low=zone_low, zone_high=zone_high):
        return _no_trade(
            symbol=normalized_symbol,
            reason="Trend is intact. Waiting for price to retrace into the pullback zone.",
            daily_bias=daily_bias,
            h1_bias=h1_bias,
            session=session_name,
            missing=["Price not at pullback zone"],
            status="WAIT_CONFIRMATION",
            bias=trade_side,
            entry=entry,
            sl=stop_loss,
            tp=take_profit,
            risk_reward_ratio=risk_reward_ratio,
            setup_grade=setup_grade,
            setup_type=setup_type,
            confidence="MEDIUM",
            confidence_score=72 if setup_grade == "A+" else 66,
            invalidation=stop_loss,
            confluences=confluences,
            lifecycle="zone_watch",
            stalker={"state": "near_valid", "score": 86.0, "confirmed_checks": 4, "total_checks": 5},
            details={
                **common_details,
                "confirmation_entry": {
                    "required": ["Price retrace into pullback zone", "Respect invalidation"],
                    "message": "Wait for price to return to the demand/supply zone before acting.",
                },
            },
            analysis_context=common_context,
        )

    confirmation = _detect_pullback_confirmation(
        m15_frame,
        trade_side=trade_side,
        zone_low=float(zone_low),
        zone_high=float(zone_high),
        minimum_body_ratio=active_config.confirmation_body_ratio,
    )
    if not confirmation.get("confirmed"):
        return _no_trade(
            symbol=normalized_symbol,
            reason="Price is in the pullback zone. Waiting for reaction confirmation.",
            daily_bias=daily_bias,
            h1_bias=h1_bias,
            session=session_name,
            missing=["No reaction confirmation"],
            status="WAIT_CONFIRMATION",
            bias=trade_side,
            entry=entry,
            sl=stop_loss,
            tp=take_profit,
            risk_reward_ratio=risk_reward_ratio,
            setup_grade=setup_grade,
            setup_type=setup_type,
            confidence="MEDIUM",
            confidence_score=78 if setup_grade == "A+" else 70,
            invalidation=stop_loss,
            confluences=[*confluences[:-1], "Zone Reclaim Pending", confluences[-1]],
            lifecycle="confirmation_watch",
            stalker={"state": "developing", "score": 74.0, "confirmed_checks": 4, "total_checks": 6},
            details={
                **common_details,
                "confirmation": confirmation,
                "confirmation_entry": {
                    "required": ["M15 rejection from zone", "Close back in trend direction"],
                    "message": "Let price reject the zone before treating it as executable.",
                },
            },
            analysis_context=common_context,
        )

    live_entry = round(float(confirmation.get("entry") or latest_price), 4)
    live_target = _select_take_profit(
        h1_frame,
        trade_side=trade_side,
        entry=live_entry,
        stop_loss=stop_loss,
        minimum_rr=active_config.minimum_rr,
        preferred_rr=active_config.preferred_rr,
    )
    if not live_target.get("confirmed"):
        return _no_trade(
            symbol=normalized_symbol,
            reason="Reaction confirmation came too late for the minimum RR.",
            daily_bias=daily_bias,
            h1_bias=h1_bias,
            session=session_name,
            missing=["RR below 1:2"],
            confluences=[*confluences[:-1], "Reaction Confirmation"],
            details={
                **common_details,
                "confirmation": confirmation,
                "late_entry": live_entry,
            },
            analysis_context=common_context,
        )

    take_profit = round(float(live_target["tp"]), 4)
    risk_reward_ratio = round(float(live_target["rr"]), 2)
    setup_grade = "A+" if risk_reward_ratio >= active_config.preferred_rr and h1_bias == daily_bias else "B"

    return {
        "status": "VALID_TRADE",
        "pair": normalized_symbol,
        "strategy": PULLBACK_STRATEGY,
        "strategies": [PULLBACK_STRATEGY],
        "message": "Valid trade setup available",
        "bias": trade_side,
        "daily_bias": daily_bias,
        "h1_bias": h1_bias,
        "entry": live_entry,
        "sl": stop_loss,
        "tp": take_profit,
        "risk_reward_ratio": risk_reward_ratio,
        "setup_grade": setup_grade,
        "setup_type": setup_type,
        "session": session_name,
        "session_preferred": bool(session_context.get("preferred")),
        "confidence": "HIGH" if setup_grade == "A+" else "NORMAL",
        "confidence_score": 88 if setup_grade == "A+" else 76,
        "invalidation": stop_loss,
        "reason": "Trend, impulse, pullback zone, and M15 reaction all align for continuation.",
        "confluences": [*confluences[:-1], "Reaction Confirmation", confluences[-1]],
        "missing": [],
        "lifecycle": "entry_reached",
        "stalker": None,
        "details": {
            **common_details,
            "confirmation": confirmation,
            "target": live_target if live_target.get("confirmed") else target,
        },
        "analysis_context": {
            **common_context,
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


def _find_recent_impulse(
    data: pd.DataFrame,
    *,
    trade_side: str,
    minimum_body_ratio: float,
) -> dict:
    frame = data.tail(72).reset_index(drop=True)
    if len(frame) < 10:
        return {"confirmed": False}

    body_baseline = abs(frame["close"].astype(float) - frame["open"].astype(float)).tail(48).median()
    range_baseline = (frame["high"].astype(float) - frame["low"].astype(float)).tail(48).median()
    body_baseline = max(float(body_baseline), 1e-9)
    range_baseline = max(float(range_baseline), 1e-9)

    events: list[dict] = []
    for index in range(1, len(frame)):
        candle = frame.iloc[index]
        previous = frame.iloc[index - 1]
        open_price = float(candle["open"])
        close_price = float(candle["close"])
        high_price = float(candle["high"])
        low_price = float(candle["low"])
        body = abs(close_price - open_price)
        candle_range = max(high_price - low_price, 1e-9)
        body_ratio = body / body_baseline
        range_ratio = candle_range / range_baseline

        if trade_side == "BUY":
            directional_break = close_price > float(previous["high"])
            direction_ok = close_price > open_price
            anchor_price = low_price
            extension = close_price - anchor_price
        else:
            directional_break = close_price < float(previous["low"])
            direction_ok = close_price < open_price
            anchor_price = high_price
            extension = anchor_price - close_price

        if not directional_break or not direction_ok:
            continue
        if body_ratio < minimum_body_ratio or range_ratio < 1.0:
            continue

        events.append(
            {
                "confirmed": True,
                "time": pd.Timestamp(candle["time"]).isoformat(),
                "close": round(close_price, 4),
                "anchor_price": round(anchor_price, 4),
                "extension": round(extension, 4),
                "body_ratio": round(body_ratio, 2),
                "range_ratio": round(range_ratio, 2),
                "timeframe": "1h" if len(data) <= 280 else "15m",
            }
        )

    if not events:
        return {"confirmed": False}
    return events[-1]


def _select_pullback_zone(
    *,
    symbol: str,
    trade_side: str,
    m15_frame: pd.DataFrame,
    current_price: float,
    impulse: dict,
    config: PullbackContinuationConfig,
) -> dict:
    zones = detect_supply_demand_zones(
        m15_frame.tail(240).reset_index(drop=True),
        symbol=symbol,
        timeframe="15m",
        impulse_multiplier=1.2,
        max_zones=10,
    )
    desired_type = "demand" if trade_side == "BUY" else "supply"
    impulse_time = pd.Timestamp(impulse.get("time")) if impulse.get("time") else None

    candidates: list[dict] = []
    for zone in reversed(zones):
        if str(zone.get("type") or "") != desired_type:
            continue

        formed_at = pd.Timestamp(zone["formed_at"])
        if impulse_time is not None and formed_at > impulse_time:
            continue

        zone_low = round(float(min(zone["start_price"], zone["end_price"])), 4)
        zone_high = round(float(max(zone["start_price"], zone["end_price"])), 4)
        zone_width = max(zone_high - zone_low, current_price * 0.00025)
        buffer_size = max(zone_width * 0.2, current_price * config.stop_buffer_ratio)
        proximity_limit = max(zone_width * config.max_zone_multiple, current_price * config.zone_proximity_ratio)

        if trade_side == "BUY":
            if current_price < zone_low - buffer_size:
                continue
            distance = max(0.0, current_price - zone_high)
            impulse_departure = float(impulse.get("close") or current_price) - zone_high
        else:
            if current_price > zone_high + buffer_size:
                continue
            distance = max(0.0, zone_low - current_price)
            impulse_departure = zone_low - float(impulse.get("close") or current_price)

        if distance > proximity_limit:
            continue
        if impulse_departure <= zone_width * 0.35:
            continue

        recency_penalty = max(0, len(candidates))
        score = 100 - (distance / max(proximity_limit, 1e-9) * 45) + float(impulse.get("body_ratio") or 0) * 8 - recency_penalty
        candidates.append(
            {
                "confirmed": True,
                "type": desired_type,
                "timeframe": "15m",
                "formed_at": formed_at.isoformat(),
                "zone_low": zone_low,
                "zone_high": zone_high,
                "zone_width": round(zone_width, 4),
                "distance": round(distance, 4),
                "score": round(score, 2),
                "plan_zone": [zone_low, zone_high],
                "order_block": {
                    "confirmed": True,
                    "zone": {
                        "start_price": zone_low,
                        "end_price": zone_high,
                        "formed_at": formed_at.isoformat(),
                        "timeframe": "15m",
                        "type": desired_type,
                    },
                },
            }
        )

    if not candidates:
        return {"confirmed": False, "reason": "No clean pullback zone close to price"}
    candidates.sort(key=lambda item: (-float(item["score"]), item["distance"], item["formed_at"]), reverse=False)
    return candidates[0]


def _build_trade_plan(
    *,
    trade_side: str,
    zone: dict,
    config: PullbackContinuationConfig,
) -> dict:
    zone_low = float(zone["zone_low"])
    zone_high = float(zone["zone_high"])
    zone_width = max(float(zone["zone_width"]), 1e-9)
    buffer_size = max(zone_width * 0.2, zone_high * config.stop_buffer_ratio)

    if trade_side == "BUY":
        entry = zone_high - (zone_width * config.zone_entry_ratio)
        stop_loss = zone_low - buffer_size
    else:
        entry = zone_low + (zone_width * config.zone_entry_ratio)
        stop_loss = zone_high + buffer_size

    if (trade_side == "BUY" and stop_loss >= entry) or (trade_side == "SELL" and stop_loss <= entry):
        return {"confirmed": False}
    return {"confirmed": True, "entry": round(entry, 4), "sl": round(stop_loss, 4)}


def _price_is_inside_zone(*, latest_price: float, zone_low: float | None, zone_high: float | None) -> bool:
    if zone_low is None or zone_high is None:
        return False
    return float(zone_low) <= latest_price <= float(zone_high)


def _detect_pullback_confirmation(
    data: pd.DataFrame,
    *,
    trade_side: str,
    zone_low: float,
    zone_high: float,
    minimum_body_ratio: float,
) -> dict:
    frame = data.tail(32).reset_index(drop=True)
    if len(frame) < 4:
        return {"confirmed": False}

    body_baseline = abs(frame["close"].astype(float) - frame["open"].astype(float)).tail(20).median()
    body_baseline = max(float(body_baseline), 1e-9)
    touch_index = None
    for index in range(len(frame) - 1, -1, -1):
        candle = frame.iloc[index]
        if float(candle["low"]) <= zone_high and float(candle["high"]) >= zone_low:
            touch_index = index
            break

    if touch_index is None:
        return {"confirmed": False}

    for index in range(max(1, touch_index), len(frame)):
        candle = frame.iloc[index]
        previous = frame.iloc[index - 1]
        open_price = float(candle["open"])
        close_price = float(candle["close"])
        body_ratio = abs(close_price - open_price) / body_baseline
        if body_ratio < minimum_body_ratio:
            continue

        if trade_side == "BUY":
            if close_price > open_price and close_price > float(previous["high"]):
                return {
                    "confirmed": True,
                    "time": pd.Timestamp(candle["time"]).isoformat(),
                    "entry": round(close_price, 4),
                    "body_ratio": round(body_ratio, 2),
                }
        else:
            if close_price < open_price and close_price < float(previous["low"]):
                return {
                    "confirmed": True,
                    "time": pd.Timestamp(candle["time"]).isoformat(),
                    "entry": round(close_price, 4),
                    "body_ratio": round(body_ratio, 2),
                }

    return {"confirmed": False, "touch_time": pd.Timestamp(frame.iloc[touch_index]["time"]).isoformat()}


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

    candidates = _find_target_levels(data, trade_side=trade_side, entry=entry)
    if not candidates:
        return {"confirmed": False, "reason": "No continuation target above minimum RR"}

    fallback = None
    for target in candidates:
        reward = abs(float(target["price"]) - entry)
        rr = reward / risk
        if rr >= preferred_rr:
            return {
                "confirmed": True,
                "tp": round(float(target["price"]), 4),
                "rr": round(rr, 2),
                "rr_floor": preferred_rr,
                "target_type": target["type"],
            }
        if rr >= minimum_rr and fallback is None:
            fallback = {
                "confirmed": True,
                "tp": round(float(target["price"]), 4),
                "rr": round(rr, 2),
                "rr_floor": minimum_rr,
                "target_type": target["type"],
            }

    if fallback is not None:
        return fallback
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

    unique: list[dict] = []
    seen: set[float] = set()
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
        "strategy": PULLBACK_STRATEGY,
        "strategies": [PULLBACK_STRATEGY],
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

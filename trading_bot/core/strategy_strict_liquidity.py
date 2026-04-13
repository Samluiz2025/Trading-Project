from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from trading_bot.core.market_structure import detect_market_structure, detect_swings, validate_ohlc_dataframe
from trading_bot.core.strategy_registry import PRIMARY_STRATEGY


@dataclass(frozen=True)
class StrictLiquidityConfig:
    symbol: str
    equal_level_tolerance_ratio: float = 0.0011
    stop_buffer_ratio: float = 0.00035
    entry_tolerance_ratio: float = 0.00065
    minimum_rr: float = 2.0
    preferred_rr: float = 3.0
    secondary_rr: float = 2.0
    allowed_sessions: tuple[str, ...] = ()


def build_strict_liquidity_config(symbol: str) -> StrictLiquidityConfig:
    normalized = str(symbol).upper()
    if normalized == "XAUUSD":
        return StrictLiquidityConfig(
            symbol=normalized,
            equal_level_tolerance_ratio=0.0018,
            stop_buffer_ratio=0.0009,
            entry_tolerance_ratio=0.00115,
        )
    if normalized.endswith("JPY"):
        return StrictLiquidityConfig(
            symbol=normalized,
            equal_level_tolerance_ratio=0.0014,
            stop_buffer_ratio=0.00055,
            entry_tolerance_ratio=0.00095,
        )
    return StrictLiquidityConfig(symbol=normalized)


def generate_strict_liquidity_setup(
    *,
    symbol: str,
    daily_data: pd.DataFrame,
    h1_data: pd.DataFrame,
    m15_data: pd.DataFrame | None,
    config: StrictLiquidityConfig | None = None,
) -> dict:
    active_config = config or build_strict_liquidity_config(symbol)
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

    m15_frame = m15_data if m15_data is not None else h1_data
    daily_frame = daily_data.tail(160).reset_index(drop=True)
    h1_frame = h1_data.tail(220).reset_index(drop=True)
    m15_frame = m15_frame.tail(320).reset_index(drop=True)

    session_context = _detect_session_context(pd.Timestamp(m15_frame.iloc[-1]["time"]), symbol=normalized_symbol)
    session_name = str(session_context.get("session") or "")
    if active_config.allowed_sessions and session_name not in active_config.allowed_sessions:
        return _no_trade(
            symbol=normalized_symbol,
            reason="Outside configured session filter",
            daily_bias=None,
            h1_bias=None,
            session=session_name,
            missing=["Outside configured session filter"],
        )

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

    h1_structure = detect_market_structure(h1_frame.tail(120).reset_index(drop=True))
    h1_bias = str(h1_structure.get("trend") or "")
    expected_trade_side = "BUY" if daily_bias == "bullish" else "SELL"
    liquidity_side = "sell_side" if expected_trade_side == "BUY" else "buy_side"
    liquidity = _find_equal_liquidity(
        h1_frame,
        side=liquidity_side,
        tolerance_ratio=active_config.equal_level_tolerance_ratio,
    )
    if not liquidity.get("confirmed"):
        return _no_trade(
            symbol=normalized_symbol,
            reason="No H1 equal highs/lows liquidity",
            daily_bias=daily_bias,
            h1_bias=h1_bias,
            session=session_name,
            missing=["No H1 liquidity pool"],
        )

    sweep = _detect_liquidity_sweep(
        h1_frame,
        trade_side=expected_trade_side,
        liquidity=liquidity,
        tolerance_ratio=active_config.equal_level_tolerance_ratio,
    )
    if not sweep.get("confirmed"):
        return _no_trade(
            symbol=normalized_symbol,
            reason="No H1 liquidity sweep",
            daily_bias=daily_bias,
            h1_bias=h1_bias,
            session=session_name,
            missing=["No H1 liquidity sweep"],
            details={"liquidity": liquidity},
        )

    m15_confirmation = _detect_m15_confirmation(
        m15_frame,
        trade_side=expected_trade_side,
        sweep_time=str(sweep.get("time")),
    )
    if not m15_confirmation.get("confirmed"):
        return _no_trade(
            symbol=normalized_symbol,
            reason="H1 sweep found. Waiting for M15 confirmation.",
            daily_bias=daily_bias,
            h1_bias=h1_bias,
            session=session_name,
            missing=["No M15 BOS confirmation"],
            status="WAIT_CONFIRMATION",
            details={
                "session": session_context,
                "liquidity": liquidity,
                "sweep": sweep,
                "confirmation_entry": {
                    "required": ["M15 BOS / CHOCH", "Displacement away from sweep"],
                    "message": "Wait for M15 confirmation before planning the entry zone.",
                },
            },
            bias=expected_trade_side,
            confidence="MEDIUM",
            confidence_score=64,
            confluences=["Daily Bias", "H1 Liquidity", "H1 Liquidity Sweep"],
            setup_type="Sweep waiting for confirmation",
            lifecycle="confirmation_watch",
            stalker={"state": "developing", "score": 72.0, "confirmed_checks": 3, "total_checks": 5},
            analysis_context={
                "session": session_name,
                "liquidity_level": liquidity.get("level"),
                "sweep_time": sweep.get("time"),
            },
        )

    entry_model = _build_entry_model(
        m15_frame,
        trade_side=expected_trade_side,
        confirmation=m15_confirmation,
        tolerance_ratio=active_config.entry_tolerance_ratio,
    )
    if not entry_model.get("confirmed"):
        return _no_trade(
            symbol=normalized_symbol,
            reason="No pullback entry after confirmation",
            daily_bias=daily_bias,
            h1_bias=h1_bias,
            session=session_name,
            missing=["No pullback entry"],
            details={"liquidity": liquidity, "sweep": sweep, "m15_confirmation": m15_confirmation},
        )

    entry = round(float(entry_model["entry"]), 4)
    stop_loss = round(_build_stop_loss(trade_side=expected_trade_side, sweep=sweep, config=active_config), 4)
    if (expected_trade_side == "BUY" and stop_loss >= entry) or (expected_trade_side == "SELL" and stop_loss <= entry):
        return _no_trade(
            symbol=normalized_symbol,
            reason="Invalid stop loss placement",
            daily_bias=daily_bias,
            h1_bias=h1_bias,
            session=session_name,
            missing=["Invalid stop loss"],
            details={"liquidity": liquidity, "sweep": sweep, "m15_confirmation": m15_confirmation, "entry_model": entry_model},
        )

    target = _select_take_profit(
        h1_frame,
        trade_side=expected_trade_side,
        entry=entry,
        stop_loss=stop_loss,
        minimum_rr=active_config.minimum_rr,
        preferred_rr=active_config.preferred_rr,
        secondary_rr=active_config.secondary_rr,
        strong_trend=_is_strong_trend(daily_structure=daily_structure, h1_structure=h1_structure, trade_side=expected_trade_side, confirmation=m15_confirmation),
    )
    if not target.get("confirmed"):
        return _no_trade(
            symbol=normalized_symbol,
            reason=str(target.get("reason") or "Target liquidity does not support minimum RR"),
            daily_bias=daily_bias,
            h1_bias=h1_bias,
            session=session_name,
            missing=["RR below 1:2"],
            details={"liquidity": liquidity, "sweep": sweep, "m15_confirmation": m15_confirmation, "entry_model": entry_model},
        )

    take_profit = round(float(target["tp"]), 4)
    risk_reward_ratio = round(float(target["rr"]), 2)
    setup_grade = "A+" if risk_reward_ratio >= active_config.preferred_rr else "B"
    confidence = "HIGH" if setup_grade == "A+" else "NORMAL"
    confidence_score = 92 if setup_grade == "A+" else 78
    setup_type = (
        "H1 sell-side sweep + M15 BOS"
        if expected_trade_side == "BUY"
        else "H1 buy-side sweep + M15 BOS"
    )
    reason = (
        "Daily bias, H1 sweep, and M15 confirmation align with the pullback entry."
        if setup_grade == "A+"
        else "Clean structure confirms the pullback and supports the closer liquidity target."
    )
    latest_price = float(m15_frame.iloc[-1]["close"])
    entry_zone = entry_model["entry_zone"]
    if not _price_in_entry_zone(
        latest_price=latest_price,
        entry_zone=entry_zone,
        trade_side=expected_trade_side,
        tolerance_ratio=active_config.entry_tolerance_ratio,
    ):
        return _no_trade(
            symbol=normalized_symbol,
            reason="Structure is aligned. Waiting for price to retrace into the entry zone.",
            daily_bias=daily_bias,
            h1_bias=h1_bias,
            session=session_name,
            missing=["No current entry at zone"],
            status="WAIT_CONFIRMATION",
            details={
                "session": session_context,
                "liquidity": liquidity,
                "sweep": sweep,
                "m15_confirmation": m15_confirmation,
                "entry_model": entry_model,
                "target": target,
                "plan_zone": entry_zone,
                "confirmation_entry": {
                    "required": ["Price retrace into entry zone", "Respect invalidation"],
                    "message": "Wait for price to revisit the entry zone before entering.",
                },
            },
            bias=expected_trade_side,
            entry=entry,
            sl=stop_loss,
            tp=take_profit,
            risk_reward_ratio=risk_reward_ratio,
            setup_grade=setup_grade,
            setup_type=setup_type,
            confidence="MEDIUM",
            confidence_score=82 if setup_grade == "A+" else 74,
            invalidation=stop_loss,
            confluences=[
                "Daily Bias",
                "H1 Liquidity",
                "H1 Liquidity Sweep",
                "M15 Confirmation",
                "Entry Zone Pending",
                f"RR 1:{int(target['rr_floor'])}",
            ],
            lifecycle="zone_watch",
            stalker={"state": "near_valid", "score": 88.0, "confirmed_checks": 5, "total_checks": 6},
            analysis_context={
                "session": session_name,
                "liquidity_level": liquidity.get("level"),
                "sweep_time": sweep.get("time"),
                "confirmation_time": m15_confirmation.get("time"),
                "entry_zone": entry_zone,
                "plan_zone": entry_zone,
            },
        )

    return {
        "status": "VALID_TRADE",
        "pair": normalized_symbol,
        "strategy": PRIMARY_STRATEGY,
        "strategies": [PRIMARY_STRATEGY],
        "message": "Valid trade setup available",
        "bias": expected_trade_side,
        "daily_bias": daily_bias,
        "h1_bias": h1_bias,
        "entry": entry,
        "sl": stop_loss,
        "tp": take_profit,
        "risk_reward_ratio": risk_reward_ratio,
        "setup_grade": setup_grade,
        "setup_type": setup_type,
        "session": session_name,
        "session_preferred": bool(session_context.get("preferred")),
        "confidence": confidence,
        "confidence_score": confidence_score,
        "invalidation": stop_loss,
        "reason": reason,
        "confluences": [
            "Daily Bias",
            "H1 Liquidity",
            "H1 Liquidity Sweep",
            "M15 Confirmation",
            "Pullback Entry",
            f"RR 1:{int(target['rr_floor'])}",
        ],
        "missing": [],
        "lifecycle": "entry_reached",
        "stalker": None,
        "details": {
            "daily_structure": daily_structure,
            "h1_structure": h1_structure,
            "session": session_context,
            "liquidity": liquidity,
            "sweep": sweep,
            "m15_confirmation": m15_confirmation,
            "entry_model": entry_model,
            "target": target,
        },
        "analysis_context": {
            "session": session_name,
            "liquidity_level": liquidity.get("level"),
            "sweep_time": sweep.get("time"),
            "confirmation_time": m15_confirmation.get("time"),
            "entry_zone": entry_zone,
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
    preferred = session in {"london", "new_york"}
    return {"session": session, "preferred": preferred, "symbol": symbol}


def _find_equal_liquidity(data: pd.DataFrame, *, side: str, tolerance_ratio: float) -> dict:
    swings = detect_swings(data.tail(160).reset_index(drop=True), swing_window=2)
    desired_type = "low" if side == "sell_side" else "high"
    levels = [item for item in swings if item["type"] == desired_type]
    if len(levels) < 2:
        return {"confirmed": False, "side": side}

    candidates: list[dict] = []
    for left, right in zip(levels[:-1], levels[1:]):
        left_price = float(left["price"])
        right_price = float(right["price"])
        average = max((left_price + right_price) / 2, 1e-9)
        diff_ratio = abs(left_price - right_price) / average
        if diff_ratio > tolerance_ratio:
            continue
        level = round((left_price + right_price) / 2, 4)
        candidates.append(
            {
                "confirmed": True,
                "side": side,
                "level": level,
                "first_index": int(left["index"]),
                "second_index": int(right["index"]),
                "first_time": pd.Timestamp(left["time"]).isoformat(),
                "second_time": pd.Timestamp(right["time"]).isoformat(),
                "label": "equal lows" if side == "sell_side" else "equal highs",
                "difference_ratio": round(diff_ratio, 6),
            }
        )

    if not candidates:
        return {"confirmed": False, "side": side}
    return max(candidates, key=lambda item: (item["second_index"], -item["difference_ratio"]))


def _detect_liquidity_sweep(data: pd.DataFrame, *, trade_side: str, liquidity: dict, tolerance_ratio: float) -> dict:
    if not liquidity.get("confirmed"):
        return {"confirmed": False}

    level = float(liquidity["level"])
    start_index = int(liquidity["second_index"]) + 1
    scan = data.iloc[start_index:].reset_index(drop=False)
    if scan.empty:
        return {"confirmed": False}

    events: list[dict] = []
    for _, row in scan.iterrows():
        index = int(row["index"])
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        if trade_side == "BUY":
            swept = low < (level * (1 - tolerance_ratio))
            reclaimed = close > level
            if not swept or not reclaimed:
                continue
            events.append(
                {
                    "confirmed": True,
                    "side": "sell_side",
                    "index": index,
                    "time": pd.Timestamp(row["time"]).isoformat(),
                    "level": round(level, 4),
                    "extreme": round(low, 4),
                    "close": round(close, 4),
                }
            )
        else:
            swept = high > (level * (1 + tolerance_ratio))
            reclaimed = close < level
            if not swept or not reclaimed:
                continue
            events.append(
                {
                    "confirmed": True,
                    "side": "buy_side",
                    "index": index,
                    "time": pd.Timestamp(row["time"]).isoformat(),
                    "level": round(level, 4),
                    "extreme": round(high, 4),
                    "close": round(close, 4),
                }
            )

    if not events:
        return {"confirmed": False}
    return events[-1]


def _detect_m15_confirmation(data: pd.DataFrame, *, trade_side: str, sweep_time: str) -> dict:
    sweep_timestamp = pd.Timestamp(sweep_time)
    post_sweep = data[data["time"] >= sweep_timestamp].reset_index(drop=True)
    if len(post_sweep) < 8:
        return {"confirmed": False}

    swings = detect_swings(post_sweep, swing_window=2)
    if not swings:
        return {"confirmed": False}

    swing_lookup = {int(item["index"]): item for item in swings}
    active_high = None
    active_low = None
    body_baseline = abs(post_sweep["close"].astype(float) - post_sweep["open"].astype(float)).tail(40).median()
    body_baseline = max(float(body_baseline), 1e-9)

    for index in range(len(post_sweep)):
        if index in swing_lookup:
            swing = swing_lookup[index]
            if swing["type"] == "high":
                active_high = swing
            else:
                active_low = swing

        candle = post_sweep.iloc[index]
        close = float(candle["close"])
        open_price = float(candle["open"])
        body = abs(close - open_price)

        if trade_side == "BUY" and active_high and index > int(active_high["index"]) and close > float(active_high["price"]):
            displacement_ratio = round(body / body_baseline, 2)
            if displacement_ratio < 1.05:
                continue
            return {
                "confirmed": True,
                "direction": "BUY",
                "index": int(index),
                "time": pd.Timestamp(candle["time"]).isoformat(),
                "break_level": round(float(active_high["price"]), 4),
                "close": round(close, 4),
                "displacement_ratio": displacement_ratio,
            }

        if trade_side == "SELL" and active_low and index > int(active_low["index"]) and close < float(active_low["price"]):
            displacement_ratio = round(body / body_baseline, 2)
            if displacement_ratio < 1.05:
                continue
            return {
                "confirmed": True,
                "direction": "SELL",
                "index": int(index),
                "time": pd.Timestamp(candle["time"]).isoformat(),
                "break_level": round(float(active_low["price"]), 4),
                "close": round(close, 4),
                "displacement_ratio": displacement_ratio,
            }

    return {"confirmed": False}


def _build_entry_model(data: pd.DataFrame, *, trade_side: str, confirmation: dict, tolerance_ratio: float) -> dict:
    if not confirmation.get("confirmed"):
        return {"confirmed": False}

    break_index = int(confirmation["index"])
    history = data.iloc[: break_index + 1]
    if trade_side == "BUY":
        opposite = history[history["close"] < history["open"]].tail(1)
    else:
        opposite = history[history["close"] > history["open"]].tail(1)

    if not opposite.empty:
        candle = opposite.iloc[-1]
        zone_low = round(float(candle["low"]), 4)
        zone_high = round(float(candle["high"]), 4)
        entry = round((zone_low + zone_high) / 2, 4)
        return {
            "confirmed": True,
            "type": "order_block_retest",
            "entry_zone": [zone_low, zone_high],
            "entry": entry,
        }

    break_level = float(confirmation["break_level"])
    padding = max(abs(break_level) * tolerance_ratio, 0.0001)
    zone_low = round(break_level - padding, 4)
    zone_high = round(break_level + padding, 4)
    return {
        "confirmed": True,
        "type": "retest",
        "entry_zone": [zone_low, zone_high],
        "entry": round((zone_low + zone_high) / 2, 4),
    }


def _price_in_entry_zone(*, latest_price: float, entry_zone: list[float], trade_side: str, tolerance_ratio: float) -> bool:
    if len(entry_zone) < 2:
        return False
    zone_low = float(min(entry_zone))
    zone_high = float(max(entry_zone))
    tolerance = max(abs(zone_high) * tolerance_ratio, 0.0001)
    return (zone_low - tolerance) <= latest_price <= (zone_high + tolerance)


def _build_stop_loss(*, trade_side: str, sweep: dict, config: StrictLiquidityConfig) -> float:
    level = float(sweep["extreme"])
    buffer_size = max(abs(level) * config.stop_buffer_ratio, 0.0001)
    if trade_side == "BUY":
        return level - buffer_size
    return level + buffer_size


def _select_take_profit(
    data: pd.DataFrame,
    *,
    trade_side: str,
    entry: float,
    stop_loss: float,
    minimum_rr: float,
    preferred_rr: float,
    secondary_rr: float,
    strong_trend: bool,
) -> dict:
    risk = abs(entry - stop_loss)
    if risk <= 0:
        return {"confirmed": False, "reason": "Invalid risk"}

    targets = _find_target_levels(data, trade_side=trade_side, entry=entry)
    if not targets:
        return {"confirmed": False, "reason": "No external liquidity target"}

    best_secondary = None
    for target in targets:
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
        if rr >= secondary_rr and strong_trend and best_secondary is None:
            best_secondary = {
                "confirmed": True,
                "tp": round(float(target["price"]), 4),
                "rr": round(rr, 2),
                "rr_floor": secondary_rr,
                "target_type": target["type"],
            }

    if best_secondary is not None:
        return best_secondary
    if minimum_rr > 0:
        return {"confirmed": False, "reason": "RR below 1:2"}
    return {"confirmed": False, "reason": "Target liquidity too close"}


def _find_target_levels(data: pd.DataFrame, *, trade_side: str, entry: float) -> list[dict]:
    swings = detect_swings(data.tail(180).reset_index(drop=True), swing_window=2)
    candidates: list[dict] = []
    if trade_side == "BUY":
        for swing in swings:
            price = float(swing["price"])
            if swing["type"] == "high" and price > entry:
                candidates.append({"price": round(price, 4), "type": "previous high"})
    else:
        for swing in swings:
            price = float(swing["price"])
            if swing["type"] == "low" and price < entry:
                candidates.append({"price": round(price, 4), "type": "previous low"})

    unique: list[dict] = []
    seen: set[float] = set()
    for item in sorted(candidates, key=lambda row: row["price"], reverse=trade_side == "SELL"):
        if item["price"] in seen:
            continue
        seen.add(item["price"])
        unique.append(item)
    if trade_side == "BUY":
        unique.sort(key=lambda row: row["price"])
    else:
        unique.sort(key=lambda row: row["price"], reverse=True)
    return unique


def _is_strong_trend(*, daily_structure: dict, h1_structure: dict, trade_side: str, confirmation: dict) -> bool:
    expected = "bullish" if trade_side == "BUY" else "bearish"
    return (
        str(daily_structure.get("trend") or "") == expected
        and str(h1_structure.get("trend") or "") == expected
        and float(confirmation.get("displacement_ratio") or 0) >= 1.2
    )


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
        "strategy": PRIMARY_STRATEGY,
        "strategies": [PRIMARY_STRATEGY],
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

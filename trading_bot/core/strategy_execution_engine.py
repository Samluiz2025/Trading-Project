from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pandas as pd

from trading_bot.concepts.bos import detect_bos_signals
from trading_bot.concepts.fvg import detect_fvg_signals
from trading_bot.concepts.liquidity import detect_liquidity_sweep_signals
from trading_bot.concepts.mss import detect_mss_signals
from trading_bot.concepts.order_block import detect_order_block_signals
from trading_bot.core.data_fetcher import FetchConfig, fetch_ohlc
from trading_bot.core.market_structure import detect_market_structure, detect_swings, validate_ohlc_dataframe
from trading_bot.core.supply_demand import detect_supply_demand_zones


@dataclass(frozen=True)
class ExecutionConfig:
    symbol: str
    source: str = "auto"
    daily_interval: str = "1d"
    primary_interval: str = "1h"
    fallback_interval: str = "4h"
    daily_limit: int = 220
    primary_limit: int = 300
    fallback_limit: int = 260
    bos_confirmation_count: int = 2
    equal_level_tolerance_ratio: float = 0.0008
    fvg_ob_distance_ratio: float = 0.003
    atr_period: int = 14
    tp_atr_multiplier: float = 2.5


def evaluate_strict_execution_setup(config: ExecutionConfig) -> dict:
    """
    Evaluate the complete strict ICT/SMC execution checklist.

    Daily determines bias and key zones. H1 must align and provide:
    MSS/BOS, liquidity taken, inducement, high-quality OB/BB, and nearby FVG.
    Entry is the 50% level of the OB/BB. Alerts should only fire when price
    reaches that level and every rule is satisfied.
    """

    daily_candles = fetch_ohlc(
        FetchConfig(
            symbol=config.symbol,
            interval=config.daily_interval,
            limit=config.daily_limit,
            source=config.source,  # type: ignore[arg-type]
        )
    )
    primary_candles = fetch_ohlc(
        FetchConfig(
            symbol=config.symbol,
            interval=config.primary_interval,
            limit=config.primary_limit,
            source=config.source,  # type: ignore[arg-type]
        )
    )
    fallback_candles = fetch_ohlc(
        FetchConfig(
            symbol=config.symbol,
            interval=config.fallback_interval,
            limit=config.fallback_limit,
            source=config.source,  # type: ignore[arg-type]
        )
    )

    validate_ohlc_dataframe(daily_candles)
    validate_ohlc_dataframe(primary_candles)
    validate_ohlc_dataframe(fallback_candles)

    daily_bias = detect_bias(daily_candles)
    daily_zones = detect_supply_demand_zones(daily_candles, symbol=config.symbol, timeframe=config.daily_interval)
    h1_bias = detect_bias(primary_candles)
    h4_bias = detect_bias(fallback_candles)
    execution_timeframe = config.primary_interval
    execution_candles = primary_candles
    execution_bias = h1_bias

    if not _biases_align(daily_bias["bias"], h1_bias["bias"]):
        if _biases_align(daily_bias["bias"], h4_bias["bias"]):
            execution_timeframe = config.fallback_interval
            execution_candles = fallback_candles
            execution_bias = h4_bias
        else:
            return _build_no_setup_payload(
                pair=config.symbol,
                bias=daily_bias["bias"],
                details="Neither H1 nor H4 aligns with Daily bias.",
                daily_bias=daily_bias,
                primary_bias=h1_bias,
                fallback_bias=h4_bias,
            )

    mss_result = detect_mss(execution_candles)
    bos_result = detect_bos(execution_candles, minimum_breaks=config.bos_confirmation_count)
    liquidity_result = detect_liquidity(
        execution_candles,
        tolerance_ratio=config.equal_level_tolerance_ratio,
    )
    inducement_result = detect_inducement(execution_candles)
    order_block_result = detect_order_block(
        execution_candles,
        symbol=config.symbol,
        timeframe=execution_timeframe,
    )
    breaker_block_result = detect_breaker_block(
        execution_candles,
        order_block_result=order_block_result,
    )
    active_block = breaker_block_result if breaker_block_result["confirmed"] else order_block_result
    fvg_result = detect_fvg(
        execution_candles,
        anchor_zone=active_block["zone"],
        max_distance_ratio=config.fvg_ob_distance_ratio,
    )

    directional_bias = daily_bias["bias"]
    entry_model = _build_entry_model(
        bias=directional_bias,
        block_result=active_block,
        fvg_result=fvg_result,
        daily_zones=daily_zones,
        execution_candles=execution_candles,
        atr_period=config.atr_period,
        tp_atr_multiplier=config.tp_atr_multiplier,
    )

    all_conditions = [
        mss_result["confirmed"] or bos_result["confirmed"],
        liquidity_result["confirmed"],
        inducement_result["confirmed"],
        active_block["confirmed"],
        fvg_result["confirmed"],
        entry_model["confirmed"],
    ]
    price_reached_entry = _price_reached_entry(
        current_price=float(execution_candles.iloc[-1]["close"]),
        candle=execution_candles.iloc[-1],
        entry=entry_model["entry"],
    )

    confluences = _build_confluences(
        bias=directional_bias,
        execution_timeframe=execution_timeframe,
        mss_result=mss_result,
        bos_result=bos_result,
        liquidity_result=liquidity_result,
        inducement_result=inducement_result,
        block_result=active_block,
        fvg_result=fvg_result,
        daily_zones=daily_zones,
    )

    confidence = _compute_confidence(
        all_conditions=all_conditions,
        price_reached_entry=price_reached_entry,
        bos_count=bos_result["bos_count"],
        block_score=active_block["quality_score"],
    )

    if not all(all_conditions) or not price_reached_entry:
        return _build_no_setup_payload(
            pair=config.symbol,
            bias=daily_bias["bias"],
            details="Full confluence or entry touch is missing.",
            daily_bias=daily_bias,
            primary_bias=execution_bias,
            fallback_bias=h4_bias,
            entry=entry_model["entry"],
            sl=entry_model["sl"],
            tp=entry_model["tp"],
            confluences=confluences,
            confidence=confidence,
            extra_details={
                "execution_timeframe": execution_timeframe,
                "mss": mss_result,
                "bos": bos_result,
                "liquidity": liquidity_result,
                "inducement": inducement_result,
                "order_block": order_block_result,
                "breaker_block": breaker_block_result,
                "active_block": active_block,
                "fvg": fvg_result,
                "price_reached_entry": price_reached_entry,
            },
        )

    return {
        "setup": "HIGH_PROBABILITY",
        "pair": config.symbol.upper(),
        "bias": directional_bias.upper(),
        "entry": entry_model["entry"],
        "sl": entry_model["sl"],
        "tp": entry_model["tp"],
        "confluences": confluences,
        "confidence": confidence,
        "daily_bias": daily_bias,
        "primary_bias": execution_bias,
        "fallback_bias": h4_bias,
        "execution_timeframe": execution_timeframe,
        "daily_zones": daily_zones[-4:],
        "details": {
            "mss": mss_result,
            "bos": bos_result,
            "liquidity": liquidity_result,
            "inducement": inducement_result,
            "order_block": order_block_result,
            "breaker_block": breaker_block_result,
            "active_block": active_block,
            "fvg": fvg_result,
            "price_reached_entry": price_reached_entry,
        },
    }


def detect_bias(dataframe: pd.DataFrame) -> dict:
    structure = detect_market_structure(dataframe)
    zones = detect_supply_demand_zones(dataframe, symbol="BIAS", timeframe="HTF")
    return {
        "bias": structure["trend"],
        "trend": structure["trend"],
        "last_HH": structure["last_HH"],
        "last_HL": structure["last_HL"],
        "last_LH": structure["last_LH"],
        "last_LL": structure["last_LL"],
        "zones": zones[-3:],
    }


def detect_mss(dataframe: pd.DataFrame) -> dict:
    signals = detect_mss_signals(dataframe)
    latest = signals[-1] if signals else None
    return {
        "confirmed": latest is not None,
        "signal": latest.signal if latest else None,
        "time": latest.time if latest else None,
    }


def detect_bos(dataframe: pd.DataFrame, minimum_breaks: int = 2) -> dict:
    signals = detect_bos_signals(dataframe)
    if not signals:
        return {"confirmed": False, "signal": None, "bos_count": 0, "time": None}

    latest_signal = signals[-1].signal
    consecutive = 0
    for signal in reversed(signals):
        if signal.signal != latest_signal:
            break
        consecutive += 1

    return {
        "confirmed": consecutive >= minimum_breaks,
        "signal": latest_signal,
        "bos_count": consecutive,
        "time": signals[-1].time,
    }


def detect_liquidity(dataframe: pd.DataFrame, tolerance_ratio: float = 0.0008) -> dict:
    sweeps = detect_liquidity_sweep_signals(dataframe)
    equal_levels = _detect_equal_highs_lows(dataframe, tolerance_ratio=tolerance_ratio)
    latest_sweep = sweeps[-1] if sweeps else None
    return {
        "confirmed": latest_sweep is not None and bool(equal_levels),
        "signal": latest_sweep.signal if latest_sweep else None,
        "sweep_time": latest_sweep.time if latest_sweep else None,
        "equal_levels": equal_levels[-4:],
    }


def detect_inducement(dataframe: pd.DataFrame) -> dict:
    swings = detect_swings(dataframe, swing_window=2)
    if len(swings) < 4:
        return {"confirmed": False, "trap_side": None, "levels": []}

    recent = swings[-4:]
    levels: list[float] = []
    trap_side = None
    confirmed = False

    if recent[-2]["type"] == "high" and recent[-1]["type"] == "low":
        pullback_depth = abs(float(recent[-1]["price"]) - float(recent[-2]["price"]))
        previous_leg = abs(float(recent[-2]["price"]) - float(recent[-3]["price"]))
        if previous_leg > 0 and pullback_depth / previous_leg < 0.6:
            confirmed = True
            trap_side = "SELL_SIDE_INDUCEMENT"
            levels = [round(float(recent[-2]["price"]), 4), round(float(recent[-1]["price"]), 4)]

    if recent[-2]["type"] == "low" and recent[-1]["type"] == "high":
        pullback_depth = abs(float(recent[-1]["price"]) - float(recent[-2]["price"]))
        previous_leg = abs(float(recent[-2]["price"]) - float(recent[-3]["price"]))
        if previous_leg > 0 and pullback_depth / previous_leg < 0.6:
            confirmed = True
            trap_side = "BUY_SIDE_INDUCEMENT"
            levels = [round(float(recent[-2]["price"]), 4), round(float(recent[-1]["price"]), 4)]

    return {
        "confirmed": confirmed,
        "trap_side": trap_side,
        "levels": levels,
    }


def detect_order_block(dataframe: pd.DataFrame, symbol: str, timeframe: str) -> dict:
    signals = detect_order_block_signals(dataframe, symbol=symbol, timeframe=timeframe)
    if not signals:
        return {"confirmed": False, "signal": None, "zone": None, "quality_score": 0}

    candidates = []
    average_range = float((dataframe["high"] - dataframe["low"]).mean())
    for signal in signals:
        zone = signal.metadata
        zone_low = min(zone["start_price"], zone["end_price"])
        zone_high = max(zone["start_price"], zone["end_price"])
        zone_size = max(zone_high - zone_low, 1e-9)
        displacement = abs(signal.take_profit - signal.entry)
        quality_score = round(min(1.0, (displacement / max(average_range, 1e-9)) / 3), 2)
        clean_zone = zone_size <= average_range * 1.5
        if quality_score >= 0.45 and clean_zone:
            candidates.append((quality_score, signal))

    if not candidates:
        return {"confirmed": False, "signal": None, "zone": None, "quality_score": 0}

    quality_score, best_signal = max(candidates, key=lambda item: item[0])
    zone = dict(best_signal.metadata)
    zone_type = zone["type"]
    block_type = "OB"
    if (zone_type == "supply" and best_signal.signal == "BUY") or (zone_type == "demand" and best_signal.signal == "SELL"):
        block_type = "BB"

    return {
        "confirmed": True,
        "signal": best_signal.signal,
        "zone": zone,
        "block_type": block_type,
        "quality_score": int(round(quality_score * 100)),
    }


def detect_breaker_block(dataframe: pd.DataFrame, order_block_result: dict) -> dict:
    zone = order_block_result.get("zone")
    if not zone:
        return {"confirmed": False, "signal": None, "zone": None, "block_type": "BB", "quality_score": 0}

    last_close = float(dataframe.iloc[-1]["close"])
    zone_low = min(zone["start_price"], zone["end_price"])
    zone_high = max(zone["start_price"], zone["end_price"])
    block_signal = order_block_result.get("signal")

    if block_signal == "BUY" and last_close < zone_low:
        flipped_zone = {**zone, "type": "supply"}
        return {
            "confirmed": True,
            "signal": "SELL",
            "zone": flipped_zone,
            "block_type": "BB",
            "quality_score": max(55, order_block_result.get("quality_score", 0)),
        }
    if block_signal == "SELL" and last_close > zone_high:
        flipped_zone = {**zone, "type": "demand"}
        return {
            "confirmed": True,
            "signal": "BUY",
            "zone": flipped_zone,
            "block_type": "BB",
            "quality_score": max(55, order_block_result.get("quality_score", 0)),
        }

    return {"confirmed": False, "signal": None, "zone": None, "block_type": "BB", "quality_score": 0}


def detect_fvg(dataframe: pd.DataFrame, anchor_zone: dict | None, max_distance_ratio: float = 0.003) -> dict:
    if anchor_zone is None:
        return {"confirmed": False, "signal": None, "gap": None}

    signals = detect_fvg_signals(dataframe)
    if not signals:
        return {"confirmed": False, "signal": None, "gap": None}

    zone_mid = _zone_midpoint(anchor_zone)
    for signal in reversed(signals):
        distance_ratio = abs(signal.entry - zone_mid) / max(zone_mid, 1e-9)
        if distance_ratio <= max_distance_ratio:
            return {
                "confirmed": True,
                "signal": signal.signal,
                "gap": {
                    "entry": round(signal.entry, 4),
                    "stop_loss": round(signal.stop_loss, 4),
                    "take_profit": round(signal.take_profit, 4),
                    "time": signal.time,
                    "metadata": signal.metadata,
                },
            }

    return {"confirmed": False, "signal": None, "gap": None}


def format_high_setup_alert(result: dict) -> str | None:
    if result.get("setup") != "HIGH_PROBABILITY":
        return None

    confluences = "\n".join(f"- {item}" for item in result.get("confluences", []))
    return (
        "[HIGH SETUP]\n"
        f"Pair: {result['pair']}\n"
        f"Bias: {result['bias']}\n"
        f"Entry: {result['entry']:.4f}\n"
        f"SL: {result['sl']:.4f}\n"
        f"TP: {result['tp']:.4f}\n"
        "Confluences:\n"
        f"{confluences}"
    )


def _build_entry_model(
    bias: str,
    block_result: dict,
    fvg_result: dict,
    daily_zones: list[dict],
    execution_candles: pd.DataFrame,
    atr_period: int,
    tp_atr_multiplier: float,
) -> dict:
    zone = block_result.get("zone")
    if zone is None:
        return {"confirmed": False, "entry": None, "sl": None, "tp": None}

    zone_low = min(zone["start_price"], zone["end_price"])
    zone_high = max(zone["start_price"], zone["end_price"])
    entry = round((zone_low + zone_high) / 2, 4)

    zone_size = max(zone_high - zone_low, entry * 0.001)
    atr = _calculate_atr(execution_candles, period=atr_period)
    atr_component = atr * tp_atr_multiplier
    htf_target = _find_htf_target(bias=bias, daily_zones=daily_zones, entry=entry)
    if "bullish" in bias:
        sl = round(zone_low - (zone_size * 0.2), 4)
        projected_tp = entry + max((entry - sl) * 2.5, atr_component)
        tp = round(max(projected_tp, htf_target or projected_tp), 4)
    else:
        sl = round(zone_high + (zone_size * 0.2), 4)
        projected_tp = entry - max((sl - entry) * 2.5, atr_component)
        tp = round(min(projected_tp, htf_target) if htf_target is not None else projected_tp, 4)

    confirmed = fvg_result["confirmed"] and block_result["confirmed"]
    return {
        "confirmed": confirmed,
        "entry": entry,
        "sl": sl,
        "tp": tp,
    }


def _build_confluences(
    bias: str,
    execution_timeframe: str,
    mss_result: dict,
    bos_result: dict,
    liquidity_result: dict,
    inducement_result: dict,
    block_result: dict,
    fvg_result: dict,
    daily_zones: list[dict],
) -> list[str]:
    confluences = [f"HTF Bias {bias.upper()}", f"{execution_timeframe.upper()} bias aligned"]
    if daily_zones:
        confluences.append("Daily key zone")
    if mss_result["confirmed"]:
        confluences.append("MSS")
    if bos_result["confirmed"]:
        confluences.append(f"Multiple BOS ({bos_result['bos_count']})")
    if liquidity_result["confirmed"]:
        confluences.append("Liquidity sweep")
        if liquidity_result["equal_levels"]:
            confluences.append("Equal highs/lows")
    if inducement_result["confirmed"]:
        confluences.append("Inducement")
    if block_result["confirmed"] and fvg_result["confirmed"]:
        confluences.append(f"{block_result['block_type']} + FVG")
    elif block_result["confirmed"]:
        confluences.append(block_result["block_type"])
    return confluences


def _compute_confidence(
    all_conditions: list[bool],
    price_reached_entry: bool,
    bos_count: int,
    block_score: int,
) -> int:
    score = 35 + sum(9 for condition in all_conditions if condition)
    score += min(10, bos_count * 2)
    score += min(10, block_score // 10)
    if price_reached_entry:
        score += 10
    return min(100, score)


def _detect_equal_highs_lows(dataframe: pd.DataFrame, tolerance_ratio: float) -> list[dict]:
    swings = detect_swings(dataframe, swing_window=2)
    equal_levels: list[dict] = []

    for left, right in zip(swings[:-1], swings[1:]):
        if left["type"] != right["type"]:
            continue
        midpoint = (float(left["price"]) + float(right["price"])) / 2
        if midpoint == 0:
            continue
        distance_ratio = abs(float(left["price"]) - float(right["price"])) / midpoint
        if distance_ratio <= tolerance_ratio:
            equal_levels.append(
                {
                    "type": f"equal_{left['type']}s",
                    "first": round(float(left["price"]), 4),
                    "second": round(float(right["price"]), 4),
                }
            )

    return equal_levels


def _price_reached_entry(current_price: float, candle: pd.Series, entry: float | None) -> bool:
    if entry is None:
        return False
    candle_low = float(candle["low"])
    candle_high = float(candle["high"])
    return candle_low <= entry <= candle_high or abs(current_price - entry) / max(entry, 1e-9) <= 0.0005


def _zone_midpoint(zone: dict) -> float:
    return (min(zone["start_price"], zone["end_price"]) + max(zone["start_price"], zone["end_price"])) / 2


def _biases_align(daily_bias: str, primary_bias: str) -> bool:
    if "bullish" in daily_bias and "bullish" in primary_bias:
        return True
    if "bearish" in daily_bias and "bearish" in primary_bias:
        return True
    return False


def _calculate_atr(dataframe: pd.DataFrame, period: int) -> float:
    high_low = dataframe["high"] - dataframe["low"]
    high_close = (dataframe["high"] - dataframe["close"].shift(1)).abs()
    low_close = (dataframe["low"] - dataframe["close"].shift(1)).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = true_range.rolling(period).mean().iloc[-1]
    if pd.isna(atr):
        atr = true_range.mean()
    return float(atr)


def _find_htf_target(bias: str, daily_zones: list[dict], entry: float) -> float | None:
    if not daily_zones:
        return None

    if "bullish" in bias:
        candidates = [max(zone["start_price"], zone["end_price"]) for zone in daily_zones if max(zone["start_price"], zone["end_price"]) > entry]
        return min(candidates) if candidates else None

    candidates = [min(zone["start_price"], zone["end_price"]) for zone in daily_zones if min(zone["start_price"], zone["end_price"]) < entry]
    return max(candidates) if candidates else None


def _build_no_setup_payload(
    pair: str,
    bias: str,
    details: str,
    daily_bias: dict,
    primary_bias: dict,
    fallback_bias: dict | None = None,
    entry: float | None = None,
    sl: float | None = None,
    tp: float | None = None,
    confluences: list[str] | None = None,
    confidence: int = 0,
    extra_details: dict | None = None,
) -> dict:
    return {
        "setup": "WAIT",
        "pair": pair.upper(),
        "bias": bias.upper(),
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "confluences": confluences or [details],
        "confidence": confidence,
        "daily_bias": daily_bias,
        "primary_bias": primary_bias,
        "fallback_bias": fallback_bias,
        "details": {
            "reason": details,
            "generated_at": datetime.now(UTC).isoformat(),
            **(extra_details or {}),
        },
    }

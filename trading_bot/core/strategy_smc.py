from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from trading_bot.core.market_structure import detect_market_structure, detect_swings, validate_ohlc_dataframe


@dataclass(frozen=True)
class SmcConfig:
    symbol: str
    bos_min_count: int = 2
    ob_padding_ratio: float = 0.0007
    fvg_distance_ratio: float = 0.0025
    inducement_lookback: int = 24
    risk_reward_ratio: float = 4.0


def detect_daily_bias(data: pd.DataFrame) -> dict:
    validate_ohlc_dataframe(data)
    structure = detect_market_structure(data)
    if structure["trend"] in {"bullish", "bearish"}:
        return {"bias": structure["trend"], "method": "structure", "structure": structure}

    recent = data.tail(10).reset_index(drop=True)
    open_price = float(recent.iloc[0]["open"])
    close_price = float(recent.iloc[-1]["close"])
    bias = "bullish" if close_price >= open_price else "bearish"
    return {
        "bias": bias,
        "method": "candle_direction",
        "structure": structure,
        "reference": {
            "window_open": round(open_price, 4),
            "window_close": round(close_price, 4),
        },
    }


def detect_bos(data: pd.DataFrame, bias: str) -> list[dict]:
    validate_ohlc_dataframe(data)
    swings = detect_swings(data, swing_window=2)
    swing_lookup = {int(item["index"]): item for item in swings}
    bos_events: list[dict] = []
    active_high = None
    active_low = None

    for index in range(len(data)):
        if index in swing_lookup:
            swing = swing_lookup[index]
            if swing["type"] == "high":
                active_high = swing
            else:
                active_low = swing

        candle = data.iloc[index]
        close_price = float(candle["close"])

        if "bullish" in bias and active_high and index > int(active_high["index"]) and close_price > float(active_high["price"]):
            bos_events.append(
                {
                    "direction": "BUY",
                    "index": int(index),
                    "time": pd.Timestamp(candle["time"]).isoformat(),
                    "break_level": round(float(active_high["price"]), 4),
                    "close": round(close_price, 4),
                }
            )
            active_high = None

        if "bearish" in bias and active_low and index > int(active_low["index"]) and close_price < float(active_low["price"]):
            bos_events.append(
                {
                    "direction": "SELL",
                    "index": int(index),
                    "time": pd.Timestamp(candle["time"]).isoformat(),
                    "break_level": round(float(active_low["price"]), 4),
                    "close": round(close_price, 4),
                }
            )
            active_low = None

    return bos_events


def detect_multiple_bos(data: pd.DataFrame, bias: str, minimum_count: int = 2) -> dict:
    events = detect_bos(data, bias=bias)
    return {
        "confirmed": len(events) >= minimum_count,
        "count": len(events),
        "events": events,
    }


def detect_last_structure_break(data: pd.DataFrame, bias: str) -> dict:
    events = detect_bos(data, bias=bias)
    if not events:
        return {"confirmed": False, "event": None}
    return {"confirmed": True, "event": events[-1]}


def detect_mss(data: pd.DataFrame, bias: str) -> dict:
    validate_ohlc_dataframe(data)
    structure = detect_market_structure(data.tail(80).reset_index(drop=True))
    signal = None
    confirmed = False
    if "bullish" in bias and structure["trend"] == "bullish":
        signal = "BUY"
        confirmed = True
    elif "bearish" in bias and structure["trend"] == "bearish":
        signal = "SELL"
        confirmed = True
    return {"confirmed": confirmed, "signal": signal, "trend": structure["trend"]}


def detect_inducement(data: pd.DataFrame, bias: str, break_index: int | None = None, lookback: int = 24) -> dict:
    """
    Strict inducement:
    BUY -> price breaks below a minor low and closes back above it.
    SELL -> price breaks above a minor high and closes back below it.
    """

    validate_ohlc_dataframe(data)
    end_index = break_index if break_index is not None else len(data) - 1
    swings = detect_swings(data.iloc[: end_index + 1].reset_index(drop=True), swing_window=2)
    if len(swings) < 3:
        return {"confirmed": False, "level": None, "zone": None}

    candidate_swings = swings[-lookback:] if len(swings) > lookback else swings
    if "bullish" in bias:
        minor_lows = [item for item in candidate_swings if item["type"] == "low"]
        for swing in reversed(minor_lows):
            level = float(swing["price"])
            for index in range(int(swing["index"]) + 1, len(data.iloc[: end_index + 1])):
                candle = data.iloc[index]
                if float(candle["low"]) < level and float(candle["close"]) > level:
                    return {
                        "confirmed": True,
                        "level": round(level, 4),
                        "zone": [round(float(candle["low"]), 4), round(level, 4)],
                        "time": pd.Timestamp(candle["time"]).isoformat(),
                    }
    else:
        minor_highs = [item for item in candidate_swings if item["type"] == "high"]
        for swing in reversed(minor_highs):
            level = float(swing["price"])
            for index in range(int(swing["index"]) + 1, len(data.iloc[: end_index + 1])):
                candle = data.iloc[index]
                if float(candle["high"]) > level and float(candle["close"]) < level:
                    return {
                        "confirmed": True,
                        "level": round(level, 4),
                        "zone": [round(level, 4), round(float(candle["high"]), 4)],
                        "time": pd.Timestamp(candle["time"]).isoformat(),
                    }

    return {"confirmed": False, "level": None, "zone": None}


def detect_order_block(data: pd.DataFrame, bias: str, break_index: int | None, padding_ratio: float = 0.0007) -> dict:
    """
    Order block = the last opposite candle before the impulse BOS.

    We reject already mitigated blocks by checking whether price later traded
    decisively through the far edge of the zone.
    """

    validate_ohlc_dataframe(data)
    if break_index is None or break_index < 2:
        return {"confirmed": False, "zone": None}

    search = data.iloc[max(0, break_index - 12):break_index]
    if search.empty:
        return {"confirmed": False, "zone": None}

    if "bullish" in bias:
        opposite = search[search["close"] < search["open"]]
    else:
        opposite = search[search["close"] > search["open"]]
    if opposite.empty:
        return {"confirmed": False, "zone": None}

    candidate = opposite.iloc[-1]
    zone_low = float(candidate["low"])
    zone_high = float(candidate["high"])
    zone_size = max(zone_high - zone_low, zone_high * padding_ratio)
    post_break = data.iloc[break_index + 1 :]

    if "bullish" in bias:
        mitigated = not post_break.empty and bool((post_break["low"] < (zone_low - zone_size * 0.1)).any())
    else:
        mitigated = not post_break.empty and bool((post_break["high"] > (zone_high + zone_size * 0.1)).any())

    if mitigated:
        return {"confirmed": False, "zone": None}

    return {
        "confirmed": True,
        "zone": {
            "start_price": round(zone_low, 4),
            "end_price": round(zone_high, 4),
            "formed_at": pd.Timestamp(candidate["time"]).isoformat(),
        },
        "index": int(candidate.name),
    }


def detect_fvg(data: pd.DataFrame, bias: str, order_block: dict | None, distance_ratio: float = 0.0025) -> dict:
    """
    Detect a directional FVG that stays near the active OB and is not fully filled.
    """

    validate_ohlc_dataframe(data)
    if not order_block or not order_block.get("confirmed"):
        return {"confirmed": False, "zone": None}

    zone = order_block["zone"]
    ob_mid = (float(zone["start_price"]) + float(zone["end_price"])) / 2

    for index in range(2, len(data)):
        first = data.iloc[index - 2]
        third = data.iloc[index]
        if "bullish" in bias and float(third["low"]) > float(first["high"]):
            gap_low = float(first["high"])
            gap_high = float(third["low"])
            gap_filled = bool((data.iloc[index + 1 :]["low"] <= gap_low).any()) if index + 1 < len(data) else False
        elif "bearish" in bias and float(third["high"]) < float(first["low"]):
            gap_low = float(third["high"])
            gap_high = float(first["low"])
            gap_filled = bool((data.iloc[index + 1 :]["high"] >= gap_high).any()) if index + 1 < len(data) else False
        else:
            continue

        gap_mid = (gap_low + gap_high) / 2
        if abs(gap_mid - ob_mid) / max(ob_mid, 1e-9) <= distance_ratio and not gap_filled:
            return {
                "confirmed": True,
                "zone": [round(gap_low, 4), round(gap_high, 4)],
                "time": pd.Timestamp(third["time"]).isoformat(),
            }

    return {"confirmed": False, "zone": None}


def generate_trade_setup(symbol: str, daily_data: pd.DataFrame, h1_data: pd.DataFrame, m30_data: pd.DataFrame | None = None, config: SmcConfig | None = None) -> dict:
    active_config = config or SmcConfig(symbol=symbol)
    daily_bias = detect_daily_bias(daily_data)
    h1_structure = detect_market_structure(h1_data.tail(120).reset_index(drop=True))
    mss = detect_mss(h1_data, daily_bias["bias"])
    bos = detect_multiple_bos(h1_data, daily_bias["bias"], minimum_count=active_config.bos_min_count)
    last_break = detect_last_structure_break(h1_data, daily_bias["bias"])
    inducement = detect_inducement(h1_data, daily_bias["bias"], break_index=last_break["event"]["index"] if last_break["confirmed"] else None, lookback=active_config.inducement_lookback)
    order_block = detect_order_block(h1_data, daily_bias["bias"], break_index=last_break["event"]["index"] if last_break["confirmed"] else None, padding_ratio=active_config.ob_padding_ratio)
    fvg = detect_fvg(h1_data, daily_bias["bias"], order_block=order_block, distance_ratio=active_config.fvg_distance_ratio)

    h1_aligned = (
        ("bullish" in daily_bias["bias"] and h1_structure["trend"] == "bullish")
        or ("bearish" in daily_bias["bias"] and h1_structure["trend"] == "bearish")
    )

    missing: list[str] = []
    if daily_bias["bias"] not in {"bullish", "bearish"}:
        missing.append("Daily Bias")
    if not h1_aligned:
        missing.append("Bias mismatch")
    if not (bos["confirmed"] or mss["confirmed"]):
        missing.append("No BOS/MSS")
    if not last_break["confirmed"]:
        missing.append("No last structure break")
    if not inducement["confirmed"]:
        missing.append("No inducement")
    if not order_block["confirmed"]:
        missing.append("No OB")
    if not fvg["confirmed"]:
        missing.append("No FVG")

    if missing:
        return {
            "status": "NO TRADE",
            "strategy": "SMC",
            "message": "No valid setup available",
            "missing": missing,
            "bias": daily_bias["bias"].upper(),
            "details": {
                "daily_bias": daily_bias,
                "h1_structure": h1_structure,
                "mss": mss,
                "bos": bos,
                "last_break": last_break,
                "inducement": inducement,
                "order_block": order_block,
                "fvg": fvg,
                "refinement": _build_m30_refinement(m30_data, daily_bias["bias"]),
            },
        }

    ob_zone = order_block["zone"]
    zone_low = float(ob_zone["start_price"])
    zone_high = float(ob_zone["end_price"])
    entry = round((zone_low + zone_high) / 2, 4)
    padding = max(abs(zone_high - zone_low) * 0.15, entry * active_config.ob_padding_ratio)
    bias = "BUY" if "bullish" in daily_bias["bias"] else "SELL"
    if bias == "BUY":
        sl = round(zone_low - padding, 4)
        risk = abs(entry - sl)
        tp = round(entry + (risk * active_config.risk_reward_ratio), 4)
    else:
        sl = round(zone_high + padding, 4)
        risk = abs(sl - entry)
        tp = round(entry - (risk * active_config.risk_reward_ratio), 4)

    return {
        "status": "VALID_TRADE",
        "strategy": "SMC",
        "pair": symbol.upper(),
        "bias": bias,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "confluences": [
            "Daily Bias",
            "H1 Structure",
            "BOS/MSS",
            "Inducement Confirmed",
            "Order Block",
            "FVG",
        ],
        "confidence_score": 72,
        "risk_reward_ratio": active_config.risk_reward_ratio,
        "details": {
            "daily_bias": daily_bias,
            "h1_structure": h1_structure,
            "mss": mss,
            "bos": bos,
            "last_break": last_break,
            "inducement": inducement,
            "order_block": order_block,
            "fvg": fvg,
            "refinement": _build_m30_refinement(m30_data, daily_bias["bias"]),
            "risk_reward_ratio": active_config.risk_reward_ratio,
        },
    }


def _build_m30_refinement(m30_data: pd.DataFrame | None, bias: str) -> dict:
    if m30_data is None or len(m30_data) < 20:
        return {"used": False}
    structure = detect_market_structure(m30_data.tail(60).reset_index(drop=True))
    aligned = ("bullish" in bias and structure["trend"] == "bullish") or ("bearish" in bias and structure["trend"] == "bearish")
    return {"used": True, "aligned": aligned, "trend": structure["trend"]}

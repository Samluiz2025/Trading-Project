from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from trading_bot.core.data_fetcher import FetchConfig, fetch_ohlc
from trading_bot.core.market_structure import detect_market_structure, detect_swings, validate_ohlc_dataframe


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
ALERT_STATE_PATH = DATA_DIR / "daily_h1_continuation_alerts.json"


@dataclass(frozen=True)
class ContinuationConfig:
    symbol: str
    source: str = "auto"
    daily_interval: str = "1d"
    execution_interval: str = "1h"
    daily_limit: int = 220
    execution_limit: int = 320
    minimum_bos_count: int = 2
    ob_padding_ratio: float = 0.0006
    fvg_ob_distance_ratio: float = 0.0025
    lookback_candles: int = 80


def detect_daily_bias(data: pd.DataFrame) -> dict:
    """
    Determine Daily bias using a simple, stable rule set.

    The method prefers market structure first. If structure is ranging, it falls
    back to the latest close relative to a 20-period average and the direction
    of the latest candle body.
    """

    validate_ohlc_dataframe(data)
    structure = detect_market_structure(data)
    if structure["trend"] in {"bullish", "bearish"}:
        return {
            "bias": structure["trend"],
            "method": "structure",
            "reference": structure,
        }

    sample = data.tail(20).copy()
    sma20 = float(sample["close"].mean())
    last = sample.iloc[-1]
    if float(last["close"]) >= sma20 and float(last["close"]) >= float(last["open"]):
        bias = "bullish"
    elif float(last["close"]) < sma20 and float(last["close"]) <= float(last["open"]):
        bias = "bearish"
    else:
        bias = "neutral"

    return {
        "bias": bias,
        "method": "sma20_candle_direction",
        "reference": {
            "sma20": round(sma20, 4),
            "last_open": round(float(last["open"]), 4),
            "last_close": round(float(last["close"]), 4),
        },
    }


def detect_bos(data: pd.DataFrame, bias: str) -> list[dict]:
    """
    Detect BOS events in the requested direction.

    For a bullish continuation, a BOS occurs when price closes above the latest
    confirmed swing high. For a bearish continuation, it closes below the latest
    confirmed swing low. This keeps the rule explicit and aligned with your
    continuation model.
    """

    validate_ohlc_dataframe(data)
    swings = detect_swings(data, swing_window=2)
    bos_events: list[dict] = []
    last_swing_high = None
    last_swing_low = None
    swing_lookup = {int(item["index"]): item for item in swings}

    for index in range(len(data)):
        if index in swing_lookup:
            swing = swing_lookup[index]
            if swing["type"] == "high":
                last_swing_high = swing
            else:
                last_swing_low = swing

        candle = data.iloc[index]
        close_price = float(candle["close"])

        if "bullish" in bias and last_swing_high and index > int(last_swing_high["index"]) and close_price > float(last_swing_high["price"]):
            bos_events.append(
                {
                    "direction": "BUY",
                    "index": int(index),
                    "time": pd.Timestamp(candle["time"]).isoformat(),
                    "break_level": round(float(last_swing_high["price"]), 4),
                    "close": round(close_price, 4),
                }
            )
            last_swing_high = None

        if "bearish" in bias and last_swing_low and index > int(last_swing_low["index"]) and close_price < float(last_swing_low["price"]):
            bos_events.append(
                {
                    "direction": "SELL",
                    "index": int(index),
                    "time": pd.Timestamp(candle["time"]).isoformat(),
                    "break_level": round(float(last_swing_low["price"]), 4),
                    "close": round(close_price, 4),
                }
            )
            last_swing_low = None

    return bos_events


def detect_multiple_bos(data: pd.DataFrame, bias: str, minimum_count: int = 2) -> dict:
    events = detect_bos(data, bias=bias)
    directional = [event for event in events if event["direction"] == ("BUY" if "bullish" in bias else "SELL")]
    return {
        "confirmed": len(directional) >= minimum_count,
        "count": len(directional),
        "events": directional,
    }


def detect_last_structure_break(data: pd.DataFrame, bias: str) -> dict:
    events = detect_bos(data, bias=bias)
    if not events:
        return {"confirmed": False, "event": None}
    return {"confirmed": True, "event": events[-1]}


def detect_inducement(data: pd.DataFrame, bias: str, break_index: int | None = None, lookback: int = 20) -> dict:
    """
    Detect inducement as a strict liquidity-grab candle before the impulse.

    Buy model:
    - choose a bearish pullback candle before the latest bullish BOS
    - later candle must trade below that low
    - then close back inside the pullback candle range

    Sell model mirrors this:
    - use a bullish pullback candle
    - later candle trades above that high
    - then closes back inside the original candle range
    """

    validate_ohlc_dataframe(data)
    end_index = break_index if break_index is not None else len(data) - 1
    start_index = max(1, end_index - lookback)
    window = data.iloc[start_index:end_index].reset_index(drop=False)

    candidate = None
    sweep_candle = None
    if "bullish" in bias:
        for position in range(len(window) - 2, -1, -1):
            candle = window.iloc[position]
            if float(candle["close"]) < float(candle["open"]):
                candle_low = float(candle["low"])
                candle_high = float(candle["high"])
                for sweep_position in range(position + 1, len(window)):
                    sweep = window.iloc[sweep_position]
                    if float(sweep["low"]) < candle_low and candle_low <= float(sweep["close"]) <= candle_high:
                        candidate = candle
                        sweep_candle = sweep
                        break
            if candidate is not None:
                break
    else:
        for position in range(len(window) - 2, -1, -1):
            candle = window.iloc[position]
            if float(candle["close"]) > float(candle["open"]):
                candle_low = float(candle["low"])
                candle_high = float(candle["high"])
                for sweep_position in range(position + 1, len(window)):
                    sweep = window.iloc[sweep_position]
                    if float(sweep["high"]) > candle_high and candle_low <= float(sweep["close"]) <= candle_high:
                        candidate = candle
                        sweep_candle = sweep
                        break
            if candidate is not None:
                break

    if candidate is None or sweep_candle is None:
        return {"confirmed": False, "zone": None, "sweep": None}

    return {
        "confirmed": True,
        "zone": [
            round(float(candidate["low"]), 4),
            round(float(candidate["high"]), 4),
        ],
        "inducement_level": round(
            float(candidate["low"] if "bullish" in bias else candidate["high"]),
            4,
        ),
        "candle_time": pd.Timestamp(candidate["time"]).isoformat(),
        "sweep": {
            "time": pd.Timestamp(sweep_candle["time"]).isoformat(),
            "low": round(float(sweep_candle["low"]), 4),
            "high": round(float(sweep_candle["high"]), 4),
            "close": round(float(sweep_candle["close"]), 4),
        },
    }


def detect_order_block(data: pd.DataFrame, bias: str, break_index: int | None = None, padding_ratio: float = 0.0006) -> dict:
    """
    Identify the last clean opposite candle before the impulse BOS.

    This approximates a continuation order block by selecting the final candle
    against the trend immediately before the structure break, then rejecting it
    if price has already traded too deeply through the zone afterwards.
    """

    validate_ohlc_dataframe(data)
    if break_index is None or break_index <= 1:
        return {"confirmed": False, "zone": None}

    search = data.iloc[max(0, break_index - 12):break_index]
    if search.empty:
        return {"confirmed": False, "zone": None}

    candidate = None
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
        mitigated = not post_break.empty and bool((post_break["low"] < (zone_low - zone_size * 0.15)).any())
        signal = "BUY"
    else:
        mitigated = not post_break.empty and bool((post_break["high"] > (zone_high + zone_size * 0.15)).any())
        signal = "SELL"

    if mitigated:
        return {"confirmed": False, "zone": None}

    return {
        "confirmed": True,
        "signal": signal,
        "zone": {
            "start_price": round(zone_low, 4),
            "end_price": round(zone_high, 4),
            "formed_at": pd.Timestamp(candidate["time"]).isoformat(),
        },
        "index": int(candidate.name),
        "impulse_reference_index": int(break_index),
    }


def detect_fvg(data: pd.DataFrame, bias: str, order_block: dict | None = None, max_distance_ratio: float = 0.0025) -> dict:
    """
    Detect a directional three-candle FVG near the active order block.

    The relationship is strict: the imbalance must be in the same direction as
    the bias and close enough to the OB midpoint to be considered part of the
    same continuation structure.
    """

    validate_ohlc_dataframe(data)
    if not order_block or not order_block.get("confirmed"):
        return {"confirmed": False, "zone": None}

    zone = order_block["zone"]
    ob_mid = (float(zone["start_price"]) + float(zone["end_price"])) / 2

    for index in range(len(data) - 1, 1, -1):
        first = data.iloc[index - 2]
        third = data.iloc[index]

        if "bullish" in bias and float(third["low"]) > float(first["high"]):
            gap_low = float(first["high"])
            gap_high = float(third["low"])
        elif "bearish" in bias and float(third["high"]) < float(first["low"]):
            gap_low = float(third["high"])
            gap_high = float(first["low"])
        else:
            continue

        gap_mid = (gap_low + gap_high) / 2
        distance_ratio = abs(gap_mid - ob_mid) / max(ob_mid, 1e-9)
        if distance_ratio <= max_distance_ratio:
            return {
                "confirmed": True,
                "zone": [round(gap_low, 4), round(gap_high, 4)],
                "midpoint": round(gap_mid, 4),
                "time": pd.Timestamp(third["time"]).isoformat(),
            }

    return {"confirmed": False, "zone": None}


def generate_trade_setup(config: ContinuationConfig) -> dict:
    daily_data = fetch_ohlc(
        FetchConfig(
            symbol=config.symbol,
            interval=config.daily_interval,
            limit=config.daily_limit,
            source=config.source,  # type: ignore[arg-type]
        )
    )
    h1_data = fetch_ohlc(
        FetchConfig(
            symbol=config.symbol,
            interval=config.execution_interval,
            limit=config.execution_limit,
            source=config.source,  # type: ignore[arg-type]
        )
    )

    validate_ohlc_dataframe(daily_data)
    validate_ohlc_dataframe(h1_data)

    daily_bias = detect_daily_bias(daily_data)
    if daily_bias["bias"] not in {"bullish", "bearish"}:
        return _wait_payload(config.symbol, "Daily bias is neutral.", daily_bias)

    h1_structure = detect_market_structure(h1_data.tail(config.lookback_candles).reset_index(drop=True))
    if h1_structure["trend"] != daily_bias["bias"]:
        return _wait_payload(config.symbol, "H1 is not aligned with Daily bias.", daily_bias, h1_structure)

    bos_state = detect_multiple_bos(h1_data, bias=daily_bias["bias"], minimum_count=config.minimum_bos_count)
    if not bos_state["confirmed"]:
        return _wait_payload(config.symbol, "Multiple H1 BOS are not confirmed.", daily_bias, h1_structure, bos_state=bos_state)

    last_break = detect_last_structure_break(h1_data, bias=daily_bias["bias"])
    if not last_break["confirmed"]:
        return _wait_payload(config.symbol, "No recent H1 structure break found.", daily_bias, h1_structure, bos_state=bos_state)

    break_event = last_break["event"]
    inducement = detect_inducement(h1_data, bias=daily_bias["bias"], break_index=break_event["index"])
    if not inducement["confirmed"]:
        return _wait_payload(
            config.symbol,
            "Inducement is not confirmed.",
            daily_bias,
            h1_structure,
            bos_state=bos_state,
            last_break=last_break,
        )

    order_block = detect_order_block(
        h1_data,
        bias=daily_bias["bias"],
        break_index=break_event["index"],
        padding_ratio=config.ob_padding_ratio,
    )
    if not order_block["confirmed"]:
        return _wait_payload(
            config.symbol,
            "No valid unmitigated order block found.",
            daily_bias,
            h1_structure,
            bos_state=bos_state,
            last_break=last_break,
            inducement=inducement,
        )

    fvg = detect_fvg(
        h1_data,
        bias=daily_bias["bias"],
        order_block=order_block,
        max_distance_ratio=config.fvg_ob_distance_ratio,
    )
    if not fvg["confirmed"]:
        return _wait_payload(
            config.symbol,
            "No valid FVG near the order block.",
            daily_bias,
            h1_structure,
            bos_state=bos_state,
            last_break=last_break,
            inducement=inducement,
            order_block=order_block,
        )

    zone = order_block["zone"]
    zone_low = float(zone["start_price"])
    zone_high = float(zone["end_price"])
    entry = round((zone_low + zone_high) / 2, 4)
    zone_padding = max(abs(zone_high - zone_low) * 0.15, entry * config.ob_padding_ratio)

    if daily_bias["bias"] == "bullish":
        bias = "BUY"
        sl = round(zone_low - zone_padding, 4)
        tp = _find_previous_target(h1_data, side="BUY", reference_index=break_event["index"], fallback=max(h1_data["high"].tail(30)))
    else:
        bias = "SELL"
        sl = round(zone_high + zone_padding, 4)
        tp = _find_previous_target(h1_data, side="SELL", reference_index=break_event["index"], fallback=min(h1_data["low"].tail(30)))

    confluences = [
        "Daily Bias",
        "Multiple BOS",
        "Inducement Confirmed",
        "Order Block",
        "FVG",
    ]
    confidence = _compute_confidence(bos_state["count"])

    setup = {
        "pair": config.symbol.upper(),
        "bias": bias,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "confluences": confluences,
        "confidence": confidence,
        "timestamp": datetime.now(UTC).isoformat(),
        "details": {
            "daily_bias": daily_bias,
            "h1_structure": h1_structure,
            "bos": bos_state,
            "last_structure_break": last_break,
            "inducement": inducement,
            "order_block": order_block,
            "fvg": fvg,
        },
    }
    setup["signature"] = _build_setup_signature(setup)
    setup["is_new_alert"] = _mark_alert_if_new(setup["signature"])
    return setup


def _find_previous_target(data: pd.DataFrame, side: str, reference_index: int, fallback: float) -> float:
    swings = detect_swings(data, swing_window=2)
    if side == "BUY":
        highs = [item for item in swings if item["type"] == "high" and int(item["index"]) < reference_index]
        if highs:
            return round(float(highs[-1]["price"]), 4)
    else:
        lows = [item for item in swings if item["type"] == "low" and int(item["index"]) < reference_index]
        if lows:
            return round(float(lows[-1]["price"]), 4)
    return round(float(fallback), 4)


def _compute_confidence(bos_count: int) -> int:
    return int(np.clip(80 + (bos_count - 2) * 5, 80, 95))


def _wait_payload(
    symbol: str,
    reason: str,
    daily_bias: dict,
    h1_structure: dict | None = None,
    **details,
) -> dict:
    return {
        "pair": symbol.upper(),
        "bias": "BUY" if daily_bias.get("bias") == "bullish" else "SELL" if daily_bias.get("bias") == "bearish" else "NEUTRAL",
        "entry": None,
        "sl": None,
        "tp": None,
        "confluences": [],
        "confidence": 0,
        "timestamp": datetime.now(UTC).isoformat(),
        "status": "WAIT",
        "reason": reason,
        "details": {
            "daily_bias": daily_bias,
            "h1_structure": h1_structure,
            **details,
        },
    }


def _build_setup_signature(setup: dict) -> str:
    return "|".join(
        [
            setup["pair"],
            setup["bias"],
            f"{setup['entry']:.4f}",
            f"{setup['sl']:.4f}",
            f"{setup['tp']:.4f}",
        ]
    )


def _mark_alert_if_new(signature: str) -> bool:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if ALERT_STATE_PATH.exists():
        seen = set(json.loads(ALERT_STATE_PATH.read_text(encoding="utf-8")))
    else:
        seen = set()

    if signature in seen:
        return False

    seen.add(signature)
    ALERT_STATE_PATH.write_text(json.dumps(sorted(seen), indent=2), encoding="utf-8")
    return True

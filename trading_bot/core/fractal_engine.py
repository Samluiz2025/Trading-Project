from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Any

import pandas as pd

from trading_bot.core.market_structure import detect_market_structure, detect_swings, validate_ohlc_dataframe


@dataclass(frozen=True)
class FractalConfig:
    symbol: str
    box_lookback: int = 48
    historical_windows: int = 18
    forecast_horizon: int = 12
    similarity_count: int = 5
    sweep_tolerance_ratio: float = 0.0015
    validation_samples: int = 10
    breakout_threshold: float = 0.03
    edge_touch_ratio: float = 0.08


def build_fractal_config(symbol: str) -> FractalConfig:
    normalized = symbol.upper()
    if normalized in {"BTCUSDT", "ETHUSDT"}:
        return FractalConfig(
            symbol=normalized,
            box_lookback=56,
            historical_windows=24,
            forecast_horizon=14,
            validation_samples=14,
            breakout_threshold=0.035,
            edge_touch_ratio=0.07,
        )
    if normalized in {"SP500", "NAS100", "US30"}:
        return FractalConfig(
            symbol=normalized,
            box_lookback=46,
            historical_windows=24,
            forecast_horizon=12,
            validation_samples=14,
            breakout_threshold=0.03,
            edge_touch_ratio=0.07,
        )
    if normalized in {"XAUUSD"}:
        return FractalConfig(
            symbol=normalized,
            box_lookback=44,
            historical_windows=20,
            forecast_horizon=10,
            validation_samples=12,
            breakout_threshold=0.028,
            edge_touch_ratio=0.075,
        )
    if normalized in {"GBPJPY", "AUDJPY", "CHFJPY", "USDJPY"}:
        return FractalConfig(
            symbol=normalized,
            box_lookback=42,
            historical_windows=22,
            forecast_horizon=10,
            validation_samples=12,
            breakout_threshold=0.022,
            edge_touch_ratio=0.065,
        )
    if normalized in {"EURUSD", "GBPUSD", "USDCHF", "AUDUSD", "NZDUSD", "EURGBP", "EURNZD", "GBPAUD", "AUDCAD", "NZDJPY", "CADJPY", "GBPCHF"}:
        return FractalConfig(
            symbol=normalized,
            box_lookback=40,
            historical_windows=20,
            forecast_horizon=9,
            validation_samples=10,
            breakout_threshold=0.015,
            edge_touch_ratio=0.06,
        )
    return FractalConfig(symbol=normalized)


def analyze_fractal_context(symbol: str, daily_data: pd.DataFrame, h4_data: pd.DataFrame | None = None, config: FractalConfig | None = None) -> dict[str, Any]:
    """Build a structural analog/fractal read for the current market box and likely next path."""

    active_config = config or build_fractal_config(symbol)
    validate_ohlc_dataframe(daily_data)
    if h4_data is not None:
        validate_ohlc_dataframe(h4_data)

    base = daily_data.tail(max(active_config.box_lookback + active_config.historical_windows + active_config.forecast_horizon + 30, 120)).reset_index(drop=True)
    if len(base) < active_config.box_lookback + active_config.forecast_horizon + 10:
        return _empty_fractal(symbol, "Not enough history for fractal context.")

    current_window = base.tail(active_config.box_lookback).reset_index(drop=True)
    current_features = _extract_box_features(current_window, active_config)
    analogs = _find_analogs(base, current_features, active_config)
    scenario = _build_scenario_summary(analogs, current_features)
    short_term = _extract_short_term_context(h4_data, active_config) if h4_data is not None else {}
    validation = _build_validation_report(base, active_config)

    return {
        "symbol": symbol.upper(),
        "status": "ok",
        "timeframe": "1d",
        "current_box": current_features,
        "scenario": scenario,
        "analogs": analogs,
        "validation": validation,
        "short_term": short_term,
        "message": _fractal_message(current_features, scenario),
    }


def _extract_box_features(window: pd.DataFrame, config: FractalConfig) -> dict[str, Any]:
    structure = detect_market_structure(window)
    swings = detect_swings(window, swing_window=2)
    highs = [float(item["price"]) for item in swings if item["type"] == "high"]
    lows = [float(item["price"]) for item in swings if item["type"] == "low"]
    range_high = float(window["high"].max())
    range_low = float(window["low"].min())
    range_size = max(range_high - range_low, 1e-9)
    atr = float((window["high"] - window["low"]).tail(14).mean())
    close_price = float(window.iloc[-1]["close"])
    open_price = float(window.iloc[0]["open"])
    close_location = (close_price - range_low) / range_size
    sweep_top = _has_top_sweep(window, range_high, config.sweep_tolerance_ratio)
    sweep_bottom = _has_bottom_sweep(window, range_low, config.sweep_tolerance_ratio)
    top_touches = _count_edge_touches(highs, range_high, range_size, config.edge_touch_ratio)
    bottom_touches = _count_edge_touches(lows, range_low, range_size, config.edge_touch_ratio)
    rotations = _count_rotations(swings, range_high, range_low)
    trend = structure["trend"]
    directional_drift = (close_price - open_price) / range_size
    box_type = _classify_box(trend=trend, close_location=close_location, directional_drift=directional_drift, sweep_top=sweep_top, sweep_bottom=sweep_bottom, top_touches=top_touches, bottom_touches=bottom_touches)
    motifs = _detect_structure_motifs(
        symbol=config.symbol,
        swings=swings,
        range_high=range_high,
        range_low=range_low,
        range_size=range_size,
        box_type=box_type,
        close_location=close_location,
        directional_drift=directional_drift,
    )

    return {
        "range_high": round(range_high, 4),
        "range_low": round(range_low, 4),
        "range_size": round(range_size, 4),
        "atr": round(atr, 4),
        "atr_multiple": round(range_size / max(atr, 1e-9), 2),
        "close_location": round(close_location, 2),
        "top_touches": top_touches,
        "bottom_touches": bottom_touches,
        "rotations": rotations,
        "sweep_top": sweep_top,
        "sweep_bottom": sweep_bottom,
        "trend": trend,
        "directional_drift": round(directional_drift, 2),
        "box_type": box_type,
        "premium_discount": "premium" if close_location >= 0.5 else "discount",
        "motifs": motifs,
    }


def _find_analogs(data: pd.DataFrame, current_features: dict[str, Any], config: FractalConfig) -> list[dict[str, Any]]:
    analogs: list[dict[str, Any]] = []
    latest_start = len(data) - config.box_lookback
    for start in range(max(0, latest_start - config.historical_windows * 6), max(0, latest_start - config.forecast_horizon)):
        end = start + config.box_lookback
        future_end = end + config.forecast_horizon
        if future_end > len(data):
            break
        window = data.iloc[start:end].reset_index(drop=True)
        future = data.iloc[end:future_end].reset_index(drop=True)
        candidate = _extract_box_features(window, config)
        score = _similarity_score(current_features, candidate)
        outcome = _future_outcome(window.iloc[-1]["close"], future, config)
        analogs.append(
            {
                "window_start": pd.Timestamp(window.iloc[0]["time"]).isoformat(),
                "window_end": pd.Timestamp(window.iloc[-1]["time"]).isoformat(),
                "similarity_score": round(score, 2),
                "box_type": candidate["box_type"],
                "trend": candidate["trend"],
                "rotations": candidate["rotations"],
                "future_outcome": outcome,
            }
        )

    analogs.sort(key=lambda item: item["similarity_score"], reverse=True)
    return analogs[: config.similarity_count]


def _future_outcome(reference_close: float, future: pd.DataFrame, config: FractalConfig) -> dict[str, Any]:
    future_high = float(future["high"].max())
    future_low = float(future["low"].min())
    future_close = float(future.iloc[-1]["close"])
    upside_move = (future_high - reference_close) / max(abs(reference_close), 1e-9)
    downside_move = (reference_close - future_low) / max(abs(reference_close), 1e-9)
    close_move = (future_close - reference_close) / max(abs(reference_close), 1e-9)

    if downside_move > upside_move * 1.2 and downside_move > config.breakout_threshold:
        label = "bearish_breakdown"
    elif upside_move > downside_move * 1.2 and upside_move > config.breakout_threshold:
        label = "bullish_breakout"
    else:
        label = "range_continuation"

    return {
        "label": label,
        "future_high": round(future_high, 4),
        "future_low": round(future_low, 4),
        "future_close": round(future_close, 4),
        "close_move_percent": round(close_move * 100, 2),
    }


def _build_scenario_summary(analogs: list[dict[str, Any]], current_features: dict[str, Any]) -> dict[str, Any]:
    if not analogs:
        return {
            "path_bias": "neutral",
            "breakdown_probability": 0.0,
            "breakout_probability": 0.0,
            "range_probability": 0.0,
            "dominant_theme": "No analogs found.",
        }

    weights = [max(float(item["similarity_score"]), 0.01) for item in analogs]
    total = sum(weights)
    breakdown = sum(weight for weight, item in zip(weights, analogs) if item["future_outcome"]["label"] == "bearish_breakdown") / total
    breakout = sum(weight for weight, item in zip(weights, analogs) if item["future_outcome"]["label"] == "bullish_breakout") / total
    range_prob = sum(weight for weight, item in zip(weights, analogs) if item["future_outcome"]["label"] == "range_continuation") / total

    path_family = "standard"
    phase_one_bias = path_bias = "neutral"
    phase_two_bias = "neutral"
    active_trade_bias = "neutral"
    active_phase = "standard"
    phase_sequence: list[str] = []

    if breakdown >= max(breakout, range_prob):
        path_bias = "bearish"
        theme = f"Current {current_features['box_type']} resembles past downside continuation/distribution boxes."
        phase_one_bias = "bearish"
        active_trade_bias = "bearish"
    elif breakout >= max(breakdown, range_prob):
        path_bias = "bullish"
        theme = f"Current {current_features['box_type']} resembles past accumulation/re-accumulation boxes."
        phase_one_bias = "bullish"
        active_trade_bias = "bullish"
    else:
        path_bias = "neutral"
        theme = f"Current {current_features['box_type']} most often stayed rotational before the next expansion."

    motifs = current_features.get("motifs") or []
    special_path = next((motif for motif in motifs if motif.get("name") == "distribution_to_lower_box_then_reversal"), None)
    equity_corrective_path = next((motif for motif in motifs if motif.get("name") == "equity_corrective_drop_sequence"), None)
    if special_path is not None:
        path_bias = "bearish"
        path_family = "distribution_to_lower_box_then_reversal"
        phase_one_bias = "bearish"
        phase_two_bias = "bullish"
        phase_sequence = ["distribution", "markdown_to_lower_box", "lower_box_accumulation", "bullish_reversal"]
        if bool(current_features.get("sweep_bottom")) and float(current_features.get("close_location") or 0.0) <= 0.35:
            active_trade_bias = "bullish"
            active_phase = "lower_box_reversal_phase"
        else:
            active_trade_bias = "bearish"
            active_phase = "markdown_phase"
        theme = (
            "Current structure resembles a distribution ledge that typically seeks a lower discount box first, "
            "then attempts a larger reversal after that lower box is built."
        )
    elif equity_corrective_path is not None:
        path_bias = "bearish"
        path_family = "equity_corrective_drop_sequence"
        phase_one_bias = "bearish"
        phase_two_bias = "neutral"
        phase_sequence = ["corrective_top", "failed_rebound", "support_loss", "sharp_drop"]
        active_trade_bias = "bearish"
        active_phase = "corrective_breakdown_phase"
        theme = (
            "Current index structure resembles prior corrective top sequences where repeated swing rotations lose momentum, "
            "rebound attempts fail, and the next major leg resolves sharply lower."
        )
    if motifs:
        theme = f"{theme} Active motifs: {', '.join(motif['label'] for motif in motifs[:2])}."

    return {
        "path_bias": path_bias,
        "path_family": path_family,
        "phase_one_bias": phase_one_bias,
        "phase_two_bias": phase_two_bias,
        "active_trade_bias": active_trade_bias,
        "active_phase": active_phase,
        "phase_sequence": phase_sequence,
        "breakdown_probability": round(breakdown * 100, 2),
        "breakout_probability": round(breakout * 100, 2),
        "range_probability": round(range_prob * 100, 2),
        "dominant_theme": theme,
        "confidence": round(max(breakdown, breakout, range_prob) * 100, 2),
    }


def _detect_structure_motifs(
    *,
    symbol: str,
    swings: list[dict[str, Any]],
    range_high: float,
    range_low: float,
    range_size: float,
    box_type: str,
    close_location: float,
    directional_drift: float,
) -> list[dict[str, Any]]:
    motifs: list[dict[str, Any]] = []
    if len(swings) < 6:
        return motifs

    recent = swings[-8:]
    highs = [float(item["price"]) for item in recent if item["type"] == "high"]
    lows = [float(item["price"]) for item in recent if item["type"] == "low"]
    tolerance = max(range_size * 0.08, 1e-9)

    if highs and box_type in {"distribution", "continuation_range", "compression_range"}:
        high_cluster = sum(1 for price in highs if abs(price - range_high) <= tolerance)
        if high_cluster >= 2 and len(lows) >= 2:
            lower_lows = lows[-1] < lows[-2]
            motifs.append(
                {
                    "name": "dbos_distribution_ledge",
                    "label": "Dbos Distribution Ledge",
                    "bias": "bearish",
                    "confidence": 72 if lower_lows else 61,
                    "description": "Highs keep leaning against the upper ledge while internal lows start giving way.",
                }
            )
        if high_cluster >= 2 and len(highs) >= 2 and len(lows) >= 2:
            lower_highs = highs[-1] < highs[-2]
            lower_lows = lows[-1] < lows[-2]
            if lower_highs and lower_lows and 0.45 <= close_location <= 0.8 and directional_drift <= 0.12:
                motifs.append(
                    {
                        "name": "distribution_to_lower_box_then_reversal",
                        "label": "Distribution -> Lower Box -> Reversal",
                        "bias": "bearish",
                        "confidence": 78 if box_type == "distribution" else 69,
                        "description": (
                            "The structure is behaving like a local distribution ledge that often breaks lower first, "
                            "builds a discount box, then attempts a larger upside reversal."
                        ),
                        "path_sequence": ["distribution", "markdown_to_lower_box", "lower_box_accumulation", "bullish_reversal"],
                    }
                )

    if lows and box_type in {"accumulation", "continuation_range", "compression_range"}:
        low_cluster = sum(1 for price in lows if abs(price - range_low) <= tolerance)
        if low_cluster >= 2 and len(highs) >= 2:
            higher_highs = highs[-1] > highs[-2]
            motifs.append(
                {
                    "name": "dbos_accumulation_ledge",
                    "label": "Dbos Accumulation Ledge",
                    "bias": "bullish",
                    "confidence": 72 if higher_highs else 61,
                    "description": "Lows keep leaning against the lower ledge while internal highs start lifting.",
                }
            )

    rotation_bias = _impulsive_corrective_bias(recent)
    if rotation_bias == "bullish":
        motifs.append(
            {
                "name": "impulsive_corrective_bullish",
                "label": "Impulsive / Corrective Bullish",
                "bias": "bullish",
                "confidence": 68,
                "description": "Upswings are expanding while pullbacks stay corrective and shallow.",
            }
        )
    elif rotation_bias == "bearish":
        motifs.append(
            {
                "name": "impulsive_corrective_bearish",
                "label": "Impulsive / Corrective Bearish",
                "bias": "bearish",
                "confidence": 68,
                "description": "Downswings are expanding while bounces stay corrective and weak.",
            }
        )

    if str(symbol).upper() in {"SP500", "NAS100", "US30"}:
        equity_sequence = _detect_equity_corrective_sequence(
            swings=recent,
            highs=highs,
            lows=lows,
            range_high=range_high,
            range_low=range_low,
            range_size=range_size,
            close_location=close_location,
            box_type=box_type,
        )
        if equity_sequence is not None:
            motifs.append(equity_sequence)

    motifs.sort(key=lambda item: float(item.get("confidence") or 0), reverse=True)
    return motifs[:3]


def _detect_equity_corrective_sequence(
    *,
    swings: list[dict[str, Any]],
    highs: list[float],
    lows: list[float],
    range_high: float,
    range_low: float,
    range_size: float,
    close_location: float,
    box_type: str,
) -> dict[str, Any] | None:
    if len(swings) < 7 or len(highs) < 3 or len(lows) < 3:
        return None

    recent_highs = highs[-3:]
    recent_lows = lows[-3:]
    lower_highs = recent_highs[0] > recent_highs[1] > recent_highs[2]
    lower_lows = recent_lows[0] > recent_lows[1] > recent_lows[2]
    upper_half_failures = sum(1 for price in recent_highs if price >= range_low + (range_size * 0.62))
    midpoint_loss = recent_lows[-1] <= range_low + (range_size * 0.42)
    alternating_rotations = _swing_alternation_score(swings[-7:]) >= 5

    if not (lower_highs and lower_lows and alternating_rotations):
        return None
    if upper_half_failures < 2 or not midpoint_loss:
        return None
    if close_location > 0.72 and box_type not in {"distribution", "continuation_range", "compression_range"}:
        return None

    confidence = 76
    if box_type == "distribution":
        confidence += 6
    return {
        "name": "equity_corrective_drop_sequence",
        "label": "Equity Corrective Drop Sequence",
        "bias": "bearish",
        "confidence": confidence,
        "description": (
            "The index is tracing a corrective top rhythm with repeated failed rebounds, lower highs, lower lows, "
            "and a structure that often resolves into a sharper downside leg."
        ),
        "path_sequence": ["corrective_top", "failed_rebound", "support_loss", "sharp_drop"],
    }


def _swing_alternation_score(swings: list[dict[str, Any]]) -> int:
    if len(swings) < 2:
        return 0
    score = 0
    previous_type = swings[0]["type"]
    for swing in swings[1:]:
        current_type = swing["type"]
        if current_type != previous_type:
            score += 1
        previous_type = current_type
    return score


def _impulsive_corrective_bias(swings: list[dict[str, Any]]) -> str | None:
    if len(swings) < 6:
        return None
    moves = []
    for left, right in zip(swings[:-1], swings[1:]):
        move = float(right["price"]) - float(left["price"])
        if abs(move) > 0:
            moves.append(move)
    if len(moves) < 4:
        return None

    positive = [abs(move) for move in moves if move > 0]
    negative = [abs(move) for move in moves if move < 0]
    if len(positive) < 2 or len(negative) < 2:
        return None

    avg_positive = sum(positive) / len(positive)
    avg_negative = sum(negative) / len(negative)
    if avg_positive > avg_negative * 1.25 and moves[-1] > 0:
        return "bullish"
    if avg_negative > avg_positive * 1.25 and moves[-1] < 0:
        return "bearish"
    return None


def _extract_short_term_context(h4_data: pd.DataFrame, config: FractalConfig) -> dict[str, Any]:
    short_window = h4_data.tail(min(len(h4_data), max(30, config.box_lookback // 2))).reset_index(drop=True)
    if len(short_window) < 10:
        return {}
    structure = detect_market_structure(short_window)
    return {
        "h4_trend": structure["trend"],
        "recent_high": round(float(short_window["high"].max()), 4),
        "recent_low": round(float(short_window["low"].min()), 4),
        "recent_close": round(float(short_window.iloc[-1]["close"]), 4),
    }


def _similarity_score(current: dict[str, Any], candidate: dict[str, Any]) -> float:
    distance = 0.0
    distance += abs(float(current["close_location"]) - float(candidate["close_location"])) * 2.2
    distance += abs(float(current["atr_multiple"]) - float(candidate["atr_multiple"])) * 0.45
    distance += abs(int(current["rotations"]) - int(candidate["rotations"])) * 0.6
    distance += abs(int(current["top_touches"]) - int(candidate["top_touches"])) * 0.4
    distance += abs(int(current["bottom_touches"]) - int(candidate["bottom_touches"])) * 0.4
    distance += abs(float(current["directional_drift"]) - float(candidate["directional_drift"])) * 1.5
    if current["trend"] != candidate["trend"]:
        distance += 1.2
    if current["box_type"] != candidate["box_type"]:
        distance += 1.4
    if bool(current["sweep_top"]) != bool(candidate["sweep_top"]):
        distance += 0.7
    if bool(current["sweep_bottom"]) != bool(candidate["sweep_bottom"]):
        distance += 0.7
    return max(0.0, 100.0 - (distance * 12.5))


def _classify_box(*, trend: str, close_location: float, directional_drift: float, sweep_top: bool, sweep_bottom: bool, top_touches: int, bottom_touches: int) -> str:
    if trend == "bearish" and close_location > 0.62 and sweep_top and top_touches >= 2:
        return "distribution"
    if trend == "bullish" and close_location < 0.38 and sweep_bottom and bottom_touches >= 2:
        return "accumulation"
    if abs(directional_drift) < 0.2 and top_touches >= 2 and bottom_touches >= 2:
        return "compression_range"
    if trend in {"bullish", "bearish"}:
        return "continuation_range"
    return "transition_box"


def _count_edge_touches(levels: list[float], edge: float, range_size: float, edge_touch_ratio: float) -> int:
    tolerance = max(range_size * edge_touch_ratio, 1e-9)
    return sum(1 for value in levels if abs(value - edge) <= tolerance)


def _count_rotations(swings: list[dict[str, Any]], range_high: float, range_low: float) -> int:
    if len(swings) < 2:
        return 0
    range_size = max(range_high - range_low, 1e-9)
    normalized = []
    for swing in swings:
        location = (float(swing["price"]) - range_low) / range_size
        normalized.append(1 if location >= 0.65 else -1 if location <= 0.35 else 0)

    rotations = 0
    previous = 0
    for side in normalized:
        if side == 0:
            continue
        if previous != 0 and side != previous:
            rotations += 1
        previous = side
    return rotations


def _has_top_sweep(window: pd.DataFrame, range_high: float, tolerance_ratio: float) -> bool:
    closes = window["close"].astype(float)
    highs = window["high"].astype(float)
    threshold = range_high * (1 - tolerance_ratio)
    return bool((highs.tail(8) >= threshold).any() and (closes.tail(8) < range_high).any())


def _has_bottom_sweep(window: pd.DataFrame, range_low: float, tolerance_ratio: float) -> bool:
    closes = window["close"].astype(float)
    lows = window["low"].astype(float)
    threshold = range_low * (1 + tolerance_ratio)
    return bool((lows.tail(8) <= threshold).any() and (closes.tail(8) > range_low).any())


def _fractal_message(current_features: dict[str, Any], scenario: dict[str, Any]) -> str:
    motifs = current_features.get("motifs") or []
    motif_text = f" Leading motif: {motifs[0]['label']}." if motifs else ""
    phase_text = ""
    if scenario.get("path_family") == "distribution_to_lower_box_then_reversal":
        phase_text = " Expected path: lower box first, larger reversal later."
    elif scenario.get("path_family") == "equity_corrective_drop_sequence":
        phase_text = " Expected path: corrective breakdown rhythm with a sharper downside leg next."
    return (
        f"Current box is {current_features['box_type']} with {current_features['rotations']} internal rotations. "
        f"Analog bias is {scenario['path_bias']} with breakout {scenario['breakout_probability']}% / "
        f"breakdown {scenario['breakdown_probability']}%.{phase_text}{motif_text}"
    )


def _build_validation_report(data: pd.DataFrame, config: FractalConfig) -> dict[str, Any]:
    """Evaluate how often the analog engine's dominant path bias matched actual historical outcomes."""

    anchors: list[dict[str, Any]] = []
    min_start = config.box_lookback + config.forecast_horizon + 10
    last_anchor = len(data) - config.forecast_horizon
    for anchor in range(min_start, last_anchor):
        history = data.iloc[:anchor].reset_index(drop=True)
        if len(history) < config.box_lookback + config.forecast_horizon + 10:
            continue
        current_window = history.tail(config.box_lookback).reset_index(drop=True)
        current_features = _extract_box_features(current_window, config)
        analogs = _find_analogs(history, current_features, config)
        if not analogs:
            continue
        predicted = _build_scenario_summary(analogs, current_features)["path_bias"]
        future = data.iloc[anchor : anchor + config.forecast_horizon].reset_index(drop=True)
        actual = _future_outcome(float(history.iloc[-1]["close"]), future, config)["label"]
        anchors.append(
            {
                "predicted": predicted,
                "actual": actual,
                "top_similarity": float(analogs[0]["similarity_score"]),
            }
        )

    anchors = anchors[-config.validation_samples :]
    if not anchors:
        return {
            "sample_count": 0,
            "directional_accuracy": 0.0,
            "avg_top_similarity": 0.0,
            "bullish_hits": 0,
            "bearish_hits": 0,
            "range_hits": 0,
        }

    def _matches(predicted: str, actual: str) -> bool:
        if predicted == "bullish":
            return actual == "bullish_breakout"
        if predicted == "bearish":
            return actual == "bearish_breakdown"
        return actual == "range_continuation"

    hits = sum(1 for item in anchors if _matches(item["predicted"], item["actual"]))
    bullish_hits = sum(1 for item in anchors if item["predicted"] == "bullish" and item["actual"] == "bullish_breakout")
    bearish_hits = sum(1 for item in anchors if item["predicted"] == "bearish" and item["actual"] == "bearish_breakdown")
    range_hits = sum(1 for item in anchors if item["predicted"] == "neutral" and item["actual"] == "range_continuation")

    return {
        "sample_count": len(anchors),
        "directional_accuracy": round((hits / len(anchors)) * 100, 2),
        "avg_top_similarity": round(sum(item["top_similarity"] for item in anchors) / len(anchors), 2),
        "bullish_hits": bullish_hits,
        "bearish_hits": bearish_hits,
        "range_hits": range_hits,
    }


def _empty_fractal(symbol: str, message: str) -> dict[str, Any]:
    return {
        "symbol": symbol.upper(),
        "status": "unavailable",
        "current_box": {},
        "scenario": {
            "path_bias": "neutral",
            "path_family": "standard",
            "phase_one_bias": "neutral",
            "phase_two_bias": "neutral",
            "active_trade_bias": "neutral",
            "active_phase": "standard",
            "phase_sequence": [],
            "breakdown_probability": 0.0,
            "breakout_probability": 0.0,
            "range_probability": 0.0,
            "dominant_theme": "Not enough history.",
        },
        "analogs": [],
        "short_term": {},
        "message": message,
    }

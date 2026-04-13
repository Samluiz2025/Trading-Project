from __future__ import annotations

import logging
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from trading_bot.core.data_fetcher import DataFetchError, FetchConfig, fetch_ohlc
from trading_bot.core.instrument_universe import get_instrument_universe
from trading_bot.core.market_structure import detect_market_structure, detect_swings
from trading_bot.core.strategy_strict_liquidity import build_strict_liquidity_config, generate_strict_liquidity_setup
from trading_bot.core.weekly_outlook_report import LOGS_DIR, render_weekly_outlook_markdown, save_weekly_outlook, save_weekly_outlook_example


LOGGER = logging.getLogger("weekly_outlook")


def configure_weekly_outlook_logging() -> None:
    """Set up file logging once for the weekly outlook job."""

    if LOGGER.handlers:
        return
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOGS_DIR / "weekly_outlook.log", encoding="utf-8")
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    handler.setFormatter(formatter)
    LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)


def run_weekly_outlook_engine(
    symbols: list[str] | None = None,
    source: str = "auto",
    timezone_name: str = "Europe/Vienna",
) -> tuple[dict, str, dict[str, str]]:
    """Run the full weekly outlook scan, save history, and return JSON + markdown."""

    configure_weekly_outlook_logging()
    tracked_symbols = symbols or get_instrument_universe("forex")
    timezone = ZoneInfo(timezone_name)
    now = datetime.now(timezone)
    review_start, review_end, next_week_start, next_week_end = _week_periods(now)
    LOGGER.info("Weekly outlook scan start for %s symbols", len(tracked_symbols))

    pair_reports: list[dict] = []
    for symbol in tracked_symbols:
        LOGGER.info("Scanning weekly outlook for %s", symbol)
        try:
            pair_reports.append(_build_pair_weekly_outlook(symbol=symbol, source=source, now=now))
        except DataFetchError as exc:
            LOGGER.warning("Weekly outlook missing data for %s: %s", symbol, exc)
            pair_reports.append(_failed_pair_report(symbol, str(exc)))
        except Exception as exc:  # keep the weekly job resilient per pair
            LOGGER.exception("Weekly outlook failed for %s", symbol)
            pair_reports.append(_failed_pair_report(symbol, str(exc)))

    rankings = _rank_pairs(pair_reports)
    summary = _build_summary(pair_reports, rankings)
    report = {
        "scan_time": now.isoformat(),
        "timezone": timezone_name,
        "week_review_period": {"start": review_start.isoformat(), "end": review_end.isoformat()},
        "next_week_period": {"start": next_week_start.isoformat(), "end": next_week_end.isoformat()},
        "pairs": pair_reports,
        "rankings": rankings,
        "summary": summary,
    }
    markdown_report = render_weekly_outlook_markdown(report)
    saved_paths = save_weekly_outlook(report, markdown_report)
    _save_pair_examples(report)
    LOGGER.info("Weekly outlook scan end")
    LOGGER.info("Weekly outlook output saved to %s and %s", saved_paths["json_path"], saved_paths["markdown_path"])
    return report, markdown_report, saved_paths


def _build_pair_weekly_outlook(symbol: str, source: str, now: datetime) -> dict:
    weekly_data = fetch_ohlc(FetchConfig(symbol=symbol, interval="1w", limit=120, source=source))
    daily_data = fetch_ohlc(FetchConfig(symbol=symbol, interval="1d", limit=260, source=source))
    h4_data = fetch_ohlc(FetchConfig(symbol=symbol, interval="4h", limit=320, source=source))
    h1_data = fetch_ohlc(FetchConfig(symbol=symbol, interval="1h", limit=480, source=source))
    ltf_data = fetch_ohlc(FetchConfig(symbol=symbol, interval="15m", limit=480, source=source))

    previous_week_review = _review_previous_week(symbol, weekly_data, daily_data, h4_data)
    outlook = _build_new_week_outlook(daily_data=daily_data, weekly_data=weekly_data, h4_data=h4_data, h1_data=h1_data)
    zones = _derive_zones(symbol=symbol, weekly_data=weekly_data, daily_data=daily_data, h4_data=h4_data, h1_data=h1_data, ltf_data=ltf_data, outlook=outlook)
    swing_plan = _build_swing_plan(symbol, weekly_data, daily_data, h4_data, h1_data, ltf_data, outlook, zones)
    intraday_plan = _build_intraday_plan(symbol, daily_data, h4_data, h1_data, ltf_data, outlook, zones)

    return {
        "symbol": symbol,
        "previous_week_review": previous_week_review,
        "outlook": outlook,
        "zones": zones,
        "swing_plan": swing_plan,
        "intraday_plan": intraday_plan,
        "scan_time": now.isoformat(),
    }


def _review_previous_week(symbol: str, weekly_data: pd.DataFrame, daily_data: pd.DataFrame, h4_data: pd.DataFrame) -> dict:
    """Review the last completed week and classify what actually happened."""

    previous_week = weekly_data.tail(2).iloc[-1] if len(weekly_data) >= 2 else weekly_data.iloc[-1]
    week_open = float(previous_week["open"])
    week_high = float(previous_week["high"])
    week_low = float(previous_week["low"])
    week_close = float(previous_week["close"])
    week_range = round(week_high - week_low, 4)
    dominant_direction = "bullish" if week_close > week_open else "bearish" if week_close < week_open else "neutral"
    avg_range = float((weekly_data["high"] - weekly_data["low"]).tail(12).mean()) if len(weekly_data) >= 4 else week_range
    body = abs(week_close - week_open)
    condition = _classify_week_condition(week_range, body, avg_range)
    structure_events = _detect_previous_week_events(daily_data=daily_data, h4_data=h4_data, dominant_direction=dominant_direction, week_high=week_high, week_low=week_low)
    worked, failed, reasons = _infer_weekly_setup_outcomes(condition, structure_events)
    lesson = _build_lesson(condition, dominant_direction, reasons)
    return {
        "ohlc": {"open": round(week_open, 4), "high": round(week_high, 4), "low": round(week_low, 4), "close": round(week_close, 4)},
        "range": week_range,
        "condition": condition,
        "dominant_direction": dominant_direction,
        "structure_events": structure_events,
        "worked_setups": worked,
        "failed_setups": failed,
        "failure_reasons": reasons,
        "lesson": lesson,
    }


def _build_new_week_outlook(*, daily_data: pd.DataFrame, weekly_data: pd.DataFrame, h4_data: pd.DataFrame, h1_data: pd.DataFrame) -> dict:
    """Build the directional and suitability outlook for the coming week."""

    monthly_data = _build_monthly_from_daily(daily_data)
    monthly_bias = detect_market_structure(monthly_data)["trend"] if len(monthly_data) >= 5 else detect_market_structure(weekly_data.tail(40).reset_index(drop=True))["trend"]
    weekly_bias = detect_market_structure(weekly_data.tail(40).reset_index(drop=True))["trend"]
    daily_bias = detect_market_structure(daily_data.tail(160).reset_index(drop=True))["trend"]
    h4_bias = detect_market_structure(h4_data.tail(160).reset_index(drop=True))["trend"]
    h1_bias = detect_market_structure(h1_data.tail(160).reset_index(drop=True))["trend"]
    alignment = _alignment_status(monthly_bias, weekly_bias, daily_bias, h4_bias, h1_bias)
    market_condition = _market_condition(daily_bias, h4_bias, h1_bias, daily_data, h4_data)
    swing_suitability = _swing_suitability(alignment, market_condition)
    intraday_suitability = _intraday_suitability(alignment, market_condition, h1_bias)
    return {
        "monthly_bias": str(monthly_bias).upper(),
        "weekly_bias": str(weekly_bias).upper(),
        "daily_bias": str(daily_bias).upper(),
        "h4_bias": str(h4_bias).upper(),
        "h1_bias": str(h1_bias).upper(),
        "alignment_status": alignment,
        "market_condition": market_condition,
        "swing_suitability": swing_suitability,
        "intraday_suitability": intraday_suitability,
    }


def _derive_zones(*, symbol: str, weekly_data: pd.DataFrame, daily_data: pd.DataFrame, h4_data: pd.DataFrame, h1_data: pd.DataFrame, ltf_data: pd.DataFrame, outlook: dict) -> dict:
    """Derive zones strictly from recent structure instead of freehand guesses."""

    previous_week = weekly_data.tail(2).iloc[-1] if len(weekly_data) >= 2 else weekly_data.iloc[-1]
    previous_week_high = round(float(previous_week["high"]), 4)
    previous_week_low = round(float(previous_week["low"]), 4)
    current_week_open = round(float(weekly_data.iloc[-1]["open"]), 4) if len(weekly_data) >= 1 else None
    side_bias = str(outlook.get("daily_bias", "")).lower()
    supply_zones = _supply_demand_from_swings(daily_data, zone_type="supply")
    demand_zones = _supply_demand_from_swings(daily_data, zone_type="demand")
    liquidity_pools = _liquidity_pools(daily_data, h4_data, h1_data, previous_week_high, previous_week_low)
    midpoint = round((previous_week_high + previous_week_low) / 2, 4)
    current_price = round(float(h1_data.iloc[-1]["close"]), 4)
    premium_discount = "premium" if current_price > midpoint else "discount"
    return {
        "previous_week_high": previous_week_high,
        "previous_week_low": previous_week_low,
        "current_week_open": current_week_open,
        "supply_zones": supply_zones,
        "demand_zones": demand_zones,
        "order_blocks": [],
        "fair_value_gaps": [],
        "liquidity_pools": liquidity_pools,
        "premium_discount_notes": f"Current price is trading in {premium_discount} relative to the previous-week midpoint {midpoint}.",
    }


def _build_swing_plan(symbol: str, weekly_data: pd.DataFrame, daily_data: pd.DataFrame, h4_data: pd.DataFrame, h1_data: pd.DataFrame, ltf_data: pd.DataFrame, outlook: dict, zones: dict) -> dict:
    """Build a swing-themed plan from the strict liquidity engine when the target is strong enough."""

    result = generate_strict_liquidity_setup(
        symbol=symbol,
        daily_data=daily_data,
        h1_data=h1_data,
        m15_data=ltf_data,
        config=build_strict_liquidity_config(symbol),
    )
    if result.get("status") != "VALID_TRADE":
        return {
            "status": "no clean swing setup",
            "bias": result.get("bias", "NEUTRAL"),
            "setup_type": "none",
            "entry_zone": [],
            "stop_loss_zone": [],
            "tp1": 0,
            "tp2": 0,
            "invalidation": 0,
            "confidence": 0,
            "explanation": f"Avoid this week. Missing: {', '.join(result.get('missing', [])) or 'clear trend context'}",
        }

    side = result["bias"]
    risk = abs(float(result["entry"]) - float(result["sl"]))
    tp1 = round(float(result["entry"]) + (risk * 2 if side == "BUY" else -risk * 2), 4)
    tp2 = float(result["tp"])
    return {
        "status": "actionable",
        "bias": side,
        "setup_type": "continuation",
        "entry_zone": [round(float(result["entry"]), 4), round(float(result["entry"]), 4)],
        "stop_loss_zone": [round(float(result["sl"]), 4), round(float(result["sl"]), 4)],
        "tp1": tp1,
        "tp2": round(tp2, 4),
        "invalidation": round(float(result["sl"]), 4),
        "confidence": int(result.get("confidence_score", 0)),
        "explanation": f"Daily bias and intraday liquidity confirmation support a {side.lower()} continuation into weekly liquidity.",
    }


def _build_intraday_plan(symbol: str, daily_data: pd.DataFrame, h4_data: pd.DataFrame, h1_data: pd.DataFrame, ltf_data: pd.DataFrame, outlook: dict, zones: dict) -> dict:
    """Build an intraday plan from the strict liquidity engine."""

    result = generate_strict_liquidity_setup(
        symbol=symbol,
        daily_data=daily_data,
        h1_data=h1_data,
        m15_data=ltf_data,
        config=build_strict_liquidity_config(symbol),
    )
    if result.get("status") != "VALID_TRADE":
        return {
            "status": "no clean intraday setup",
            "bias": result.get("bias", "NEUTRAL"),
            "preferred_session": "",
            "entry_zone": [],
            "stop_loss_zone": [],
            "tp1": 0,
            "tp2": 0,
            "invalidation": 0,
            "confidence": 0,
            "explanation": f"No clean intraday setup. Missing: {', '.join(result.get('missing', [])) or 'clear pullback confirmation'}",
        }

    side = result["bias"]
    risk = abs(float(result["entry"]) - float(result["sl"]))
    tp1 = round(float(result["entry"]) + (risk * 2 if side == 'BUY' else -risk * 2), 4)
    tp2 = float(result["tp"])
    preferred_session = _preferred_session(symbol, outlook.get("h1_bias", ""))
    return {
        "status": "actionable",
        "bias": side,
        "preferred_session": preferred_session,
        "entry_zone": [round(float(result["entry"]), 4), round(float(result["entry"]), 4)],
        "stop_loss_zone": [round(float(result["sl"]), 4), round(float(result["sl"]), 4)],
        "tp1": tp1,
        "tp2": round(tp2, 4),
        "invalidation": round(float(result["sl"]), 4),
        "confidence": int(result.get("confidence_score", 0)),
        "explanation": f"Daily/H4/H1 alignment supports a {side.lower()} intraday continuation with London/New York quality entry conditions.",
    }


def _rank_pairs(pair_reports: list[dict]) -> dict:
    swing_scored = []
    intraday_scored = []
    for pair in pair_reports:
        swing_scored.append((pair["symbol"], _plan_score(pair["swing_plan"], pair["outlook"], pair["zones"])))
        intraday_scored.append((pair["symbol"], _plan_score(pair["intraday_plan"], pair["outlook"], pair["zones"])))
    swing_scored.sort(key=lambda item: item[1], reverse=True)
    intraday_scored.sort(key=lambda item: item[1], reverse=True)
    return {
        "top_swing_pairs": [symbol for symbol, score in swing_scored if score > 0][:10],
        "top_intraday_pairs": [symbol for symbol, score in intraday_scored if score > 0][:10],
    }


def _build_summary(pair_reports: list[dict], rankings: dict) -> dict:
    swing_theme = Counter(pair["swing_plan"].get("bias", "NEUTRAL") for pair in pair_reports if pair["swing_plan"].get("status") == "actionable").most_common(1)
    intraday_theme = Counter(pair["intraday_plan"].get("bias", "NEUTRAL") for pair in pair_reports if pair["intraday_plan"].get("status") == "actionable").most_common(1)
    avoid = [
        pair["symbol"]
        for pair in pair_reports
        if pair["swing_plan"].get("status") != "actionable" and pair["intraday_plan"].get("status") != "actionable"
    ]
    return {
        "best_overall_swing_theme": swing_theme[0][0] if swing_theme else "No clean swing theme",
        "best_overall_intraday_theme": intraday_theme[0][0] if intraday_theme else "No clean intraday theme",
        "pairs_to_avoid": avoid,
        "market_notes": f"Focus on the top swing pairs {', '.join(rankings.get('top_swing_pairs', [])[:3]) or 'none'} and top intraday pairs {', '.join(rankings.get('top_intraday_pairs', [])[:3]) or 'none'} while avoiding messy consolidation.",
    }


def _classify_week_condition(week_range: float, body: float, avg_range: float) -> str:
    if avg_range > 0 and week_range > avg_range * 1.2:
        return "expanded"
    if body / max(week_range, 1e-9) >= 0.55:
        return "trended"
    if body / max(week_range, 1e-9) <= 0.2:
        return "ranged"
    return "distributed"


def _detect_previous_week_events(*, daily_data: pd.DataFrame, h4_data: pd.DataFrame, dominant_direction: str, week_high: float, week_low: float) -> list[str]:
    events: list[str] = []
    if len(daily_data) >= 10:
        daily_structure = detect_market_structure(daily_data.tail(40).reset_index(drop=True))
        if daily_structure["trend"] != "ranging":
            events.append("break of structure")
        if dominant_direction == "bullish" and float(daily_data.iloc[-1]["low"]) < week_low and float(daily_data.iloc[-1]["close"]) > week_low:
            events.append("liquidity sweep")
        if dominant_direction == "bearish" and float(daily_data.iloc[-1]["high"]) > week_high and float(daily_data.iloc[-1]["close"]) < week_high:
            events.append("liquidity sweep")
    if len(h4_data) >= 20:
        h4_structure = detect_market_structure(h4_data.tail(80).reset_index(drop=True))
        if h4_structure["trend"] == "ranging":
            events.append("change of character")
        events.append("impulse and pullback behavior")
    if abs(float(daily_data.iloc[-1]["close"]) - float(daily_data.iloc[-1]["open"])) / max(abs(week_high - week_low), 1e-9) > 0.3:
        events.append("rejection from key zone")
    return list(dict.fromkeys(events))


def _infer_weekly_setup_outcomes(condition: str, structure_events: list[str]) -> tuple[list[str], list[str], list[str]]:
    worked: list[str] = []
    failed: list[str] = []
    reasons: list[str] = []
    if condition in {"trended", "expanded"}:
        worked.extend(["continuation", "pullback continuation"])
    else:
        failed.append("continuation")
        reasons.append("ranging conditions")
    if "liquidity sweep" in structure_events and "change of character" in structure_events:
        worked.append("reversal")
    else:
        failed.append("reversal")
        reasons.append("no follow-through")
    if "rejection from key zone" not in structure_events:
        reasons.append("weak displacement")
    return list(dict.fromkeys(worked)), list(dict.fromkeys(failed)), list(dict.fromkeys(reasons))


def _build_lesson(condition: str, dominant_direction: str, reasons: list[str]) -> str:
    lines = [
        f"Last week was mostly {condition} with a {dominant_direction} directional tilt.",
        "Continuation ideas worked best when higher timeframe direction stayed clean." if condition in {"trended", "expanded"} else "Range conditions punished early continuation entries.",
        f"Main failure theme: {', '.join(reasons[:2]) or 'limited follow-through'}.",
    ]
    return " ".join(lines[:3])


def _build_monthly_from_daily(daily_data: pd.DataFrame) -> pd.DataFrame:
    frame = daily_data.copy()
    frame["time"] = pd.to_datetime(frame["time"], utc=True)
    monthly = (
        frame.set_index("time")
        .resample("1ME")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna()
        .reset_index()
    )
    return monthly.tail(24).reset_index(drop=True)


def _alignment_status(monthly_bias: str, weekly_bias: str, daily_bias: str, h4_bias: str, h1_bias: str) -> str:
    if len({monthly_bias, weekly_bias, daily_bias}) == 1 and monthly_bias in {"bullish", "bearish"}:
        if daily_bias == h4_bias == h1_bias:
            return "full_alignment"
        if daily_bias == h4_bias:
            return "htf_aligned_intraday_mixed"
    if daily_bias == h4_bias == h1_bias and daily_bias in {"bullish", "bearish"}:
        return "daily_intraday_alignment"
    return "mixed"


def _market_condition(daily_bias: str, h4_bias: str, h1_bias: str, daily_data: pd.DataFrame, h4_data: pd.DataFrame) -> str:
    daily_ranges = (daily_data["high"] - daily_data["low"]).astype(float)
    h4_ranges = (h4_data["high"] - h4_data["low"]).astype(float)
    expanding = float(h4_ranges.tail(10).mean()) > float(h4_ranges.tail(30).median())
    if daily_bias == h4_bias == h1_bias and daily_bias in {"bullish", "bearish"}:
        return "expanding" if expanding else "trending"
    if daily_bias == h4_bias and h1_bias == "ranging":
        return "corrective"
    if daily_bias == "ranging" or h4_bias == "ranging":
        return "ranging"
    return "transitional"


def _swing_suitability(alignment: str, market_condition: str) -> str:
    if alignment in {"full_alignment", "htf_aligned_intraday_mixed"} and market_condition in {"trending", "expanding", "corrective"}:
        return "swing trades"
    if market_condition == "transitional":
        return "both"
    return "neither"


def _intraday_suitability(alignment: str, market_condition: str, h1_bias: str) -> str:
    if alignment in {"full_alignment", "daily_intraday_alignment"} and market_condition in {"trending", "expanding"} and h1_bias in {"bullish", "bearish"}:
        return "day trades"
    if market_condition == "corrective":
        return "both"
    return "neither"


def _supply_demand_from_swings(data: pd.DataFrame, zone_type: str) -> list[list[float]]:
    swings = detect_swings(data.tail(120).reset_index(drop=True), swing_window=2)
    selected = [item for item in swings if item["type"] == ("high" if zone_type == "supply" else "low")][-3:]
    zones = []
    for item in selected:
        price = float(item["price"])
        zones.append([round(price * (0.999 if zone_type == "supply" else 1.001), 4), round(price, 4)])
    return zones


def _serialize_order_block(timeframe: str, order_block: dict) -> dict | None:
    if not order_block or not order_block.get("confirmed"):
        return None
    return {"timeframe": timeframe, **order_block.get("zone", {})}


def _liquidity_pools(daily_data: pd.DataFrame, h4_data: pd.DataFrame, h1_data: pd.DataFrame, previous_week_high: float, previous_week_low: float) -> list[dict]:
    pools: list[dict] = [
        {"type": "previous_week_high", "level": previous_week_high, "timeframe": "Weekly"},
        {"type": "previous_week_low", "level": previous_week_low, "timeframe": "Weekly"},
    ]
    for timeframe, frame in (("Daily", daily_data), ("H4", h4_data), ("H1", h1_data)):
        swings = detect_swings(frame.tail(120).reset_index(drop=True), swing_window=2)
        highs = [float(item["price"]) for item in swings if item["type"] == "high"]
        lows = [float(item["price"]) for item in swings if item["type"] == "low"]
        equal_high = _find_equal_levels(highs)
        equal_low = _find_equal_levels(lows)
        if equal_high is not None:
            pools.append({"type": "equal_highs", "level": equal_high, "timeframe": timeframe})
        if equal_low is not None:
            pools.append({"type": "equal_lows", "level": equal_low, "timeframe": timeframe})
    return pools


def _find_equal_levels(levels: list[float], tolerance_ratio: float = 0.001) -> float | None:
    if len(levels) < 2:
        return None
    for left, right in zip(levels[:-1], levels[1:]):
        if abs(left - right) / max(abs(left), 1e-9) <= tolerance_ratio:
            return round((left + right) / 2, 4)
    return None


def _preferred_session(symbol: str, h1_bias: str) -> str:
    if symbol.endswith("JPY"):
        return "London"
    if symbol.startswith("USD") and h1_bias in {"BULLISH", "BEARISH"}:
        return "New York"
    return "London"


def _plan_score(plan: dict, outlook: dict, zones: dict) -> float:
    if plan.get("status") != "actionable":
        return 0.0
    score = float(plan.get("confidence") or 0)
    if outlook.get("alignment_status") == "full_alignment":
        score += 15
    if outlook.get("market_condition") in {"trending", "expanding"}:
        score += 10
    if zones.get("fair_value_gaps"):
        score += 5
    if zones.get("order_blocks"):
        score += 5
    return score


def _week_periods(now: datetime) -> tuple[datetime, datetime, datetime, datetime]:
    weekday = now.weekday()
    current_week_start = (now - timedelta(days=weekday)).replace(hour=0, minute=0, second=0, microsecond=0)
    previous_week_start = current_week_start - timedelta(days=7)
    previous_week_end = current_week_start - timedelta(seconds=1)
    next_week_start = current_week_start + timedelta(days=7)
    next_week_end = next_week_start + timedelta(days=7) - timedelta(seconds=1)
    return previous_week_start, previous_week_end, next_week_start, next_week_end


def _failed_pair_report(symbol: str, error_message: str) -> dict:
    return {
        "symbol": symbol,
        "previous_week_review": {
            "ohlc": {"open": 0, "high": 0, "low": 0, "close": 0},
            "range": 0,
            "condition": "unknown",
            "dominant_direction": "unknown",
            "structure_events": [],
            "worked_setups": [],
            "failed_setups": [],
            "failure_reasons": ["missing data"],
            "lesson": f"Weekly review failed: {error_message}",
        },
        "outlook": {
            "monthly_bias": "UNKNOWN",
            "weekly_bias": "UNKNOWN",
            "daily_bias": "UNKNOWN",
            "h4_bias": "UNKNOWN",
            "h1_bias": "UNKNOWN",
            "alignment_status": "data_error",
            "market_condition": "unknown",
            "swing_suitability": "neither",
            "intraday_suitability": "neither",
        },
        "zones": {
            "previous_week_high": 0,
            "previous_week_low": 0,
            "supply_zones": [],
            "demand_zones": [],
            "order_blocks": [],
            "fair_value_gaps": [],
            "liquidity_pools": [],
            "premium_discount_notes": "No data available.",
        },
        "swing_plan": {
            "status": "no setup",
            "bias": "UNKNOWN",
            "setup_type": "",
            "entry_zone": [],
            "stop_loss_zone": [],
            "tp1": 0,
            "tp2": 0,
            "invalidation": 0,
            "confidence": 0,
            "explanation": error_message,
        },
        "intraday_plan": {
            "status": "no setup",
            "bias": "UNKNOWN",
            "preferred_session": "",
            "entry_zone": [],
            "stop_loss_zone": [],
            "tp1": 0,
            "tp2": 0,
            "invalidation": 0,
            "confidence": 0,
            "explanation": error_message,
        },
    }


def _save_pair_examples(report: dict) -> None:
    """Persist two pair-level examples so the output format is easy to inspect quickly."""

    example_pairs = [pair for pair in report.get("pairs", [])[:2]]
    for pair in example_pairs:
        pair_report = {
            "scan_time": report.get("scan_time"),
            "timezone": report.get("timezone"),
            "week_review_period": report.get("week_review_period"),
            "next_week_period": report.get("next_week_period"),
            "pairs": [pair],
            "rankings": report.get("rankings", {}),
            "summary": report.get("summary", {}),
        }
        markdown_report = render_weekly_outlook_markdown(pair_report)
        save_weekly_outlook_example(f"{pair.get('symbol', 'pair')}_weekly_outlook", pair_report, markdown_report)

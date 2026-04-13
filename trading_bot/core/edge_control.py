from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from trading_bot.core.journal import ACTIVE_STRATEGY, build_open_trade_snapshot, load_journal_entries
from trading_bot.core.strategy_registry import normalize_strategy_scope, strategy_matches_scope
from trading_bot.core.validation_mode import build_validation_snapshot


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
EDGE_CONTROL_PATH = DATA_DIR / "edge_control.json"
DEFAULT_TIMEZONE = "Europe/Vienna"
GRADE_RANK = {"D": 0, "C": 1, "B": 2, "A": 3, "A+": 4}


def load_edge_control_settings() -> dict[str, Any]:
    if not EDGE_CONTROL_PATH.exists():
        default_settings = _default_edge_control_settings()
        save_edge_control_settings(default_settings)
        return default_settings

    try:
        raw = EDGE_CONTROL_PATH.read_text(encoding="utf-8-sig")
    except OSError:
        return _default_edge_control_settings()

    cleaned = raw.replace("\x00", "").strip()
    if not cleaned:
        default_settings = _default_edge_control_settings()
        save_edge_control_settings(default_settings)
        return default_settings

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        backup_path = EDGE_CONTROL_PATH.with_suffix(".corrupt.json")
        try:
            backup_path.write_text(raw, encoding="utf-8", errors="ignore")
        except OSError:
            pass
        default_settings = _default_edge_control_settings()
        save_edge_control_settings(default_settings)
        return default_settings

    if not isinstance(payload, dict):
        default_settings = _default_edge_control_settings()
        save_edge_control_settings(default_settings)
        return default_settings
    return _with_defaults(payload)


def save_edge_control_settings(settings: dict[str, Any]) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    normalized = _with_defaults(settings)
    try:
        EDGE_CONTROL_PATH.write_text(json.dumps(normalized, separators=(",", ":")), encoding="utf-8")
    except OSError as exc:
        print(f"[WARN] Failed to save edge control settings: {exc}")
    return EDGE_CONTROL_PATH


def build_edge_control_snapshot(
    *,
    entries: list[dict[str, Any]] | None = None,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = _with_defaults(settings or load_edge_control_settings())
    entries = list(entries) if entries is not None else load_journal_entries()
    timezone = _resolve_timezone(settings.get("timezone"))
    strategy = normalize_strategy_scope(settings.get("active_strategy") or ACTIVE_STRATEGY)

    closed_entries = _closed_strategy_entries(entries, strategy=strategy)
    open_snapshot = build_open_trade_snapshot(entries=entries)
    open_entries = [
        entry
        for entry in open_snapshot.get("active_open_entries", [])
        if strategy_matches_scope(entry.get("strategy"), strategy)
    ]
    stale_open_entries = [
        entry
        for entry in open_snapshot.get("stale_open_entries", [])
        if strategy_matches_scope(entry.get("strategy"), strategy)
    ]
    daily_entries = _entries_for_local_day(closed_entries, timezone=timezone)
    weekly_entries = _entries_for_local_week(closed_entries, timezone=timezone)
    consecutive_losses = _count_consecutive_losses(closed_entries)
    validation_snapshot = build_validation_snapshot(entries=entries)

    symbol_breakdown = validation_snapshot.get("symbol_breakdown", [])
    session_breakdown = validation_snapshot.get("session_breakdown", [])
    allowed_symbols, symbol_mode = _derive_allowed_symbols(
        settings=settings,
        symbol_breakdown=symbol_breakdown,
    )
    symbol_filter_mode = _normalize_symbol_filter_mode(settings.get("symbol_filter_mode"))
    if symbol_filter_mode != "strict":
        allowed_symbols = []
        symbol_mode = "open"
    manual_blocked_symbols = _normalize_symbols(settings.get("manual_symbol_blacklist"))
    calibration_blocked_symbols = _normalize_symbols(settings.get("calibrated_symbol_blacklist"))
    validation_blocked_symbols = _normalize_symbols(validation_snapshot.get("underperforming_symbols"))
    blocked_symbols = list(dict.fromkeys([*manual_blocked_symbols, *calibration_blocked_symbols, *validation_blocked_symbols]))

    max_daily_loss = float(settings.get("max_daily_net_r_loss") or 0.0)
    max_weekly_loss = float(settings.get("max_weekly_net_r_loss") or 0.0)
    max_consecutive_losses = int(settings.get("max_consecutive_losses") or 0)
    daily_net_rr = round(sum(float(item.get("rr_achieved") or 0.0) for item in daily_entries), 2)
    weekly_net_rr = round(sum(float(item.get("rr_achieved") or 0.0) for item in weekly_entries), 2)
    daily_locked = max_daily_loss > 0 and daily_net_rr <= -max_daily_loss
    weekly_locked = max_weekly_loss > 0 and weekly_net_rr <= -max_weekly_loss
    consecutive_locked = max_consecutive_losses > 0 and consecutive_losses >= max_consecutive_losses

    lock_reasons: list[str] = []
    if daily_locked:
        lock_reasons.append("Daily loss lock active")
    if weekly_locked:
        lock_reasons.append("Weekly loss lock active")
    if consecutive_locked:
        lock_reasons.append("Consecutive loss lock active")

    session_whitelist = _normalize_sessions(settings.get("allowed_sessions"))
    shadow_sessions = _normalize_sessions(settings.get("shadow_sessions"))
    session_filter_mode = _normalize_session_filter_mode(settings.get("session_filter_mode"))
    minimum_setup_grade = _normalize_grade(settings.get("minimum_setup_grade"))
    local_now = datetime.now(timezone)
    current_week = local_now.isocalendar()

    return {
        "enabled": bool(settings.get("enabled", True)),
        "timezone": timezone.key,
        "active_strategy": strategy,
        "symbol_filter_mode": symbol_filter_mode,
        "session_filter_mode": session_filter_mode,
        "allowed_sessions": session_whitelist,
        "shadow_sessions": shadow_sessions,
        "minimum_setup_grade": minimum_setup_grade,
        "symbol_mode": symbol_mode,
        "allowed_symbols": allowed_symbols,
        "blocked_symbols": blocked_symbols,
        "calibration_blocked_symbols": calibration_blocked_symbols,
        "validation_blocked_symbols": validation_blocked_symbols,
        "locked": bool(lock_reasons),
        "lock_reasons": lock_reasons,
        "consecutive_losses": consecutive_losses,
        "max_consecutive_losses": max_consecutive_losses,
        "daily": {
            "date": local_now.date().isoformat(),
            "closed_trades": len(daily_entries),
            "net_rr": daily_net_rr,
            "max_loss_r": max_daily_loss,
            "locked": daily_locked,
        },
        "weekly": {
            "iso_year": current_week.year,
            "iso_week": current_week.week,
            "closed_trades": len(weekly_entries),
            "net_rr": weekly_net_rr,
            "max_loss_r": max_weekly_loss,
            "locked": weekly_locked,
        },
        "open_positions": {
            "total": len(open_entries),
            "triggered": sum(1 for item in open_entries if bool(item.get("entry_triggered"))),
            "pending": sum(1 for item in open_entries if not bool(item.get("entry_triggered"))),
            "stale": len(stale_open_entries),
        },
        "validation_summary": {
            "validated_closed_trades": validation_snapshot.get("validated_closed_trades", 0),
            "adjusted_net_r": validation_snapshot.get("adjusted_net_r", 0.0),
            "adjusted_expectancy_r": validation_snapshot.get("adjusted_expectancy_r", 0.0),
            "adjusted_profit_factor": validation_snapshot.get("adjusted_profit_factor", 0.0),
            "adjusted_win_rate": validation_snapshot.get("adjusted_win_rate", 0.0),
        },
        "symbol_breakdown": symbol_breakdown,
        "session_breakdown": session_breakdown,
        "settings": settings,
    }


def evaluate_edge_control(
    candidate: dict[str, Any],
    *,
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot = snapshot or build_edge_control_snapshot()
    symbol = str(candidate.get("pair") or candidate.get("symbol") or "").upper()
    session = str(candidate.get("session") or candidate.get("analysis_context", {}).get("session") or "").strip().lower()
    setup_grade = _normalize_grade(candidate.get("setup_grade"))
    reasons: list[str] = []

    if not snapshot.get("enabled", True):
        return {
            "allowed": True,
            "reasons": [],
            "symbol": symbol,
            "session": session,
            "setup_grade": setup_grade,
        }

    if symbol in set(snapshot.get("validation_blocked_symbols") or []):
        reasons.append("Symbol auto-blocked by validation")
    elif symbol in set(snapshot.get("calibration_blocked_symbols") or []):
        reasons.append("Symbol blocked by calibration")
    elif symbol in set(snapshot.get("blocked_symbols") or []):
        reasons.append("Symbol manually blocked")

    symbol_mode = str(snapshot.get("symbol_mode") or "open")
    symbol_filter_mode = _normalize_symbol_filter_mode(snapshot.get("symbol_filter_mode"))
    allowed_symbols = set(snapshot.get("allowed_symbols") or [])
    if symbol_filter_mode == "strict" and symbol_mode != "open" and symbol and symbol not in allowed_symbols:
        reasons.append("Symbol not in edge whitelist")

    session_filter_mode = _normalize_session_filter_mode(snapshot.get("session_filter_mode"))
    allowed_sessions = set(snapshot.get("allowed_sessions") or [])
    if session_filter_mode == "strict" and allowed_sessions and session not in allowed_sessions:
        reasons.append("Outside allowed session")

    minimum_setup_grade = _normalize_grade(snapshot.get("minimum_setup_grade"))
    if _grade_score(setup_grade) < _grade_score(minimum_setup_grade):
        reasons.append("Below minimum setup grade")

    reasons.extend(snapshot.get("lock_reasons") or [])
    unique_reasons = list(dict.fromkeys(reasons))
    shadow_sessions = set(snapshot.get("shadow_sessions") or [])
    shadow_eligible = bool(
        session_filter_mode == "strict"
        and symbol
        and session in shadow_sessions
        and unique_reasons == ["Outside allowed session"]
    )
    return {
        "allowed": not unique_reasons,
        "reasons": unique_reasons,
        "symbol": symbol,
        "session": session,
        "setup_grade": setup_grade,
        "symbol_filter_mode": symbol_filter_mode,
        "session_filter_mode": session_filter_mode,
        "locked": bool(snapshot.get("locked")),
        "shadow_eligible": shadow_eligible,
        "trade_mode": "shadow" if shadow_eligible else ("live" if not unique_reasons else "blocked"),
    }


def _default_edge_control_settings() -> dict[str, Any]:
    return {
        "enabled": True,
        "timezone": DEFAULT_TIMEZONE,
        "active_strategy": normalize_strategy_scope(ACTIVE_STRATEGY),
        "symbol_filter_mode": "score_only",
        "session_filter_mode": "score_only",
        "allowed_sessions": [],
        "shadow_sessions": [],
        "minimum_setup_grade": "A+",
        "max_daily_net_r_loss": 2.0,
        "max_weekly_net_r_loss": 5.0,
        "max_consecutive_losses": 2,
        "manual_symbol_whitelist": [],
        "manual_symbol_blacklist": [],
        "calibrated_symbol_whitelist": [],
        "calibrated_symbol_blacklist": [],
        "auto_symbol_whitelist": {
            "enabled": True,
            "minimum_closed_trades": 2,
            "minimum_adjusted_win_rate": 35.0,
            "minimum_adjusted_net_r": 0.0,
            "minimum_adjusted_expectancy_r": 0.0,
        },
    }


def _with_defaults(settings: dict[str, Any]) -> dict[str, Any]:
    default_settings = _default_edge_control_settings()
    merged = {**default_settings, **dict(settings or {})}
    auto_whitelist = {
        **default_settings["auto_symbol_whitelist"],
        **dict((settings or {}).get("auto_symbol_whitelist") or {}),
    }
    merged["auto_symbol_whitelist"] = auto_whitelist
    merged["symbol_filter_mode"] = _normalize_symbol_filter_mode(merged.get("symbol_filter_mode"))
    merged["session_filter_mode"] = _normalize_session_filter_mode(merged.get("session_filter_mode"))
    merged["allowed_sessions"] = _normalize_sessions(merged.get("allowed_sessions"))
    merged["shadow_sessions"] = _normalize_sessions(merged.get("shadow_sessions"))
    merged["manual_symbol_whitelist"] = _normalize_symbols(merged.get("manual_symbol_whitelist"))
    merged["manual_symbol_blacklist"] = _normalize_symbols(merged.get("manual_symbol_blacklist"))
    merged["calibrated_symbol_whitelist"] = _normalize_symbols(merged.get("calibrated_symbol_whitelist"))
    merged["calibrated_symbol_blacklist"] = _normalize_symbols(merged.get("calibrated_symbol_blacklist"))
    merged["minimum_setup_grade"] = _normalize_grade(merged.get("minimum_setup_grade"))
    merged["active_strategy"] = normalize_strategy_scope(merged.get("active_strategy") or ACTIVE_STRATEGY)
    merged["timezone"] = _resolve_timezone(merged.get("timezone")).key
    return merged


def _resolve_timezone(value: Any) -> ZoneInfo:
    try:
        return ZoneInfo(str(value or DEFAULT_TIMEZONE))
    except Exception:
        return ZoneInfo("UTC")


def _normalize_symbols(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(str(value).upper() for value in values if str(value).strip()))


def _normalize_sessions(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(str(value).strip().lower() for value in values if str(value).strip()))


def _normalize_session_filter_mode(value: Any) -> str:
    normalized = str(value or "score_only").strip().lower()
    return normalized if normalized in {"strict", "score_only"} else "score_only"


def _normalize_symbol_filter_mode(value: Any) -> str:
    normalized = str(value or "score_only").strip().lower()
    return normalized if normalized in {"strict", "score_only"} else "score_only"


def _normalize_grade(value: Any) -> str:
    normalized = str(value or "A+").strip().upper()
    return normalized if normalized in GRADE_RANK else "A+"


def _grade_score(value: Any) -> int:
    return GRADE_RANK.get(_normalize_grade(value), 0)


def _closed_strategy_entries(entries: list[dict[str, Any]], *, strategy: str) -> list[dict[str, Any]]:
    closed = [
        entry
        for entry in entries
        if strategy_matches_scope(entry.get("strategy"), strategy)
        and not bool(entry.get("shadow_mode"))
        and str(entry.get("result") or "").upper() in {"WIN", "LOSS"}
    ]
    return sorted(closed, key=_entry_sort_key)


def _open_strategy_entries(entries: list[dict[str, Any]], *, strategy: str) -> list[dict[str, Any]]:
    return [
        entry
        for entry in entries
        if strategy_matches_scope(entry.get("strategy"), strategy)
        and not bool(entry.get("shadow_mode"))
        and str(entry.get("status") or "").upper() == "OPEN"
    ]


def _entries_for_local_day(entries: list[dict[str, Any]], *, timezone: ZoneInfo) -> list[dict[str, Any]]:
    today = datetime.now(timezone).date()
    return [entry for entry in entries if _entry_local_datetime(entry, timezone=timezone).date() == today]


def _entries_for_local_week(entries: list[dict[str, Any]], *, timezone: ZoneInfo) -> list[dict[str, Any]]:
    current_week = datetime.now(timezone).isocalendar()
    filtered: list[dict[str, Any]] = []
    for entry in entries:
        local_dt = _entry_local_datetime(entry, timezone=timezone)
        iso = local_dt.isocalendar()
        if iso.year == current_week.year and iso.week == current_week.week:
            filtered.append(entry)
    return filtered


def _entry_local_datetime(entry: dict[str, Any], *, timezone: ZoneInfo) -> datetime:
    timestamp = entry.get("closed_at") or entry.get("timestamp") or datetime.now(UTC).isoformat()
    try:
        parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except ValueError:
        parsed = datetime.now(UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(timezone)


def _entry_sort_key(entry: dict[str, Any]) -> datetime:
    timestamp = entry.get("closed_at") or entry.get("timestamp") or datetime.now(UTC).isoformat()
    try:
        parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except ValueError:
        parsed = datetime.now(UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _count_consecutive_losses(entries: list[dict[str, Any]]) -> int:
    count = 0
    for entry in sorted(entries, key=_entry_sort_key, reverse=True):
        if str(entry.get("result") or "").upper() == "LOSS":
            count += 1
            continue
        break
    return count


def _build_symbol_breakdown(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        grouped[str(entry.get("symbol") or "UNKNOWN").upper()].append(entry)
    rows = [_summarize_group(symbol, symbol_entries) for symbol, symbol_entries in grouped.items()]
    rows.sort(key=lambda item: (float(item["net_rr"]), float(item["expectancy_r"]), item["symbol"]), reverse=True)
    return rows


def _build_session_breakdown(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        session = str(entry.get("session") or "unknown").strip().lower()
        grouped[session].append(entry)
    rows = []
    for session, session_entries in grouped.items():
        summary = _summarize_group(session, session_entries)
        rows.append(
            {
                "session": session,
                "closed_trades": summary["closed_trades"],
                "wins": summary["wins"],
                "losses": summary["losses"],
                "win_rate": summary["win_rate"],
                "net_rr": summary["net_rr"],
                "expectancy_r": summary["expectancy_r"],
                "profit_factor": summary["profit_factor"],
            }
        )
    rows.sort(key=lambda item: (float(item["net_rr"]), float(item["expectancy_r"]), item["session"]), reverse=True)
    return rows


def _summarize_group(name: str, entries: list[dict[str, Any]]) -> dict[str, Any]:
    closed_trades = len(entries)
    wins = [entry for entry in entries if str(entry.get("result") or "").upper() == "WIN"]
    losses = [entry for entry in entries if str(entry.get("result") or "").upper() == "LOSS"]
    gross_profit = sum(max(float(entry.get("rr_achieved") or 0.0), 0.0) for entry in entries)
    gross_loss = abs(sum(min(float(entry.get("rr_achieved") or 0.0), 0.0) for entry in entries))
    net_rr = round(sum(float(entry.get("rr_achieved") or 0.0) for entry in entries), 2)
    expectancy_r = round(net_rr / closed_trades, 2) if closed_trades else 0.0
    win_rate = round((len(wins) / closed_trades) * 100, 2) if closed_trades else 0.0
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss else round(gross_profit, 2)
    return {
        "symbol": name,
        "closed_trades": closed_trades,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "net_rr": net_rr,
        "expectancy_r": expectancy_r,
        "profit_factor": profit_factor,
    }


def _derive_allowed_symbols(
    *,
    settings: dict[str, Any],
    symbol_breakdown: list[dict[str, Any]],
) -> tuple[list[str], str]:
    manual_whitelist = _normalize_symbols(settings.get("manual_symbol_whitelist"))
    if manual_whitelist:
        return manual_whitelist, "manual_whitelist"

    calibrated_whitelist = _normalize_symbols(settings.get("calibrated_symbol_whitelist"))
    if calibrated_whitelist:
        return calibrated_whitelist, "calibrated_whitelist"

    auto_settings = dict(settings.get("auto_symbol_whitelist") or {})
    if not auto_settings.get("enabled", True):
        return [], "open"

    minimum_closed_trades = int(auto_settings.get("minimum_closed_trades") or 0)
    minimum_adjusted_win_rate = float(auto_settings.get("minimum_adjusted_win_rate") or 0.0)
    minimum_adjusted_net_r = float(auto_settings.get("minimum_adjusted_net_r") or 0.0)
    minimum_adjusted_expectancy_r = float(auto_settings.get("minimum_adjusted_expectancy_r") or 0.0)

    qualified = [
        str(item.get("symbol") or "").upper()
        for item in symbol_breakdown
        if int(item.get("closed_trades") or 0) >= minimum_closed_trades
        and float(item.get("adjusted_win_rate") or 0.0) >= minimum_adjusted_win_rate
        and float(item.get("adjusted_net_r") or 0.0) >= minimum_adjusted_net_r
        and float(item.get("adjusted_expectancy_r") or 0.0) >= minimum_adjusted_expectancy_r
    ]
    if qualified:
        return qualified, "auto_whitelist"
    return [], "open"

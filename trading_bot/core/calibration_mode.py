from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from trading_bot.core.edge_control import load_edge_control_settings, save_edge_control_settings
from trading_bot.core.journal import ACTIVE_STRATEGY, load_journal_entries
from trading_bot.core.strategy_registry import normalize_strategy_scope
from trading_bot.core.validation_mode import build_validation_snapshot, load_validation_settings, save_validation_settings


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
CALIBRATION_MODE_PATH = DATA_DIR / "calibration_mode.json"
CALIBRATION_HISTORY_PATH = DATA_DIR / "calibration_history.json"
DEFAULT_TIMEZONE = "Europe/Vienna"


def load_calibration_settings() -> dict[str, Any]:
    if not CALIBRATION_MODE_PATH.exists():
        default_settings = _default_calibration_settings()
        save_calibration_settings(default_settings)
        return default_settings

    try:
        raw = CALIBRATION_MODE_PATH.read_text(encoding="utf-8-sig")
    except OSError:
        return _default_calibration_settings()

    cleaned = raw.replace("\x00", "").strip()
    if not cleaned:
        default_settings = _default_calibration_settings()
        save_calibration_settings(default_settings)
        return default_settings

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        backup_path = CALIBRATION_MODE_PATH.with_suffix(".corrupt.json")
        try:
            backup_path.write_text(raw, encoding="utf-8", errors="ignore")
        except OSError:
            pass
        default_settings = _default_calibration_settings()
        save_calibration_settings(default_settings)
        return default_settings

    if not isinstance(payload, dict):
        default_settings = _default_calibration_settings()
        save_calibration_settings(default_settings)
        return default_settings
    return _with_defaults(payload)


def save_calibration_settings(settings: dict[str, Any]) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    normalized = _with_defaults(settings)
    try:
        CALIBRATION_MODE_PATH.write_text(json.dumps(normalized, separators=(",", ":")), encoding="utf-8")
    except OSError as exc:
        print(f"[WARN] Failed to save calibration settings: {exc}")
    return CALIBRATION_MODE_PATH


def load_calibration_history() -> list[dict[str, Any]]:
    if not CALIBRATION_HISTORY_PATH.exists():
        return []
    try:
        raw = CALIBRATION_HISTORY_PATH.read_text(encoding="utf-8-sig")
    except OSError:
        return []

    cleaned = raw.replace("\x00", "").strip()
    if not cleaned:
        return []
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        return []
    return payload if isinstance(payload, list) else []


def get_recent_calibration_history(limit: int = 10) -> list[dict[str, Any]]:
    history = load_calibration_history()
    return list(reversed(history[-max(1, limit):]))


def save_calibration_history(history: list[dict[str, Any]]) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        CALIBRATION_HISTORY_PATH.write_text(json.dumps(history, separators=(",", ":")), encoding="utf-8")
    except OSError as exc:
        print(f"[WARN] Failed to save calibration history: {exc}")
    return CALIBRATION_HISTORY_PATH


def build_calibration_snapshot(
    *,
    entries: list[dict[str, Any]] | None = None,
    calibration_settings: dict[str, Any] | None = None,
    edge_settings: dict[str, Any] | None = None,
    validation_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    calibration_settings = _with_defaults(calibration_settings or load_calibration_settings())
    edge_settings = edge_settings or load_edge_control_settings()
    validation_settings = validation_settings or load_validation_settings()
    entries = list(entries) if entries is not None else load_journal_entries()
    validation_snapshot = build_validation_snapshot(entries=entries, settings=validation_settings)
    shadow_sessions = [str(item).strip().lower() for item in edge_settings.get("shadow_sessions") or [] if str(item).strip()]
    session_filter_mode = str(edge_settings.get("session_filter_mode") or "score_only").strip().lower()
    shadow_validation_snapshot = build_validation_snapshot(
        entries=entries,
        settings=validation_settings,
        shadow_mode="only",
        sessions=shadow_sessions,
    )
    history = load_calibration_history()
    timezone = _resolve_timezone(calibration_settings.get("timezone"))

    symbol_review_min_trades = int(calibration_settings.get("symbol_review_min_trades") or 0)
    session_review_min_trades = int(calibration_settings.get("session_review_min_trades") or 0)
    recent_trade_window = int(calibration_settings.get("recent_trade_window") or 0)

    symbol_rows = list(validation_snapshot.get("symbol_breakdown") or [])
    session_rows = list(validation_snapshot.get("session_breakdown") or [])
    shadow_session_rows = list(shadow_validation_snapshot.get("session_breakdown") or [])
    recent_trades = list(validation_snapshot.get("recent_trades") or [])
    recent_window = recent_trades[-recent_trade_window:] if recent_trade_window > 0 else recent_trades
    recent_adjusted_net_r = round(sum(float(item.get("adjusted_rr") or 0.0) for item in recent_window), 2)
    recent_adjusted_expectancy_r = round(recent_adjusted_net_r / len(recent_window), 2) if recent_window else 0.0

    promoted_symbols = [
        str(item.get("symbol") or "").upper()
        for item in symbol_rows
        if int(item.get("closed_trades") or 0) >= symbol_review_min_trades
        and float(item.get("adjusted_expectancy_r") or 0.0) >= float(calibration_settings.get("promote_min_expectancy_r") or 0.0)
        and float(item.get("adjusted_net_r") or 0.0) >= float(calibration_settings.get("promote_min_net_r") or 0.0)
    ]
    demoted_symbols = [
        str(item.get("symbol") or "").upper()
        for item in symbol_rows
        if int(item.get("closed_trades") or 0) >= symbol_review_min_trades
        and (
            float(item.get("adjusted_expectancy_r") or 0.0) <= float(calibration_settings.get("demote_max_expectancy_r") or 0.0)
            or float(item.get("adjusted_net_r") or 0.0) <= float(calibration_settings.get("demote_max_net_r") or 0.0)
        )
    ]
    promoted_symbols = [symbol for symbol in promoted_symbols if symbol not in demoted_symbols]

    current_sessions = [str(item).strip().lower() for item in edge_settings.get("allowed_sessions") or [] if str(item).strip()]
    if session_filter_mode == "strict":
        recommended_sessions = [
            str(item.get("session") or "").strip().lower()
            for item in session_rows
            if int(item.get("closed_trades") or 0) >= session_review_min_trades
            and float(item.get("adjusted_expectancy_r") or 0.0) > float(calibration_settings.get("session_keep_min_expectancy_r") or 0.0)
        ]
        if not recommended_sessions:
            recommended_sessions = current_sessions or ["new_york"]
    else:
        recommended_sessions = current_sessions

    shadow_session_reviews = _build_shadow_session_reviews(
        shadow_session_rows=shadow_session_rows,
        shadow_sessions=shadow_sessions,
        calibration_settings=calibration_settings,
    )
    shadow_sessions_ready = [item["session"] for item in shadow_session_reviews if item.get("ready_for_live")]

    current_grade = str(edge_settings.get("minimum_setup_grade") or calibration_settings.get("default_grade") or "A+").upper()
    recommended_grade = current_grade
    if len(recent_window) >= int(calibration_settings.get("grade_review_min_trades") or 0):
        if (
            recent_adjusted_net_r >= float(calibration_settings.get("grade_loosen_adjusted_net_r") or 0.0)
            and recent_adjusted_expectancy_r >= float(calibration_settings.get("grade_loosen_expectancy_r") or 0.0)
        ):
            recommended_grade = str(calibration_settings.get("relaxed_grade") or current_grade).upper()
        elif (
            recent_adjusted_net_r <= float(calibration_settings.get("grade_tighten_adjusted_net_r") or 0.0)
            or recent_adjusted_expectancy_r <= float(calibration_settings.get("grade_tighten_expectancy_r") or 0.0)
        ):
            recommended_grade = str(calibration_settings.get("strict_grade") or current_grade).upper()

    recommended_min_symbol_trades = _recommended_validation_min_trades(validation_snapshot)

    next_edge_settings = {**edge_settings}
    next_edge_settings["allowed_sessions"] = recommended_sessions
    next_edge_settings["minimum_setup_grade"] = recommended_grade
    next_edge_settings["calibrated_symbol_whitelist"] = promoted_symbols
    next_edge_settings["calibrated_symbol_blacklist"] = demoted_symbols
    if bool(calibration_settings.get("auto_promote_shadow_sessions", False)) and shadow_sessions_ready:
        next_edge_settings["allowed_sessions"] = list(dict.fromkeys([*recommended_sessions, *shadow_sessions_ready]))
        next_edge_settings["shadow_sessions"] = [session for session in shadow_sessions if session not in shadow_sessions_ready]

    next_validation_settings = {**validation_settings}
    next_validation_settings["minimum_symbol_trades"] = recommended_min_symbol_trades
    next_validation_settings["auto_disable_underperformers"] = True
    next_validation_settings["maximum_negative_expectancy_r"] = float(calibration_settings.get("demote_max_expectancy_r") or 0.0)
    next_validation_settings["maximum_negative_net_r"] = float(calibration_settings.get("demote_max_net_r") or 0.0)
    next_validation_settings["require_triggered_entry"] = True

    changes = _detect_changes(
        edge_settings=edge_settings,
        next_edge_settings=next_edge_settings,
        validation_settings=validation_settings,
        next_validation_settings=next_validation_settings,
    )
    reasons = _build_reason_log(
        promoted_symbols=promoted_symbols,
        demoted_symbols=demoted_symbols,
        recommended_sessions=recommended_sessions,
        recommended_grade=recommended_grade,
        recent_adjusted_net_r=recent_adjusted_net_r,
        recent_adjusted_expectancy_r=recent_adjusted_expectancy_r,
        validation_snapshot=validation_snapshot,
        shadow_session_reviews=shadow_session_reviews,
        calibration_settings=calibration_settings,
        session_filter_mode=session_filter_mode,
    )

    return {
        "enabled": bool(calibration_settings.get("enabled", True)),
        "auto_apply_in_monitor": bool(calibration_settings.get("auto_apply_in_monitor", True)),
        "timezone": timezone.key,
        "active_strategy": normalize_strategy_scope(calibration_settings.get("active_strategy") or ACTIVE_STRATEGY),
        "recommended_edge_settings": next_edge_settings,
        "recommended_validation_settings": next_validation_settings,
        "promoted_symbols": promoted_symbols,
        "demoted_symbols": demoted_symbols,
        "recommended_sessions": recommended_sessions,
        "recommended_minimum_setup_grade": recommended_grade,
        "recommended_validation_min_symbol_trades": recommended_min_symbol_trades,
        "recent_trade_window": len(recent_window),
        "recent_adjusted_net_r": recent_adjusted_net_r,
        "recent_adjusted_expectancy_r": recent_adjusted_expectancy_r,
        "validated_closed_trades": validation_snapshot.get("validated_closed_trades", 0),
        "shadow_sessions": shadow_sessions,
        "shadow_validated_closed_trades": shadow_validation_snapshot.get("validated_closed_trades", 0),
        "shadow_session_reviews": shadow_session_reviews,
        "shadow_sessions_ready": shadow_sessions_ready,
        "pending_changes": changes,
        "reason_log": reasons,
        "can_apply": bool(changes),
        "last_application": history[-1] if history else None,
        "history_count": len(history),
        "settings": calibration_settings,
    }


def apply_calibration(
    *,
    entries: list[dict[str, Any]] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    calibration_settings = load_calibration_settings()
    snapshot = build_calibration_snapshot(entries=entries, calibration_settings=calibration_settings)
    if not snapshot.get("enabled", True) and not force:
        return {
            "status": "DISABLED",
            "message": "Calibration mode is disabled.",
            "snapshot": snapshot,
        }

    if not snapshot.get("pending_changes") and not force:
        return {
            "status": "NO_CHANGES",
            "message": "Calibration found no setting changes to apply.",
            "snapshot": snapshot,
        }

    save_edge_control_settings(snapshot["recommended_edge_settings"])
    save_validation_settings(snapshot["recommended_validation_settings"])

    applied_at = datetime.now(UTC).isoformat()
    history = load_calibration_history()
    history.append(
        {
            "applied_at": applied_at,
            "changes": snapshot.get("pending_changes", []),
            "reason_log": snapshot.get("reason_log", []),
            "promoted_symbols": snapshot.get("promoted_symbols", []),
            "demoted_symbols": snapshot.get("demoted_symbols", []),
            "recommended_sessions": snapshot.get("recommended_sessions", []),
            "recommended_minimum_setup_grade": snapshot.get("recommended_minimum_setup_grade"),
            "recommended_validation_min_symbol_trades": snapshot.get("recommended_validation_min_symbol_trades"),
            "recent_adjusted_net_r": snapshot.get("recent_adjusted_net_r", 0.0),
            "recent_adjusted_expectancy_r": snapshot.get("recent_adjusted_expectancy_r", 0.0),
        }
    )
    history = history[-int(calibration_settings.get("history_limit") or 20):]
    save_calibration_history(history)

    refreshed_snapshot = build_calibration_snapshot(entries=entries, calibration_settings=calibration_settings)
    return {
        "status": "APPLIED",
        "message": "Calibration settings updated from validated trading performance.",
        "applied_at": applied_at,
        "changes": snapshot.get("pending_changes", []),
        "snapshot": refreshed_snapshot,
    }


def maybe_apply_calibration(*, entries: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    calibration_settings = load_calibration_settings()
    if not bool(calibration_settings.get("enabled", True)):
        return {"status": "DISABLED"}
    if not bool(calibration_settings.get("auto_apply_in_monitor", True)):
        return {"status": "AUTO_APPLY_DISABLED"}

    history = load_calibration_history()
    if history:
        last_applied_at = history[-1].get("applied_at")
        try:
            last_dt = datetime.fromisoformat(str(last_applied_at).replace("Z", "+00:00"))
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=UTC)
            cooldown = timedelta(minutes=int(calibration_settings.get("auto_apply_cooldown_minutes") or 0))
            if datetime.now(UTC) - last_dt.astimezone(UTC) < cooldown:
                return {"status": "COOLDOWN"}
        except ValueError:
            pass

    return apply_calibration(entries=entries)


def _default_calibration_settings() -> dict[str, Any]:
    return {
        "enabled": True,
        "auto_apply_in_monitor": True,
        "auto_apply_cooldown_minutes": 30,
        "history_limit": 20,
        "timezone": DEFAULT_TIMEZONE,
        "active_strategy": normalize_strategy_scope(ACTIVE_STRATEGY),
        "symbol_review_min_trades": 3,
        "session_review_min_trades": 2,
        "recent_trade_window": 8,
        "grade_review_min_trades": 8,
        "promote_min_expectancy_r": 0.15,
        "promote_min_net_r": 0.5,
        "demote_max_expectancy_r": 0.0,
        "demote_max_net_r": -0.5,
        "session_keep_min_expectancy_r": 0.0,
        "shadow_session_review_min_trades": 5,
        "shadow_promote_min_expectancy_r": 0.15,
        "shadow_promote_min_net_r": 0.5,
        "auto_promote_shadow_sessions": False,
        "default_grade": "A+",
        "strict_grade": "A+",
        "relaxed_grade": "B",
        "grade_loosen_adjusted_net_r": 4.0,
        "grade_loosen_expectancy_r": 0.5,
        "grade_tighten_adjusted_net_r": 0.0,
        "grade_tighten_expectancy_r": 0.0,
    }


def _with_defaults(settings: dict[str, Any]) -> dict[str, Any]:
    defaults = _default_calibration_settings()
    merged = {**defaults, **dict(settings or {})}
    merged["timezone"] = _resolve_timezone(merged.get("timezone")).key
    merged["active_strategy"] = normalize_strategy_scope(merged.get("active_strategy") or ACTIVE_STRATEGY)
    return merged


def _resolve_timezone(value: Any) -> ZoneInfo:
    try:
        return ZoneInfo(str(value or DEFAULT_TIMEZONE))
    except Exception:
        return ZoneInfo("UTC")


def _recommended_validation_min_trades(validation_snapshot: dict[str, Any]) -> int:
    validated_closed = int(validation_snapshot.get("validated_closed_trades") or 0)
    if validated_closed >= 40:
        return 5
    if validated_closed >= 20:
        return 4
    return 3


def _detect_changes(
    *,
    edge_settings: dict[str, Any],
    next_edge_settings: dict[str, Any],
    validation_settings: dict[str, Any],
    next_validation_settings: dict[str, Any],
) -> list[str]:
    changes: list[str] = []

    if list(edge_settings.get("allowed_sessions") or []) != list(next_edge_settings.get("allowed_sessions") or []):
        changes.append("Updated allowed sessions from calibration")
    if list(edge_settings.get("shadow_sessions") or []) != list(next_edge_settings.get("shadow_sessions") or []):
        changes.append("Updated shadow sessions from calibration")
    if str(edge_settings.get("minimum_setup_grade") or "") != str(next_edge_settings.get("minimum_setup_grade") or ""):
        changes.append("Adjusted minimum setup grade")
    if list(edge_settings.get("calibrated_symbol_whitelist") or []) != list(next_edge_settings.get("calibrated_symbol_whitelist") or []):
        changes.append("Refreshed calibrated symbol whitelist")
    if list(edge_settings.get("calibrated_symbol_blacklist") or []) != list(next_edge_settings.get("calibrated_symbol_blacklist") or []):
        changes.append("Refreshed calibrated symbol blacklist")
    if int(validation_settings.get("minimum_symbol_trades") or 0) != int(next_validation_settings.get("minimum_symbol_trades") or 0):
        changes.append("Adjusted validation minimum symbol trade count")
    if float(validation_settings.get("maximum_negative_expectancy_r") or 0.0) != float(next_validation_settings.get("maximum_negative_expectancy_r") or 0.0):
        changes.append("Updated validation expectancy cutoff")
    if float(validation_settings.get("maximum_negative_net_r") or 0.0) != float(next_validation_settings.get("maximum_negative_net_r") or 0.0):
        changes.append("Updated validation net R cutoff")
    return changes


def _build_reason_log(
    *,
    promoted_symbols: list[str],
    demoted_symbols: list[str],
    recommended_sessions: list[str],
    recommended_grade: str,
    recent_adjusted_net_r: float,
    recent_adjusted_expectancy_r: float,
    validation_snapshot: dict[str, Any],
    shadow_session_reviews: list[dict[str, Any]],
    calibration_settings: dict[str, Any],
    session_filter_mode: str,
) -> list[str]:
    reasons = [
        f"Recent adjusted net R over the last window is {recent_adjusted_net_r:.2f}.",
        f"Recent adjusted expectancy is {recent_adjusted_expectancy_r:.2f}R.",
        f"Validated closed trades available: {int(validation_snapshot.get('validated_closed_trades') or 0)}.",
        f"Recommended minimum setup grade is {recommended_grade}.",
        f"Session filter mode is {session_filter_mode}.",
        f"Recommended sessions: {', '.join(recommended_sessions) if recommended_sessions else 'none'}.",
    ]
    if promoted_symbols:
        reasons.append(f"Promoting symbols with positive adjusted expectancy: {', '.join(promoted_symbols)}.")
    if demoted_symbols:
        reasons.append(f"Demoting symbols with weak adjusted results: {', '.join(demoted_symbols)}.")
    ready_shadow_sessions = [item["session"] for item in shadow_session_reviews if item.get("ready_for_live")]
    if ready_shadow_sessions:
        reasons.append(f"Shadow sessions ready for live promotion: {', '.join(ready_shadow_sessions)}.")
    elif shadow_session_reviews:
        waiting_shadow = ", ".join(
            f"{item['session']} ({int(item.get('closed_trades') or 0)} trades)"
            for item in shadow_session_reviews
        )
        reasons.append(f"Shadow sessions still collecting evidence: {waiting_shadow}.")
    reasons.append(
        "Symbol review requires at least "
        f"{int(calibration_settings.get('symbol_review_min_trades') or 0)} validated closed trades."
    )
    return reasons


def _build_shadow_session_reviews(
    *,
    shadow_session_rows: list[dict[str, Any]],
    shadow_sessions: list[str],
    calibration_settings: dict[str, Any],
) -> list[dict[str, Any]]:
    if not shadow_sessions:
        return []

    reviews: list[dict[str, Any]] = []
    min_trades = int(calibration_settings.get("shadow_session_review_min_trades") or 0)
    min_expectancy = float(calibration_settings.get("shadow_promote_min_expectancy_r") or 0.0)
    min_net_r = float(calibration_settings.get("shadow_promote_min_net_r") or 0.0)
    row_by_session = {
        str(item.get("session") or "").strip().lower(): item
        for item in shadow_session_rows
    }
    for session in shadow_sessions:
        row = dict(row_by_session.get(session) or {})
        closed_trades = int(row.get("closed_trades") or 0)
        adjusted_expectancy_r = float(row.get("adjusted_expectancy_r") or 0.0)
        adjusted_net_r = float(row.get("adjusted_net_r") or 0.0)
        ready_for_live = (
            closed_trades >= min_trades
            and adjusted_expectancy_r >= min_expectancy
            and adjusted_net_r >= min_net_r
        )
        reasons: list[str] = []
        if closed_trades < min_trades:
            reasons.append(f"Needs {min_trades - closed_trades} more closed shadow trades")
        if adjusted_expectancy_r < min_expectancy:
            reasons.append(f"Expectancy below {min_expectancy:.2f}R")
        if adjusted_net_r < min_net_r:
            reasons.append(f"Net R below {min_net_r:.2f}")
        reviews.append(
            {
                "session": session,
                "closed_trades": closed_trades,
                "adjusted_net_r": round(adjusted_net_r, 2),
                "adjusted_expectancy_r": round(adjusted_expectancy_r, 2),
                "adjusted_win_rate": round(float(row.get("adjusted_win_rate") or 0.0), 2),
                "adjusted_profit_factor": round(float(row.get("adjusted_profit_factor") or 0.0), 2),
                "ready_for_live": ready_for_live,
                "requirements": {
                    "minimum_closed_trades": min_trades,
                    "minimum_adjusted_expectancy_r": min_expectancy,
                    "minimum_adjusted_net_r": min_net_r,
                },
                "reasons": reasons,
            }
        )
    return reviews

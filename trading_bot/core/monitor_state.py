from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
STATE_PATH = DATA_DIR / "monitor_state.json"
MAX_ERROR_LENGTH = 240


def _default_monitor_state() -> dict[str, Any]:
    return {
        "last_successful_scan": None,
        "last_cycle_started_at": None,
        "telegram": {"last_ok": None, "last_error": None},
        "data_source_health": {},
        "alert_contexts": {},
        "digests_sent": {},
        "scan_diagnostics": {},
        "scanner": {
            "running": False,
            "group": None,
            "source": None,
            "poll_interval_seconds": None,
            "current_symbol": None,
            "completed_symbols": 0,
            "total_symbols": 0,
            "last_progress_at": None,
            "last_completed_symbol": None,
        },
    }


def load_monitor_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return _default_monitor_state()

    try:
        raw = STATE_PATH.read_text(encoding="utf-8-sig")
    except OSError:
        return _default_monitor_state()

    cleaned = raw.replace("\x00", "").strip()
    if not cleaned:
        default_state = _default_monitor_state()
        save_monitor_state(default_state)
        return default_state

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        backup_path = STATE_PATH.with_suffix(".corrupt.json")
        try:
            backup_path.write_text(raw, encoding="utf-8", errors="ignore")
        except OSError:
            pass
        default_state = _default_monitor_state()
        save_monitor_state(default_state)
        return default_state

    if not isinstance(payload, dict):
        default_state = _default_monitor_state()
        save_monitor_state(default_state)
        return default_state

    default_state = _default_monitor_state()
    default_state.update(payload)
    default_state["telegram"] = {
        **_default_monitor_state()["telegram"],
        **(payload.get("telegram") or {}),
    }
    default_state["scanner"] = {
        **_default_monitor_state()["scanner"],
        **(payload.get("scanner") or {}),
    }
    return default_state


def save_monitor_state(state: dict[str, Any]) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    compacted = _compact_monitor_state(state)
    payload = json.dumps(compacted, separators=(",", ":"))
    try:
        STATE_PATH.write_text(payload, encoding="utf-8")
    except OSError as exc:
        print(f"[WARN] Failed to save monitor state: {exc}")
    return STATE_PATH


def mark_cycle_started(*, group: str, source: str, poll_interval_seconds: int) -> dict[str, Any]:
    state = load_monitor_state()
    timestamp = datetime.now(UTC).isoformat()
    state["last_cycle_started_at"] = timestamp
    state["scanner"] = {
        "running": True,
        "group": group,
        "source": source,
        "poll_interval_seconds": poll_interval_seconds,
        "current_symbol": None,
        "completed_symbols": 0,
        "total_symbols": 0,
        "last_progress_at": timestamp,
        "last_completed_symbol": None,
    }
    save_monitor_state(state)
    return state


def mark_cycle_completed() -> dict[str, Any]:
    state = load_monitor_state()
    timestamp = datetime.now(UTC).isoformat()
    state["last_successful_scan"] = timestamp
    scanner = state.setdefault("scanner", {})
    scanner["running"] = True
    scanner["current_symbol"] = None
    scanner["last_progress_at"] = timestamp
    save_monitor_state(state)
    return state


def record_cycle_progress(*, symbol: str, completed_symbols: int, total_symbols: int, phase: str = "completed") -> dict[str, Any]:
    state = load_monitor_state()
    scanner = state.setdefault("scanner", {})
    timestamp = datetime.now(UTC).isoformat()
    normalized_symbol = str(symbol or "").upper() or None
    scanner["running"] = True
    scanner["completed_symbols"] = int(max(completed_symbols, 0))
    scanner["total_symbols"] = int(max(total_symbols, 0))
    scanner["last_progress_at"] = timestamp
    if phase == "started":
        scanner["current_symbol"] = normalized_symbol
    else:
        scanner["current_symbol"] = None
        scanner["last_completed_symbol"] = normalized_symbol
    save_monitor_state(state)
    return state


def update_symbol_health(symbol: str, *, ok: bool, source: str, error: str | None = None) -> dict[str, Any]:
    state = load_monitor_state()
    health = state.setdefault("data_source_health", {})
    health[str(symbol).upper()] = {
        "ok": ok,
        "source": source,
        "last_checked": datetime.now(UTC).isoformat(),
        "last_error": _trim_error_message(error),
    }
    save_monitor_state(state)
    return state


def record_telegram_delivery(ok: bool, error: str | None = None) -> dict[str, Any]:
    state = load_monitor_state()
    telegram = state.setdefault("telegram", {})
    timestamp = datetime.now(UTC).isoformat()
    if ok:
        telegram["last_ok"] = timestamp
    else:
        telegram["last_error"] = {"time": timestamp, "error": error}
    save_monitor_state(state)
    return state


def load_alert_contexts() -> dict[str, dict[str, Any]]:
    return load_monitor_state().get("alert_contexts", {})


def save_alert_contexts(contexts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    state = load_monitor_state()
    state["alert_contexts"] = contexts
    save_monitor_state(state)
    return state


def should_send_digest(name: str, digest_date: str) -> bool:
    state = load_monitor_state()
    digests = state.setdefault("digests_sent", {})
    return digests.get(name) != digest_date


def mark_digest_sent(name: str, digest_date: str) -> dict[str, Any]:
    state = load_monitor_state()
    digests = state.setdefault("digests_sent", {})
    digests[name] = digest_date
    save_monitor_state(state)
    return state


def reset_runtime_monitor_state(*, keep_telegram_status: bool = True) -> dict[str, Any]:
    current = load_monitor_state()
    state = _default_monitor_state()
    if keep_telegram_status:
        state["telegram"] = {
            **state["telegram"],
            **(current.get("telegram") or {}),
        }
    save_monitor_state(state)
    return state


def record_scan_diagnostics(
    *,
    evaluated_symbols: int,
    valid_candidates: int,
    selected_candidates: list[dict[str, Any]],
    blocked_candidates: list[dict[str, Any]],
    rejected_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    state = load_monitor_state()
    state["scan_diagnostics"] = {
        "last_updated_at": datetime.now(UTC).isoformat(),
        "evaluated_symbols": int(evaluated_symbols),
        "valid_candidates": int(valid_candidates),
        "selected_count": len(selected_candidates),
        "blocked_count": len(blocked_candidates),
        "rejected_count": len(rejected_candidates),
        "selected_candidates": [_compact_candidate(item) for item in selected_candidates[:8]],
        "blocked_candidates": [_compact_candidate(item) for item in blocked_candidates[:12]],
        "rejected_candidates": [_compact_candidate(item) for item in rejected_candidates[:12]],
    }
    save_monitor_state(state)
    return state


def build_scanner_health_snapshot(state: dict[str, Any] | None = None) -> dict[str, Any]:
    active_state = state or load_monitor_state()
    scanner = dict(active_state.get("scanner") or {})
    poll_interval_seconds = int(scanner.get("poll_interval_seconds") or 0)
    total_symbols = int(scanner.get("total_symbols") or 0)
    last_cycle_started_at = _parse_timestamp(active_state.get("last_cycle_started_at"))
    last_successful_scan = _parse_timestamp(active_state.get("last_successful_scan"))
    last_progress_at = _parse_timestamp(scanner.get("last_progress_at"))
    now = datetime.now(UTC)

    cycle_threshold_seconds = max(300, poll_interval_seconds * 20, total_symbols * 20)
    last_progress_age_seconds = _age_seconds(now, last_progress_at)
    last_success_age_seconds = _age_seconds(now, last_successful_scan)
    has_incomplete_cycle = bool(
        last_cycle_started_at
        and (last_successful_scan is None or last_cycle_started_at > last_successful_scan)
    )

    status = "healthy"
    reasons: list[str] = []
    if not bool(scanner.get("running")):
        status = "offline"
        reasons.append("Scanner is not marked as running.")
    elif has_incomplete_cycle and last_progress_age_seconds is not None and last_progress_age_seconds > cycle_threshold_seconds:
        status = "stalled"
        reasons.append("Scanner started a cycle but stopped making progress before completion.")
    elif last_success_age_seconds is not None and last_success_age_seconds > cycle_threshold_seconds:
        status = "stale"
        reasons.append("No successful scan completion recorded within the expected cycle window.")
    elif has_incomplete_cycle:
        status = "in_progress"
        reasons.append("Scanner is still working through the current cycle.")

    return {
        "status": status,
        "reasons": reasons,
        "current_symbol": scanner.get("current_symbol"),
        "last_completed_symbol": scanner.get("last_completed_symbol"),
        "completed_symbols": int(scanner.get("completed_symbols") or 0),
        "total_symbols": total_symbols,
        "last_progress_at": last_progress_at.isoformat() if last_progress_at else None,
        "last_cycle_started_at": last_cycle_started_at.isoformat() if last_cycle_started_at else None,
        "last_successful_scan": last_successful_scan.isoformat() if last_successful_scan else None,
        "last_progress_age_seconds": last_progress_age_seconds,
        "last_success_age_seconds": last_success_age_seconds,
        "cycle_threshold_seconds": cycle_threshold_seconds,
        "has_incomplete_cycle": has_incomplete_cycle,
    }


def _trim_error_message(error: str | None) -> str | None:
    if error is None:
        return None
    return str(error)[:MAX_ERROR_LENGTH]


def _compact_monitor_state(state: dict[str, Any]) -> dict[str, Any]:
    compacted = dict(state)
    health = dict(compacted.get("data_source_health") or {})
    compacted["data_source_health"] = {
        symbol: {
            **dict(details or {}),
            "last_error": _trim_error_message((details or {}).get("last_error")),
        }
        for symbol, details in health.items()
    }
    contexts = dict(compacted.get("alert_contexts") or {})
    if len(contexts) > 100:
        keys = list(contexts.keys())[-100:]
        compacted["alert_contexts"] = {key: contexts[key] for key in keys}
    diagnostics = dict(compacted.get("scan_diagnostics") or {})
    compacted["scan_diagnostics"] = {
        "last_updated_at": diagnostics.get("last_updated_at"),
        "evaluated_symbols": int(diagnostics.get("evaluated_symbols") or 0),
        "valid_candidates": int(diagnostics.get("valid_candidates") or 0),
        "selected_count": int(diagnostics.get("selected_count") or 0),
        "blocked_count": int(diagnostics.get("blocked_count") or 0),
        "rejected_count": int(diagnostics.get("rejected_count") or 0),
        "selected_candidates": [_compact_candidate(item) for item in diagnostics.get("selected_candidates", [])[:8]],
        "blocked_candidates": [_compact_candidate(item) for item in diagnostics.get("blocked_candidates", [])[:12]],
        "rejected_candidates": [_compact_candidate(item) for item in diagnostics.get("rejected_candidates", [])[:12]],
    }
    compacted["scanner"] = {
        **_default_monitor_state()["scanner"],
        **dict(compacted.get("scanner") or {}),
    }
    return compacted


def _compact_candidate(item: dict[str, Any]) -> dict[str, Any]:
    payload = dict(item or {})
    return {
        "symbol": str(payload.get("symbol") or payload.get("pair") or "").upper() or None,
        "status": payload.get("status"),
        "session": str(payload.get("session") or "").strip().lower() or None,
        "setup_grade": payload.get("setup_grade"),
        "confidence_score": _safe_int(payload.get("confidence_score")),
        "ranking_score": _safe_float(payload.get("ranking_score")),
        "message": str(payload.get("message") or "")[:MAX_ERROR_LENGTH] or None,
        "missing": list(payload.get("missing") or [])[:4],
        "reasons": list(payload.get("reasons") or [])[:4],
        "block_type": payload.get("block_type"),
        "lifecycle": payload.get("lifecycle"),
    }


def _safe_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    try:
        return round(float(value), 2) if value is not None else None
    except (TypeError, ValueError):
        return None


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _age_seconds(now: datetime, earlier: datetime | None) -> int | None:
    if earlier is None:
        return None
    return max(int((now - earlier).total_seconds()), 0)

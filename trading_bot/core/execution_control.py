from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from trading_bot.core.journal import build_open_trade_snapshot, get_recent_journal


def build_execution_control_snapshot(entries: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    journal = list(entries) if entries is not None else get_recent_journal(limit=500)
    live_entries = [entry for entry in journal if not bool(entry.get("shadow_mode"))]
    open_snapshot = build_open_trade_snapshot(entries=live_entries)
    active_open_entries = open_snapshot.get("active_open_entries", [])

    enabled = _env_bool("LIVE_EXECUTION_ENABLED", default=False)
    kill_switch = _env_bool("EXECUTION_KILL_SWITCH", default=False)
    strategy_freeze = _env_bool("STRATEGY_FREEZE_MODE", default=True)
    max_open_positions = _env_int("EXECUTION_MAX_OPEN_POSITIONS", default=1)
    max_daily_loss_r = _env_float("EXECUTION_MAX_DAILY_LOSS_R", default=2.0)
    allowed_symbols = _env_symbol_list("EXECUTION_ALLOWED_SYMBOLS")

    today_prefix = datetime.now(UTC).date().isoformat()
    closed_today = [
        entry
        for entry in live_entries
        if str(entry.get("closed_at") or "").startswith(today_prefix)
        and str(entry.get("result") or "").upper() in {"WIN", "LOSS"}
    ]
    daily_closed_r = round(sum(float(entry.get("rr_achieved") or 0.0) for entry in closed_today), 2)
    max_loss_hit = daily_closed_r <= -abs(max_daily_loss_r)
    open_limit_hit = len(active_open_entries) >= max_open_positions > 0

    reasons: list[str] = []
    if not enabled:
        reasons.append("Live execution is disabled.")
    if kill_switch:
        reasons.append("Execution kill switch is active.")
    if max_loss_hit:
        reasons.append("Daily loss limit has been reached.")
    if open_limit_hit:
        reasons.append("Maximum open positions reached.")

    return {
        "enabled": enabled,
        "draft_only": not enabled,
        "kill_switch": kill_switch,
        "strategy_freeze": strategy_freeze,
        "layers": {
            "watcher": "dashboard",
            "executor": "telegram",
            "broker": "draft_only" if not enabled else "live",
        },
        "allowed_symbols": allowed_symbols,
        "max_open_positions": max_open_positions,
        "current_open_positions": len(active_open_entries),
        "max_daily_loss_r": max_daily_loss_r,
        "daily_closed_r": daily_closed_r,
        "daily_loss_limit_hit": max_loss_hit,
        "hard_block_reasons": reasons,
    }


def evaluate_execution_controls(setup: dict[str, Any], snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    active_snapshot = dict(snapshot or build_execution_control_snapshot())
    reasons = list(active_snapshot.get("hard_block_reasons") or [])
    symbol = str(setup.get("pair") or setup.get("symbol") or "").upper()
    allowed_symbols = list(active_snapshot.get("allowed_symbols") or [])
    if allowed_symbols and symbol and symbol not in allowed_symbols:
        reasons.append("Symbol is outside the execution allowlist.")

    checklist = list(((setup.get("broker_draft") or {}).get("execution_checklist") or {}).get("items") or [])
    return {
        "allowed": not reasons,
        "reasons": reasons,
        "symbol": symbol,
        "checklist_items": checklist,
        "snapshot": active_snapshot,
    }


def _env_bool(name: str, *, default: bool) -> bool:
    raw = str(os.getenv(name, "")).strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_int(name: str, *, default: int) -> int:
    try:
        return int(str(os.getenv(name, "")).strip() or default)
    except ValueError:
        return default


def _env_float(name: str, *, default: float) -> float:
    try:
        return float(str(os.getenv(name, "")).strip() or default)
    except ValueError:
        return default


def _env_symbol_list(name: str) -> list[str]:
    raw = str(os.getenv(name, "")).strip()
    if not raw:
        return []
    return [item.strip().upper() for item in raw.split(",") if item.strip()]

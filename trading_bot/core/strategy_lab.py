from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from trading_bot.core.journal import load_journal_entries
from trading_bot.core.strategy_registry import LIVE_STRATEGIES, strategy_matches_scope


def build_strategy_lab_snapshot() -> dict[str, Any]:
    entries = [
        entry
        for entry in load_journal_entries()
        if not bool(entry.get("shadow_mode"))
    ]
    forward_test_week_entries = _filter_forward_test_week_entries(entries)

    strategies: list[dict[str, Any]] = []
    for strategy_name in LIVE_STRATEGIES:
        strategy_entries = [
            entry
            for entry in entries
            if strategy_matches_scope(entry.get("strategy"), strategy_name)
        ]
        forward_entries = [
            entry
            for entry in forward_test_week_entries
            if strategy_matches_scope(entry.get("strategy"), strategy_name)
        ]

        all_time = _build_window_metrics(strategy_entries)
        forward_test_week = _build_window_metrics(forward_entries)
        promotion_status, promotion_reasons = _promotion_status(forward_test_week)

        strategies.append(
            {
                "name": strategy_name,
                "all_time": all_time,
                "forward_test_week": forward_test_week,
                "blocked_all_time": max(int(all_time["total_setups"]) - int(all_time["valid_setups"]), 0),
                "blocked_forward_test_week": max(int(forward_test_week["total_setups"]) - int(forward_test_week["valid_setups"]), 0),
                "most_common_failure_reason": all_time.get("most_common_failure_reason"),
                "promotion_status": promotion_status,
                "promotion_reasons": promotion_reasons,
                "promotion_basis": "forward_test_week",
                # Compatibility fields for existing consumers.
                "total_setups": all_time["total_setups"],
                "valid_setups": all_time["valid_setups"],
                "activated_entries": all_time["activated_entries"],
                "wins": all_time["wins"],
                "losses": all_time["losses"],
                "win_rate": all_time["win_rate"],
                "total_r": all_time["total_r"],
                "average_r": all_time["average_r"],
                "draft_to_trigger_conversion": all_time["draft_to_trigger_conversion"],
                "trigger_to_win_conversion": all_time["trigger_to_win_conversion"],
                "promotion_valid_setups": forward_test_week["valid_setups"],
                "promotion_closed_trades": forward_test_week["closed_trades"],
                "promotion_win_rate": forward_test_week["win_rate"],
                "promotion_total_r": forward_test_week["total_r"],
                "promotion_average_r": forward_test_week["average_r"],
            }
        )

    return {
        "summary": {
            "strategies_tracked": len(strategies),
            "promotion_basis": "forward_test_week",
            "forward_test_week_entries": len(forward_test_week_entries),
            "all_time": _build_summary_metrics(entries),
            "forward_test_week": _build_summary_metrics(forward_test_week_entries),
            "highlights": _build_highlights(strategies, forward_test_week_entries),
        },
        "strategies": strategies,
        "promotion_table": [
            {
                "strategy": item["name"],
                "promotion_status": item["promotion_status"],
                "promotion_reasons": item["promotion_reasons"],
                "all_time": item["all_time"],
                "forward_test_week": item["forward_test_week"],
            }
            for item in strategies
        ],
    }


def _build_highlights(strategies: list[dict[str, Any]], forward_test_week_entries: list[dict[str, Any]]) -> dict[str, Any]:
    best_all_time = _pick_best_strategy(strategies, window_key="all_time")
    best_forward_week = _pick_best_strategy(strategies, window_key="forward_test_week")
    most_blocked = max(
        strategies,
        key=lambda item: (int(item.get("blocked_all_time") or 0), -float(item.get("all_time", {}).get("total_r") or 0.0)),
        default=None,
    )

    return {
        "best_all_time": _serialize_highlight_strategy(best_all_time, window_key="all_time"),
        "best_forward_test_week": _serialize_highlight_strategy(best_forward_week, window_key="forward_test_week"),
        "most_blocked": {
            "name": most_blocked.get("name"),
            "blocked_count": int(most_blocked.get("blocked_all_time") or 0),
            "failure_reason": str(most_blocked.get("most_common_failure_reason") or "No dominant failure yet"),
        } if most_blocked else None,
        "top_failure_reason_week": _top_failure_reason_for_entries(forward_test_week_entries),
    }


def _pick_best_strategy(strategies: list[dict[str, Any]], *, window_key: str) -> dict[str, Any] | None:
    candidates = [item for item in strategies if int((item.get(window_key) or {}).get("valid_setups") or 0) > 0]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (
            float((item.get(window_key) or {}).get("total_r") or 0.0),
            float((item.get(window_key) or {}).get("win_rate") or 0.0),
            int((item.get(window_key) or {}).get("valid_setups") or 0),
        ),
    )


def _serialize_highlight_strategy(item: dict[str, Any] | None, *, window_key: str) -> dict[str, Any] | None:
    if not item:
        return None
    window = item.get(window_key) or {}
    return {
        "name": item.get("name"),
        "valid_setups": int(window.get("valid_setups") or 0),
        "closed_trades": int(window.get("closed_trades") or 0),
        "wins": int(window.get("wins") or 0),
        "losses": int(window.get("losses") or 0),
        "win_rate": float(window.get("win_rate") or 0.0),
        "total_r": float(window.get("total_r") or 0.0),
        "average_r": float(window.get("average_r") or 0.0),
    }


def _top_failure_reason_for_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    counter = Counter()
    for entry in entries:
        reason = _entry_failure_reason(entry)
        if reason:
            counter[reason] += 1
    if not counter:
        return {"reason": "No forward-test-week failure yet", "count": 0}
    reason, count = counter.most_common(1)[0]
    return {"reason": reason, "count": count}


def _build_summary_metrics(entries: list[dict[str, Any]]) -> dict[str, Any]:
    valid_entries = [entry for entry in entries if _is_valid_setup(entry)]
    triggered_entries = [entry for entry in valid_entries if bool(entry.get("entry_triggered"))]
    closed_entries = [entry for entry in valid_entries if str(entry.get("result") or "").upper() in {"WIN", "LOSS"}]
    wins = [entry for entry in closed_entries if str(entry.get("result") or "").upper() == "WIN"]
    losses = [entry for entry in closed_entries if str(entry.get("result") or "").upper() == "LOSS"]
    total_r = round(sum(_safe_float(entry.get("rr_achieved")) for entry in closed_entries), 2)
    average_r = round(total_r / len(closed_entries), 2) if closed_entries else 0.0

    return {
        "total_setups": len(entries),
        "valid_setups": len(valid_entries),
        "activated_entries": len(triggered_entries),
        "closed_trades": len(closed_entries),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round((len(wins) / len(closed_entries)) * 100, 2) if closed_entries else 0.0,
        "total_r": total_r,
        "average_r": average_r,
    }


def _build_window_metrics(entries: list[dict[str, Any]]) -> dict[str, Any]:
    valid_entries = [entry for entry in entries if _is_valid_setup(entry)]
    triggered_entries = [entry for entry in valid_entries if bool(entry.get("entry_triggered"))]
    closed_entries = [entry for entry in valid_entries if str(entry.get("result") or "").upper() in {"WIN", "LOSS"}]
    wins = [entry for entry in closed_entries if str(entry.get("result") or "").upper() == "WIN"]
    losses = [entry for entry in closed_entries if str(entry.get("result") or "").upper() == "LOSS"]
    total_r = round(sum(_safe_float(entry.get("rr_achieved")) for entry in closed_entries), 2)
    average_r = round(total_r / len(closed_entries), 2) if closed_entries else 0.0

    return {
        "total_setups": len(entries),
        "valid_setups": len(valid_entries),
        "activated_entries": len(triggered_entries),
        "closed_trades": len(closed_entries),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round((len(wins) / len(closed_entries)) * 100, 2) if closed_entries else 0.0,
        "total_r": total_r,
        "average_r": average_r,
        "draft_to_trigger_conversion": round((len(triggered_entries) / len(valid_entries)) * 100, 2) if valid_entries else 0.0,
        "trigger_to_win_conversion": round((len(wins) / len(triggered_entries)) * 100, 2) if triggered_entries else 0.0,
        "most_common_failure_reason": _most_common_failure_reason(entries),
    }


def _is_valid_setup(entry: dict[str, Any]) -> bool:
    status = str(entry.get("status") or "").upper()
    return entry.get("entry") is not None and status != "NO_TRADE"


def _most_common_failure_reason(entries: list[dict[str, Any]]) -> str:
    counter: Counter[str] = Counter()
    for entry in entries:
        status = str(entry.get("status") or "").upper()
        if status == "NO_TRADE":
            for item in entry.get("missing", []) or []:
                reason = str(item or "").strip()
                if reason:
                    counter[reason] += 1
            continue

        reason = _entry_failure_reason(entry)
        if reason:
            counter[reason] += 1

    return counter.most_common(1)[0][0] if counter else "No dominant failure yet"


def _entry_failure_reason(entry: dict[str, Any]) -> str | None:
    result = str(entry.get("result") or "").upper()
    status = str(entry.get("status") or "").upper()

    if result == "LOSS":
        reason = str(entry.get("close_reason") or "STOP_LOSS_HIT").strip()
        return reason or "STOP_LOSS_HIT"

    if status == "ARCHIVED" or result == "ARCHIVED":
        reason = str(entry.get("reconciliation_reason") or entry.get("close_reason") or "Never activated").strip()
        return reason or "Never activated"

    return None


def _promotion_status(window: dict[str, Any]) -> tuple[str, list[str]]:
    valid_setups = int(window.get("valid_setups") or 0)
    closed_trades = int(window.get("closed_trades") or 0)
    win_rate = float(window.get("win_rate") or 0.0)
    average_r = float(window.get("average_r") or 0.0)
    total_r = float(window.get("total_r") or 0.0)

    reasons: list[str] = []
    if valid_setups < 3:
        reasons.append("Need at least 3 valid forward-test-week setups.")
    if closed_trades < 4:
        reasons.append("Need at least 4 forward-test-week closed trades.")
    if win_rate < 50.0:
        reasons.append("Forward-test-week win rate is below 50%.")
    if average_r <= 0.0:
        reasons.append("Forward-test-week average R is not positive.")
    if total_r <= 0.0:
        reasons.append("Forward-test-week total R is not positive.")

    if not reasons:
        return "PROMOTED", []
    if closed_trades >= 4 and (win_rate < 40.0 or total_r < 0.0):
        return "REVIEW", reasons
    return "BUILDING", reasons


def _filter_forward_test_week_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    current = datetime.now(UTC)
    week_start = (current - timedelta(days=current.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = week_start + timedelta(days=7)
    filtered: list[dict[str, Any]] = []

    for entry in entries:
        if not bool(entry.get("forward_test_mode")):
            continue
        timestamp = _entry_timestamp(entry)
        if week_start <= timestamp < week_end:
            filtered.append(entry)
    return filtered


def _entry_timestamp(entry: dict[str, Any]) -> datetime:
    raw = str(entry.get("closed_at") or entry.get("timestamp") or "").strip()
    if not raw:
        return datetime.now(UTC)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0

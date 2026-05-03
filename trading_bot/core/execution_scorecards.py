from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from trading_bot.core.journal import load_journal_entries, summarize_forward_test


def build_execution_scorecards(*, days: int = 7) -> dict[str, Any]:
    entries = load_journal_entries()
    forward_test_entries = [
        entry
        for entry in entries
        if bool(entry.get("forward_test_mode")) and not bool(entry.get("shadow_mode"))
    ]

    today = datetime.now(UTC).date()
    daily_cards: list[dict[str, Any]] = []
    cumulative_entries: list[dict[str, Any]] = []

    for offset in range(days - 1, -1, -1):
        current_day = today - timedelta(days=offset)
        day_entries = [entry for entry in forward_test_entries if _entry_day(entry) == current_day]
        cumulative_entries.extend(day_entries)
        closed = [entry for entry in day_entries if str(entry.get("result") or "").upper() in {"WIN", "LOSS"}]
        wins = [entry for entry in closed if str(entry.get("result") or "").upper() == "WIN"]
        losses = [entry for entry in closed if str(entry.get("result") or "").upper() == "LOSS"]
        total_r = round(sum(float(entry.get("rr_achieved") or 0.0) for entry in closed), 2)
        avg_r = round(total_r / len(closed), 2) if closed else 0.0
        triggered = [entry for entry in day_entries if bool(entry.get("entry_triggered"))]
        open_entries = [entry for entry in day_entries if str(entry.get("status") or "").upper() == "OPEN"]

        daily_cards.append(
            {
                "date": current_day.isoformat(),
                "entries": len(day_entries),
                "triggered": len(triggered),
                "open": len(open_entries),
                "closed": len(closed),
                "wins": len(wins),
                "losses": len(losses),
                "win_rate": round((len(wins) / len(closed)) * 100, 2) if closed else 0.0,
                "total_r": total_r,
                "average_r": avg_r,
            }
        )

    promotion_history = [
        _promotion_gate_snapshot(current_day=today - timedelta(days=offset), entries=[
            entry for entry in forward_test_entries if _entry_day(entry) <= (today - timedelta(days=offset))
        ])
        for offset in range(days - 1, -1, -1)
    ]

    return {
        "summary": summarize_forward_test(),
        "daily": daily_cards,
        "promotion_history": promotion_history,
    }


def _promotion_gate_snapshot(*, current_day: date, entries: list[dict[str, Any]]) -> dict[str, Any]:
    closed = [entry for entry in entries if str(entry.get("result") or "").upper() in {"WIN", "LOSS"}]
    wins = [entry for entry in closed if str(entry.get("result") or "").upper() == "WIN"]
    total_r = round(sum(float(entry.get("rr_achieved") or 0.0) for entry in closed), 2)
    avg_r = round(total_r / len(closed), 2) if closed else 0.0
    win_rate = round((len(wins) / len(closed)) * 100, 2) if closed else 0.0
    reasons: list[str] = []
    if len(closed) < 8:
        reasons.append("Need at least 8 forward-test closed trades.")
    if win_rate < 50.0:
        reasons.append("Forward-test win rate is below 50%.")
    if avg_r <= 0.0:
        reasons.append("Forward-test average R is not positive.")
    if total_r <= 0.0:
        reasons.append("Forward-test total R is not positive.")
    return {
        "date": current_day.isoformat(),
        "status": "READY_FOR_SHADOW_EXECUTION" if not reasons else "BUILDING_TRUST",
        "closed": len(closed),
        "win_rate": win_rate,
        "average_r": avg_r,
        "total_r": total_r,
        "reasons": reasons,
    }


def _entry_day(entry: dict[str, Any]) -> date:
    timestamp = str(entry.get("closed_at") or entry.get("timestamp") or "")
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        parsed = datetime.now(UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).date()

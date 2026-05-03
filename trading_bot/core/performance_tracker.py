"""
performance_tracker.py – Win rate, profit factor, streaks
"""
from __future__ import annotations


def compute_performance(entries: list[dict]) -> dict:
    closed = [e for e in entries if e.get("outcome") in ("WIN", "LOSS")]
    wins   = [e for e in closed if e["outcome"] == "WIN"]
    losses = [e for e in closed if e["outcome"] == "LOSS"]
    open_t = [e for e in entries if e.get("outcome") == "OPEN"]

    total   = len(closed)
    win_pct = round(len(wins) / total * 100, 1) if total else 0

    avg_rr_win  = round(sum(e.get("rr", 0) for e in wins)   / len(wins),  2) if wins   else 0
    avg_rr_loss = round(sum(e.get("rr", 0) for e in losses) / len(losses), 2) if losses else 0
    profit_factor = round(
        (len(wins) * avg_rr_win) / max(len(losses) * 1, 1), 2
    ) if losses else float("inf")

    # Streak
    streak, max_streak = 0, 0
    for e in reversed(closed):
        if e["outcome"] == "WIN":
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            break

    return {
        "total_trades":   total,
        "wins":           len(wins),
        "losses":         len(losses),
        "open":           len(open_t),
        "win_rate_pct":   win_pct,
        "avg_rr_win":     avg_rr_win,
        "profit_factor":  profit_factor,
        "current_streak": streak,
        "max_win_streak": max_streak,
    }

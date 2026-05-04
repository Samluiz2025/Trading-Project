"""
performance_tracker.py – Win rate, profit factor, streaks, per-pair & per-strategy breakdown
"""
from __future__ import annotations
from collections import defaultdict


def _outcome(e: dict) -> str:
    """Return normalised outcome from either the outcome or result field."""
    v = str(e.get("outcome") or e.get("result") or "").upper()
    return v if v in ("WIN", "LOSS", "OPEN", "ARCHIVED") else "OPEN"


def compute_performance(entries: list[dict]) -> dict:
    closed = [e for e in entries if _outcome(e) in ("WIN", "LOSS")]
    wins   = [e for e in closed if _outcome(e) == "WIN"]
    losses = [e for e in closed if _outcome(e) == "LOSS"]
    open_t = [e for e in entries if _outcome(e) == "OPEN"]

    total   = len(closed)
    win_pct = round(len(wins) / total * 100, 1) if total else 0

    avg_rr_win  = round(sum(e.get("rr", 0) for e in wins)   / len(wins),  2) if wins   else 0
    avg_rr_loss = round(sum(e.get("rr", 0) for e in losses) / len(losses), 2) if losses else 0
    profit_factor = round(
        (len(wins) * avg_rr_win) / max(len(losses) * 1, 1), 2
    ) if losses else float("inf")

    # Current win streak
    streak = 0
    for e in reversed(closed):
        if e["outcome"] == "WIN":
            streak += 1
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
        "per_pair":       _per_pair(closed),
        "per_strategy":   _per_strategy(closed),
        "per_session":    _per_session(closed),
        "recent_trades":  _recent(closed, 20),
    }


def _bucket(entries: list[dict], key: str) -> dict:
    buckets: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        buckets[str(e.get(key) or "Unknown")].append(e)
    result = {}
    for label, trades in sorted(buckets.items()):
        w = sum(1 for t in trades if _outcome(t) == "WIN")
        l = sum(1 for t in trades if _outcome(t) == "LOSS")
        total = w + l
        win_rr = sum(
            float(t.get("rr_achieved") or t.get("rr") or t.get("target_rr") or 0)
            for t in trades if _outcome(t) == "WIN"
        )
        result[label] = {
            "trades":      total,
            "wins":        w,
            "losses":      l,
            "win_rate_pct": round(w / total * 100, 1) if total else 0,
            "avg_rr":      round(win_rr / w, 2) if w else 0,
        }
    return result


def _per_pair(closed: list[dict]) -> dict:
    return _bucket(closed, "symbol")


def _per_strategy(closed: list[dict]) -> dict:
    return _bucket(closed, "strategy")


def _per_session(closed: list[dict]) -> dict:
    return _bucket(closed, "session")


def _recent(closed: list[dict], n: int) -> list[dict]:
    return [
        {
            "symbol":    e.get("symbol"),
            "bias":      e.get("bias"),
            "outcome":   _outcome(e),
            "score":     e.get("score") or e.get("quality"),
            "rr":        e.get("rr") or e.get("target_rr"),
            "strategy":  e.get("strategy"),
            "timestamp": e.get("timestamp"),
            "closed_at": e.get("closed_at"),
        }
        for e in closed[-n:]
    ]

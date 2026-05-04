"""
journal.py – Trade journal with persistence and entry normalisation.
"""
from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

DATA_DIR    = Path(__file__).resolve().parent.parent / "data"
JOURNAL_FILE = DATA_DIR / "trade_journal.json"

# Map old strategy names → current registry names
_STRATEGY_MAP = {
    "Strict Liquidity Sweep":                                       "Sweep Reversal",
    "Pullback Continuation":                                        "Trend Pullback Continuation",
    "Pullback Continuation + HTF Liquidity Swing":                  "Trend Pullback Continuation",
    "Pullback Continuation + HTF Liquidity Swing + Directional Bias Reaction": "Trend Pullback Continuation",
    "Pullback Continuation + HTF Liquidity Swing + HTF OB Reaction":"Trend Pullback Continuation",
    "HTF Liquidity Swing":                                          "HTF Zone Reaction",
    "Directional Bias Reaction":                                    "HTF Zone Reaction",
}


def _normalize(entry: dict) -> dict:
    """Normalise old-format fields so every consumer sees consistent keys."""
    e = dict(entry)

    # sl / tp — old entries used stop_loss / take_profit
    if "sl" not in e and "stop_loss" in e:
        e["sl"] = e["stop_loss"]
    if "tp" not in e and "take_profit" in e:
        e["tp"] = e["take_profit"]

    # score — old entries used quality
    if "score" not in e and "quality" in e:
        e["score"] = e["quality"]

    # outcome — old entries stored result but not outcome
    result  = str(e.get("result") or "").upper()
    outcome = str(e.get("outcome") or "")
    if not outcome or outcome in ("?", "None", "none"):
        if result in ("WIN", "LOSS", "ARCHIVED"):
            e["outcome"] = result
        else:
            e["outcome"] = "OPEN"

    # strategy — normalise legacy names
    strat = e.get("strategy")
    if not strat or strat in ("?", "None", "none"):
        e["strategy"] = "Sweep Reversal"   # default: current active strategy
    elif strat in _STRATEGY_MAP:
        e["strategy"] = _STRATEGY_MAP[strat]

    return e


def load_journal() -> list[dict]:
    if not JOURNAL_FILE.exists():
        return []
    try:
        raw = json.loads(JOURNAL_FILE.read_text())
        return [_normalize(e) for e in raw]
    except Exception:
        return []


# Alias used by strategy_lab.py
def load_journal_entries() -> list[dict]:
    return load_journal()


def save_journal(entries: list[dict]):
    DATA_DIR.mkdir(exist_ok=True)
    JOURNAL_FILE.write_text(json.dumps(entries, indent=2, default=str))


def append_journal(entry: dict):
    DATA_DIR.mkdir(exist_ok=True)
    raw = []
    if JOURNAL_FILE.exists():
        try:
            raw = json.loads(JOURNAL_FILE.read_text())
        except Exception:
            pass
    entry.setdefault("logged_at", datetime.now(timezone.utc).isoformat())
    raw.append(entry)
    JOURNAL_FILE.write_text(json.dumps(raw, indent=2, default=str))


def update_journal_entries(updates: list[dict[str, Any]]) -> int:
    """Bulk-update entries matched by (symbol, timestamp)."""
    if not JOURNAL_FILE.exists():
        return 0
    try:
        raw = json.loads(JOURNAL_FILE.read_text())
    except Exception:
        return 0

    index: dict[str, int] = {
        f"{e.get('symbol')}|{e.get('timestamp')}": i
        for i, e in enumerate(raw)
    }
    changed = 0
    for upd in updates:
        key = f"{upd.get('symbol')}|{upd.get('timestamp')}"
        if key in index:
            raw[index[key]].update(upd)
            changed += 1
    if changed:
        JOURNAL_FILE.write_text(json.dumps(raw, indent=2, default=str))
    return changed

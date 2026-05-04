"""
journal.py – Trade journal with persistence
"""
from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
JOURNAL_FILE = DATA_DIR / "trade_journal.json"


def load_journal() -> list[dict]:
    if not JOURNAL_FILE.exists():
        return []
    try:
        return json.loads(JOURNAL_FILE.read_text())
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
    entries = load_journal()
    entry.setdefault("logged_at", datetime.now(timezone.utc).isoformat())
    entries.append(entry)
    save_journal(entries)


def update_journal_entries(updates: list[dict[str, Any]]):
    """Bulk-update entries matched by (symbol, timestamp). updates is a list of
    dicts with at minimum 'symbol' and 'timestamp' keys plus fields to overwrite."""
    entries = load_journal()
    index: dict[str, int] = {
        f"{e.get('symbol')}|{e.get('timestamp')}": i
        for i, e in enumerate(entries)
    }
    changed = 0
    for upd in updates:
        key = f"{upd.get('symbol')}|{upd.get('timestamp')}"
        if key in index:
            entries[index[key]].update(upd)
            changed += 1
    if changed:
        save_journal(entries)
    return changed

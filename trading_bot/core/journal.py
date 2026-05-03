"""
journal.py – Trade journal with persistence
"""
from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime, timezone

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
JOURNAL_FILE = DATA_DIR / "trade_journal.json"


def load_journal() -> list[dict]:
    if not JOURNAL_FILE.exists():
        return []
    try:
        return json.loads(JOURNAL_FILE.read_text())
    except Exception:
        return []


def append_journal(entry: dict):
    DATA_DIR.mkdir(exist_ok=True)
    entries = load_journal()
    entry.setdefault("logged_at", datetime.now(timezone.utc).isoformat())
    entries.append(entry)
    JOURNAL_FILE.write_text(json.dumps(entries, indent=2))

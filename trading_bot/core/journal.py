from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
JOURNAL_PATH = DATA_DIR / "trade_journal.json"


def load_journal_entries() -> list[dict[str, Any]]:
    if not JOURNAL_PATH.exists():
        return []
    return json.loads(JOURNAL_PATH.read_text(encoding="utf-8"))


def save_journal_entries(entries: list[dict[str, Any]]) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    JOURNAL_PATH.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    return JOURNAL_PATH


def ensure_trade_logged(
    *,
    symbol: str,
    strategy: str,
    entry: float,
    stop_loss: float,
    take_profit: float,
    confluences: list[str],
    confidence: int,
    timeframe: str,
    timeframes_used: list[str] | None = None,
    profit_factor: float | None = None,
) -> dict[str, Any] | None:
    """
    Persist a newly detected trade only once.

    This keeps the journal useful even when the dashboard refreshes every few seconds.
    """

    entries = load_journal_entries()
    signature = _build_signature(symbol=symbol, strategy=strategy, entry=entry, timeframe=timeframe)
    if any(entry_row.get("signature") == signature for entry_row in entries):
        return None

    journal_entry = {
        "id": signature,
        "signature": signature,
        "symbol": symbol.upper(),
        "strategy": strategy,
        "entry": round(entry, 4),
        "stop_loss": round(stop_loss, 4),
        "take_profit": round(take_profit, 4),
        "confluences": confluences,
        "confidence": int(confidence),
        "timeframe": timeframe,
        "timeframes_used": timeframes_used or [timeframe],
        "profit_factor": profit_factor,
        "timestamp": datetime.now(UTC).isoformat(),
        "status": "OPEN",
        "result": None,
        "rr_achieved": None,
    }
    entries.append(journal_entry)
    save_journal_entries(entries[-500:])
    return journal_entry


def update_trade_result(
    *,
    symbol: str,
    timeframe: str,
    outcome: str,
    pnl: float,
    strategy: str | None = None,
) -> dict[str, Any] | None:
    entries = load_journal_entries()
    normalized_outcome = outcome.upper()

    for entry in reversed(entries):
        if entry.get("symbol") != symbol.upper():
            continue
        if entry.get("timeframe") != timeframe:
            continue
        if entry.get("status") == normalized_outcome:
            return entry
        if strategy and entry.get("strategy") != strategy:
            continue
        if entry.get("status") == "OPEN":
            entry["status"] = normalized_outcome
            entry["result"] = normalized_outcome
            entry["rr_achieved"] = round(float(pnl), 2)
            entry["closed_at"] = datetime.now(UTC).isoformat()
            save_journal_entries(entries)
            return entry
    return None


def get_recent_journal(limit: int = 20) -> list[dict[str, Any]]:
    entries = load_journal_entries()
    return list(reversed(entries[-limit:]))


def _build_signature(symbol: str, strategy: str, entry: float, timeframe: str) -> str:
    return "|".join([symbol.upper(), strategy, timeframe, f"{entry:.4f}"])

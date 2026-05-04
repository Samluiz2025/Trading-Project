"""
outcome_resolver.py
────────────────────────────────────────────────────────────────────────────
Resolves OPEN journal entries to WIN or LOSS by replaying price action
since the entry was logged.

Logic per entry:
  - Fetch M15 bars from entry timestamp to now
  - Walk bars in chronological order
  - BUY:  if low  <= SL  → LOSS  |  if high >= TP → WIN
  - SELL: if high >= SL  → LOSS  |  if low  <= TP → WIN
  - Whichever level is touched first wins
  - If neither touched yet → remains OPEN
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from .journal import load_journal, update_journal_entries
from .data_fetcher import fetch_ohlcv

logger = logging.getLogger(__name__)

# Only resolve entries older than this — gives the trade time to develop
MIN_AGE_MINUTES = 15


def resolve_open_outcomes(source: str = "auto") -> int:
    """
    Scan all OPEN journal entries and resolve any whose price has already
    hit SL or TP.  Returns the number of entries resolved.
    load_journal() normalises old-format entries before we see them.
    """
    entries = load_journal()
    now = datetime.now(timezone.utc)
    updates: list[dict] = []

    open_entries = [e for e in entries if e.get("outcome") == "OPEN"]
    if not open_entries:
        return 0

    # Group by symbol so we only fetch data once per symbol
    by_symbol: dict[str, list[dict]] = {}
    for e in open_entries:
        sym = e.get("symbol", "")
        if sym:
            by_symbol.setdefault(sym, []).append(e)

    for symbol, sym_entries in by_symbol.items():
        # Fetch enough M15 history to cover all open entries for this symbol
        df = fetch_ohlcv(symbol, "15m", source, limit=500)
        if df is None or df.empty:
            continue

        for entry in sym_entries:
            result = _check_entry(entry, df, now)
            if result:
                updates.append(result)

    resolved = update_journal_entries(updates) if updates else 0
    if resolved:
        logger.info("Outcome resolver: resolved %d entries", resolved)
    return resolved


def _check_entry(entry: dict, df, now: datetime) -> Optional[dict]:
    """Return an update dict if the entry can be closed, else None."""
    try:
        ts_raw  = entry.get("timestamp") or entry.get("logged_at", "")
        symbol  = entry.get("symbol", "")
        bias    = str(entry.get("bias", "")).upper()
        entry_p = float(entry.get("entry", 0))
        # support both new (sl/tp) and old (stop_loss/take_profit) field names
        sl      = float(entry.get("sl") or entry.get("stop_loss") or 0)
        tp      = float(entry.get("tp") or entry.get("take_profit") or 0)
        rr      = float(entry.get("rr") or entry.get("target_rr") or 0)

        if not all([symbol, bias, entry_p, sl, tp]):
            return None

        # Parse entry timestamp
        entry_time = _parse_ts(ts_raw)
        if entry_time is None:
            return None

        # Skip if entry is too recent
        if (now - entry_time).total_seconds() < MIN_AGE_MINUTES * 60:
            return None

        # Filter bars to those after entry time
        bars = df[df.index > entry_time].copy()
        if bars.empty:
            return None

        outcome     = None
        closed_at   = None
        rr_achieved = None

        for ts, row in bars.iterrows():
            high = float(row["high"])
            low  = float(row["low"])

            if bias == "BUY":
                if low <= sl:
                    outcome, rr_achieved = "LOSS", -1.0
                    closed_at = ts
                    break
                if high >= tp:
                    outcome, rr_achieved = "WIN", rr
                    closed_at = ts
                    break
            else:  # SELL
                if high >= sl:
                    outcome, rr_achieved = "LOSS", -1.0
                    closed_at = ts
                    break
                if low <= tp:
                    outcome, rr_achieved = "WIN", rr
                    closed_at = ts
                    break

        if outcome is None:
            return None

        return {
            "symbol":      symbol,
            "timestamp":   entry.get("timestamp"),
            "outcome":     outcome,
            "result":      outcome,
            "rr_achieved": rr_achieved,
            "closed_at":   closed_at.isoformat() if hasattr(closed_at, "isoformat") else str(closed_at),
        }

    except Exception as e:
        logger.debug("Outcome check failed for %s: %s", entry.get("symbol"), e)
        return None


def _parse_ts(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None

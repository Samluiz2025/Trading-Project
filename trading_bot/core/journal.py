from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trading_bot.core.data_fetcher import FetchConfig, fetch_ohlc


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
    confluences: list[Any],
    confidence: int,
    timeframe: str,
    source: str = "auto",
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
        "source": source,
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


def log_rejected_analysis(
    *,
    symbol: str,
    strategy: str,
    missing: list[str],
    timeframe: str,
    source: str = "auto",
    message: str = "No valid setup available",
) -> dict[str, Any] | None:
    entries = load_journal_entries()
    signature = "|".join([symbol.upper(), strategy, timeframe, "NO_TRADE", ",".join(sorted(missing))])
    if any(entry_row.get("signature") == signature for entry_row in entries):
        return None

    payload = {
        "id": signature,
        "signature": signature,
        "symbol": symbol.upper(),
        "strategy": strategy,
        "entry": None,
        "stop_loss": None,
        "take_profit": None,
        "confluences": [],
        "confidence": 0,
        "timeframe": timeframe,
        "source": source,
        "timeframes_used": [timeframe],
        "profit_factor": None,
        "timestamp": datetime.now(UTC).isoformat(),
        "status": "NO_TRADE",
        "result": None,
        "rr_achieved": None,
        "missing": missing,
        "message": message,
    }
    entries.append(payload)
    save_journal_entries(entries[-500:])
    return payload


def update_open_trade_outcomes(default_source: str = "auto") -> list[dict[str, Any]]:
    """
    Check open journaled trades against the latest candle and close them on TP/SL.

    The resolution is conservative: if both SL and TP are touched in the same
    candle, the trade is marked as a loss.
    """

    entries = load_journal_entries()
    updated_entries: list[dict[str, Any]] = []
    changed = False

    for entry in entries:
        if entry.get("status") != "OPEN":
            continue

        symbol = entry.get("symbol")
        timeframe = entry.get("timeframe")
        if not symbol or not timeframe:
            continue

        try:
            candles = fetch_ohlc(
                FetchConfig(
                    symbol=symbol,
                    interval=timeframe,
                    limit=3,
                    source=entry.get("source", default_source),
                )
            )
        except Exception:
            continue

        if candles.empty:
            continue

        latest = candles.iloc[-1]
        trade_update = _resolve_open_trade(entry, latest)
        if trade_update is None:
            continue

        entry.update(trade_update)
        updated_entries.append(dict(entry))
        changed = True

    if changed:
        save_journal_entries(entries)

    return updated_entries


def _build_signature(symbol: str, strategy: str, entry: float, timeframe: str) -> str:
    return "|".join([symbol.upper(), strategy, timeframe, f"{entry:.4f}"])


def _resolve_open_trade(entry: dict[str, Any], latest_candle) -> dict[str, Any] | None:
    entry_price = float(entry["entry"])
    stop_loss = float(entry["stop_loss"])
    take_profit = float(entry["take_profit"])
    candle_low = float(latest_candle["low"])
    candle_high = float(latest_candle["high"])
    bias = _infer_trade_side(entry)

    if bias == "BUY":
        stop_hit = candle_low <= stop_loss
        target_hit = candle_high >= take_profit
    else:
        stop_hit = candle_high >= stop_loss
        target_hit = candle_low <= take_profit

    if not stop_hit and not target_hit:
        return None

    risk = abs(entry_price - stop_loss)
    reward = abs(take_profit - entry_price)
    if risk <= 0:
        risk = 1.0

    if stop_hit:
        return {
            "status": "LOSS",
            "result": "LOSS",
            "rr_achieved": round(-1.0, 2),
            "close_reason": "STOP_LOSS_HIT",
            "closed_at": datetime.now(UTC).isoformat(),
        }

    return {
        "status": "WIN",
        "result": "WIN",
        "rr_achieved": round(reward / risk, 2),
        "close_reason": "TAKE_PROFIT_HIT",
        "closed_at": datetime.now(UTC).isoformat(),
    }


def _infer_trade_side(entry: dict[str, Any]) -> str:
    bias = str(entry.get("bias") or "").upper()
    if bias in {"BUY", "SELL"}:
        return bias

    entry_price = float(entry["entry"])
    take_profit = float(entry["take_profit"])
    return "BUY" if take_profit >= entry_price else "SELL"

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trading_bot.core.data_fetcher import FetchConfig, fetch_ohlc
from trading_bot.core.digital_twin import load_digital_twin_state
from trading_bot.core.strategy_registry import ALL_LIVE_STRATEGIES, is_live_strategy, strategy_matches_scope


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
JOURNAL_PATH = DATA_DIR / "trade_journal.json"
SNAPSHOTS_DIR = DATA_DIR / "journal_snapshots"
ACTIVE_STRATEGY = ALL_LIVE_STRATEGIES
PENDING_OPEN_TIMEOUT_HOURS = 18
TRIGGERED_OPEN_TIMEOUT_HOURS = 72


def load_journal_entries() -> list[dict[str, Any]]:
    if not JOURNAL_PATH.exists():
        return []
    try:
        raw = JOURNAL_PATH.read_text(encoding="utf-8-sig")
    except OSError:
        return []

    cleaned = raw.replace("\x00", "").strip()
    if not cleaned:
        save_journal_entries([])
        return []

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        backup_path = JOURNAL_PATH.with_suffix(".corrupt.json")
        try:
            backup_path.write_text(raw, encoding="utf-8", errors="ignore")
        except OSError:
            pass
        save_journal_entries([])
        return []

    return payload if isinstance(payload, list) else []


def save_journal_entries(entries: list[dict[str, Any]]) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        JOURNAL_PATH.write_text(json.dumps(entries, separators=(",", ":")), encoding="utf-8")
    except OSError as exc:
        print(f"[WARN] Failed to save trade journal: {exc}")
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
    analysis_snapshot: dict[str, Any] | None = None,
    session: str | None = None,
    setup_grade: str | None = None,
    invalidation: float | None = None,
    reason: str | None = None,
    shadow_mode: bool = False,
    challenge_mode: bool = False,
    challenge_name: str | None = None,
    planned_risk: float | None = None,
) -> dict[str, Any] | None:
    entries = load_journal_entries()
    signature = _build_signature(symbol=symbol, strategy=strategy, entry=entry, timeframe=timeframe, shadow_mode=shadow_mode)
    if any(entry_row.get("signature") == signature for entry_row in entries):
        return None

    rr = _infer_rr(entry, stop_loss, take_profit)
    snapshot_path = _write_snapshot(signature, analysis_snapshot) if analysis_snapshot else None
    snapshot_image_path = _write_snapshot_svg(signature, analysis_snapshot) if analysis_snapshot else None
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
        "session": session,
        "timeframes_used": timeframes_used or [timeframe],
        "profit_factor": profit_factor,
        "timestamp": datetime.now(UTC).isoformat(),
        "status": "SHADOW_OPEN" if shadow_mode else "OPEN",
        "result": None,
        "rr_achieved": None,
        "entry_triggered": False,
        "triggered_at": None,
        "quality": _classify_quality(confidence=int(confidence), rr=rr),
        "target_rr": rr,
        "setup_grade": setup_grade,
        "invalidation": round(float(invalidation), 4) if invalidation is not None else None,
        "reason": reason,
        "shadow_mode": bool(shadow_mode),
        "trade_mode": "shadow" if shadow_mode else "live",
        "challenge_mode": bool(challenge_mode),
        "challenge_name": challenge_name,
        "planned_risk": round(float(planned_risk), 2) if planned_risk is not None else None,
        "snapshot_path": str(snapshot_path) if snapshot_path else None,
        "snapshot_image_path": str(snapshot_image_path) if snapshot_image_path else None,
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
        if _is_open_entry(entry):
            entry["status"] = _closed_status_for_entry(entry, normalized_outcome)
            entry["result"] = normalized_outcome
            entry["rr_achieved"] = round(float(pnl), 2)
            entry["closed_at"] = datetime.now(UTC).isoformat()
            save_journal_entries(entries)
            return entry
    return None


def get_recent_journal(
    limit: int = 20,
    pair: str | None = None,
    result: str | None = None,
    month: str | None = None,
    quality: str | None = None,
) -> list[dict[str, Any]]:
    entries = load_journal_entries()
    filtered = _filter_entries(entries, pair=pair, result=result, month=month, quality=quality)
    return list(reversed(filtered[-limit:]))


def summarize_journal(
    *,
    pair: str | None = None,
    result: str | None = None,
    month: str | None = None,
    quality: str | None = None,
) -> dict[str, Any]:
    entries = _filter_entries(load_journal_entries(), pair=pair, result=result, month=month, quality=quality)
    closed = [entry for entry in entries if entry.get("result") in {"WIN", "LOSS"}]
    wins = [entry for entry in closed if entry.get("result") == "WIN"]
    losses = [entry for entry in closed if entry.get("result") == "LOSS"]
    return {
        "count": len(entries),
        "closed": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round((len(wins) / len(closed)) * 100, 2) if closed else 0.0,
    }


def find_trade_by_signature(signature: str | None) -> dict[str, Any] | None:
    if not signature:
        return None
    for entry in reversed(load_journal_entries()):
        if entry.get("signature") == signature:
            return entry
    return None


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
        "quality": "REJECTED",
        "target_rr": None,
        "snapshot_path": None,
        "snapshot_image_path": None,
    }
    entries.append(payload)
    save_journal_entries(entries[-500:])
    return payload


def update_open_trade_lifecycle(default_source: str = "auto") -> dict[str, list[dict[str, Any]]]:
    entries = load_journal_entries()
    archived_legacy = _archive_legacy_open_entries(entries)
    activated_entries: list[dict[str, Any]] = []
    closed_entries: list[dict[str, Any]] = []
    changed = archived_legacy

    for entry in entries:
        if not _is_open_entry(entry):
            continue

        symbol = entry.get("symbol")
        timeframe = entry.get("timeframe")
        if not symbol or not timeframe:
            continue

        try:
            candles = fetch_ohlc(
                FetchConfig(
                    symbol=symbol,
                    interval=_monitor_interval_for_entry(entry, timeframe),
                    limit=6,
                    source=entry.get("source", default_source),
                )
            )
        except Exception:
            continue

        if candles.empty:
            continue

        latest = candles.iloc[-1]
        was_entry_triggered = bool(entry.get("entry_triggered"))
        trade_update = _resolve_open_trade(entry, latest)
        if trade_update is None:
            continue

        if trade_update.get("status") in {"WIN", "LOSS"}:
            trade_update["status"] = _closed_status_for_entry(entry, str(trade_update.get("status")))
        entry.update(trade_update)
        changed = True
        if not was_entry_triggered and bool(entry.get("entry_triggered")) and _is_open_entry(entry):
            activated_entries.append(dict(entry))
        if entry.get("status") in {"WIN", "LOSS", "SHADOW_WIN", "SHADOW_LOSS"}:
            closed_entries.append(dict(entry))

    if changed:
        save_journal_entries(entries)

    return {"activated": activated_entries, "closed": closed_entries}


def get_challenge_trade_stats(*, challenge_name: str | None = None) -> dict[str, Any]:
    entries = load_journal_entries()
    relevant = [
        entry
        for entry in entries
        if bool(entry.get("challenge_mode"))
        and not bool(entry.get("shadow_mode"))
        and is_live_strategy(entry.get("strategy"))
        and (challenge_name is None or str(entry.get("challenge_name") or "") == str(challenge_name))
    ]
    wins = [entry for entry in relevant if str(entry.get("result") or "").upper() == "WIN"]
    losses = [entry for entry in relevant if str(entry.get("result") or "").upper() == "LOSS"]
    open_entries = [entry for entry in relevant if _is_open_entry(entry)]
    return {
        "count": len(relevant),
        "wins": len(wins),
        "losses": len(losses),
        "open": len(open_entries),
        "entries": relevant,
    }


def update_open_trade_outcomes(default_source: str = "auto") -> list[dict[str, Any]]:
    return update_open_trade_lifecycle(default_source=default_source)["closed"]


def build_open_trade_snapshot(
    *,
    entries: list[dict[str, Any]] | None = None,
    twin_state: dict[str, Any] | None = None,
    pending_timeout_hours: int = PENDING_OPEN_TIMEOUT_HOURS,
    triggered_timeout_hours: int = TRIGGERED_OPEN_TIMEOUT_HOURS,
) -> dict[str, Any]:
    entries = list(entries) if entries is not None else load_journal_entries()
    twin_state = dict(twin_state or load_digital_twin_state())
    twin_open_trades = list(twin_state.get("open_trades") or [])
    open_entries = [dict(entry) for entry in entries if _is_open_entry(entry) and not _is_shadow_entry(entry)]

    active_open_entries: list[dict[str, Any]] = []
    stale_open_entries: list[dict[str, Any]] = []
    for entry in open_entries:
        stale_reason = _stale_open_reason(
            entry,
            twin_open_trades=twin_open_trades,
            pending_timeout_hours=pending_timeout_hours,
            triggered_timeout_hours=triggered_timeout_hours,
        )
        if stale_reason:
            stale_open_entries.append({**entry, "stale_reason": stale_reason})
        else:
            active_open_entries.append(dict(entry))

    return {
        "open_entries": open_entries,
        "active_open_entries": active_open_entries,
        "stale_open_entries": stale_open_entries,
        "pending_timeout_hours": int(pending_timeout_hours),
        "triggered_timeout_hours": int(triggered_timeout_hours),
    }


def reconcile_open_trade_state(
    *,
    twin_state: dict[str, Any] | None = None,
    pending_timeout_hours: int = PENDING_OPEN_TIMEOUT_HOURS,
    triggered_timeout_hours: int = TRIGGERED_OPEN_TIMEOUT_HOURS,
) -> dict[str, Any]:
    entries = load_journal_entries()
    twin_state = dict(twin_state or load_digital_twin_state())
    archived_entries: list[dict[str, Any]] = []
    changed = _archive_legacy_open_entries(entries)
    archived_at = datetime.now(UTC).isoformat()
    twin_open_trades = list(twin_state.get("open_trades") or [])

    for entry in entries:
        if not _is_open_entry(entry):
            continue
        stale_reason = _stale_open_reason(
            entry,
            twin_open_trades=twin_open_trades,
            pending_timeout_hours=pending_timeout_hours,
            triggered_timeout_hours=triggered_timeout_hours,
        )
        if not stale_reason:
            continue
        _archive_open_entry(
            entry,
            archived_at=archived_at,
            close_reason="STATE_RECONCILED",
            reconciliation_reason=stale_reason,
        )
        archived_entries.append(dict(entry))
        changed = True

    if changed:
        save_journal_entries(entries)

    snapshot = build_open_trade_snapshot(
        entries=entries,
        twin_state=twin_state,
        pending_timeout_hours=pending_timeout_hours,
        triggered_timeout_hours=triggered_timeout_hours,
    )
    return {
        "changed": changed,
        "archived_entries": archived_entries,
        "snapshot": snapshot,
    }


def _build_signature(symbol: str, strategy: str, entry: float, timeframe: str, shadow_mode: bool = False) -> str:
    parts = [symbol.upper(), strategy, timeframe, f"{entry:.4f}"]
    if shadow_mode:
        parts.append("shadow")
    return "|".join(parts)


def _archive_legacy_open_entries(entries: list[dict[str, Any]]) -> bool:
    changed = False
    archived_at = datetime.now(UTC).isoformat()
    for entry in entries:
        if not _is_open_entry(entry):
            continue
        if is_live_strategy(entry.get("strategy")):
            continue
        _archive_open_entry(
            entry,
            archived_at=archived_at,
            close_reason="RETIRED_STRATEGY",
            reconciliation_reason="Open trade belongs to a retired strategy.",
        )
        changed = True
    return changed


def _archive_open_entry(
    entry: dict[str, Any],
    *,
    archived_at: str,
    close_reason: str,
    reconciliation_reason: str | None = None,
) -> None:
    entry["status"] = "ARCHIVED"
    entry["result"] = "ARCHIVED"
    entry["closed_at"] = archived_at
    entry["close_reason"] = close_reason
    entry["rr_achieved"] = 0.0
    if reconciliation_reason:
        entry["reconciliation_reason"] = reconciliation_reason


def _write_snapshot(signature: str, payload: dict[str, Any]) -> Path:
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    path = SNAPSHOTS_DIR / f"{signature.replace('|', '_').replace('/', '_')}.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def _write_snapshot_svg(signature: str, payload: dict[str, Any]) -> Path | None:
    candles = list((payload or {}).get("recent_candles") or [])
    if not candles:
        return None

    width = 900
    height = 420
    left_pad = 60
    right_pad = 24
    top_pad = 24
    bottom_pad = 40
    plot_width = width - left_pad - right_pad
    plot_height = height - top_pad - bottom_pad

    prices = []
    for candle in candles:
        prices.extend([
            float(candle.get("high", 0)),
            float(candle.get("low", 0)),
            float(candle.get("open", 0)),
            float(candle.get("close", 0)),
        ])
    levels = (payload or {}).get("chart_overlays", {}).get("trade_levels", {})
    for key in ("entry", "sl", "tp"):
        if levels.get(key) is not None:
            prices.append(float(levels[key]))

    if not prices:
        return None

    min_price = min(prices)
    max_price = max(prices)
    if max_price <= min_price:
        max_price = min_price + 1

    def y(price: float) -> float:
        scaled = (price - min_price) / (max_price - min_price)
        return top_pad + (plot_height * (1 - scaled))

    candle_width = max(4, plot_width / max(len(candles), 1) * 0.55)
    spacing = plot_width / max(len(candles), 1)
    body_elements = []
    wick_elements = []
    close_points = []
    for index, candle in enumerate(candles):
        x = left_pad + spacing * index + (spacing / 2)
        open_price = float(candle.get("open", 0))
        close_price = float(candle.get("close", 0))
        high_price = float(candle.get("high", 0))
        low_price = float(candle.get("low", 0))
        color = "#2ecc71" if close_price >= open_price else "#ff6b6b"
        wick_elements.append(f'<line x1="{x:.2f}" y1="{y(high_price):.2f}" x2="{x:.2f}" y2="{y(low_price):.2f}" stroke="{color}" stroke-width="1.5" />')
        body_top = min(y(open_price), y(close_price))
        body_height = max(abs(y(open_price) - y(close_price)), 2)
        body_elements.append(
            f'<rect x="{(x - candle_width / 2):.2f}" y="{body_top:.2f}" width="{candle_width:.2f}" height="{body_height:.2f}" fill="{color}" fill-opacity="0.85" rx="1" />'
        )
        close_points.append(f"{x:.2f},{y(close_price):.2f}")

    line_elements = []
    labels = []
    colors = {"entry": "#5dade2", "sl": "#ff6b6b", "tp": "#2ecc71"}
    names = {"entry": "Entry", "sl": "SL", "tp": "TP"}
    for key in ("entry", "sl", "tp"):
        if levels.get(key) is None:
            continue
        level_price = float(levels[key])
        line_y = y(level_price)
        color = colors[key]
        line_elements.append(f'<line x1="{left_pad}" y1="{line_y:.2f}" x2="{width - right_pad}" y2="{line_y:.2f}" stroke="{color}" stroke-width="1.4" stroke-dasharray="6 4" />')
        labels.append(f'<text x="{width - right_pad - 4}" y="{line_y - 6:.2f}" text-anchor="end" fill="{color}" font-size="12">{names[key]} {level_price:.4f}</text>')

    path = SNAPSHOTS_DIR / f"{signature.replace('|', '_').replace('/', '_')}.svg"
    title = (payload or {}).get("symbol", "Setup Snapshot")
    subtitle = f"{title} | {(payload or {}).get('interval', '1h')} | {(payload or {}).get('source', 'auto')}"
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<rect width="{width}" height="{height}" fill="#0a1119"/>
<rect x="{left_pad}" y="{top_pad}" width="{plot_width}" height="{plot_height}" rx="12" fill="#111b26" stroke="#24384a"/>
<text x="{left_pad}" y="18" fill="#eef4fb" font-size="16" font-family="Segoe UI">FlowScope Snapshot</text>
<text x="{left_pad}" y="36" fill="#9db0c1" font-size="12" font-family="Segoe UI">{subtitle}</text>
<polyline fill="none" stroke="#5dade2" stroke-width="1.3" points="{' '.join(close_points)}" opacity="0.45"/>
{''.join(wick_elements)}
{''.join(body_elements)}
{''.join(line_elements)}
{''.join(labels)}
</svg>"""
    path.write_text(svg, encoding="utf-8")
    return path


def _resolve_open_trade(entry: dict[str, Any], latest_candle) -> dict[str, Any] | None:
    entry_price = float(entry["entry"])
    stop_loss = float(entry["stop_loss"])
    take_profit = float(entry["take_profit"])
    candle_low = float(latest_candle["low"])
    candle_high = float(latest_candle["high"])
    bias = _infer_trade_side(entry)
    entry_triggered = bool(entry.get("entry_triggered"))
    entry_hit = candle_low <= entry_price <= candle_high

    updates: dict[str, Any] = {}
    if not entry_triggered:
        if not entry_hit:
            return None
        updates["entry_triggered"] = True
        updates["triggered_at"] = datetime.now(UTC).isoformat()
        return updates

    if bias == "BUY":
        stop_hit = candle_low <= stop_loss
        target_hit = candle_high >= take_profit
    else:
        stop_hit = candle_high >= stop_loss
        target_hit = candle_low <= take_profit

    if not stop_hit and not target_hit:
        return updates or None

    risk = abs(entry_price - stop_loss) or 1.0
    reward = abs(take_profit - entry_price)

    if stop_hit:
        return {
            **updates,
            "status": "LOSS",
            "result": "LOSS",
            "rr_achieved": round(-1.0, 2),
            "close_reason": "STOP_LOSS_HIT",
            "closed_at": datetime.now(UTC).isoformat(),
        }

    return {
        **updates,
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


def _monitor_interval_for_entry(entry: dict[str, Any], timeframe: str) -> str:
    if is_live_strategy(entry.get("strategy")):
        return "15m"
    return timeframe


def _is_shadow_entry(entry: dict[str, Any]) -> bool:
    if bool(entry.get("shadow_mode")):
        return True
    return str(entry.get("trade_mode") or "").strip().lower() == "shadow"


def _is_open_entry(entry: dict[str, Any]) -> bool:
    return str(entry.get("status") or "").upper() in {"OPEN", "SHADOW_OPEN"}


def _closed_status_for_entry(entry: dict[str, Any], outcome: str) -> str:
    normalized = str(outcome or "").upper()
    if _is_shadow_entry(entry) and normalized in {"WIN", "LOSS"}:
        return f"SHADOW_{normalized}"
    return normalized


def _stale_open_reason(
    entry: dict[str, Any],
    *,
    twin_open_trades: list[dict[str, Any]],
    pending_timeout_hours: int,
    triggered_timeout_hours: int,
) -> str | None:
    if not _is_open_entry(entry):
        return None

    if not is_live_strategy(entry.get("strategy")):
        return "Open trade belongs to a retired strategy."

    is_triggered = bool(entry.get("entry_triggered"))
    timeout_hours = int(triggered_timeout_hours if is_triggered else pending_timeout_hours)
    age_hours = _entry_age_hours(entry, triggered=is_triggered)

    if _is_shadow_entry(entry):
        if age_hours < timeout_hours:
            return None
        if is_triggered:
            return f"Shadow trade stayed open for {age_hours:.1f}h without resolution."
        return f"Shadow pending trade expired after {age_hours:.1f}h."

    if any(_matches_twin_trade(entry, trade) for trade in twin_open_trades):
        return None

    if age_hours < timeout_hours:
        return None

    if is_triggered:
        return f"Triggered trade is missing from the digital twin after {age_hours:.1f}h."
    return f"Pending trade never activated and is older than {age_hours:.1f}h."


def _matches_twin_trade(entry: dict[str, Any], trade: dict[str, Any]) -> bool:
    if str(entry.get("symbol") or "").upper() != str(trade.get("symbol") or "").upper():
        return False
    if str(entry.get("timeframe") or "").lower() != str(trade.get("timeframe") or "").lower():
        return False

    journal_strategy = str(entry.get("strategy") or "").strip().lower()
    twin_primary = str(trade.get("primary_strategy") or "").strip().lower()
    twin_strategies = {str(item).strip().lower() for item in trade.get("strategies") or []}
    if journal_strategy and journal_strategy not in twin_strategies and journal_strategy != twin_primary:
        return False

    return all(
        _prices_match(entry.get(entry_key), trade.get(trade_key))
        for entry_key, trade_key in (
            ("entry", "entry"),
            ("stop_loss", "stop_loss"),
            ("take_profit", "take_profit"),
        )
    )


def _prices_match(left: Any, right: Any, *, tolerance: float = 0.0002) -> bool:
    try:
        return abs(float(left) - float(right)) <= tolerance
    except (TypeError, ValueError):
        return False


def _entry_age_hours(entry: dict[str, Any], *, triggered: bool) -> float:
    timestamp = entry.get("triggered_at") if triggered and entry.get("triggered_at") else entry.get("timestamp")
    parsed = _parse_timestamp(timestamp)
    return max(0.0, (datetime.now(UTC) - parsed).total_seconds() / 3600.0)


def _parse_timestamp(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        parsed = datetime.now(UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _infer_rr(entry: float, stop_loss: float, take_profit: float) -> float:
    risk = abs(entry - stop_loss)
    if risk <= 0:
        return 0.0
    return round(abs(take_profit - entry) / risk, 2)


def _classify_quality(*, confidence: int, rr: float) -> str:
    if rr >= 4 and confidence >= 80:
        return "A"
    if rr >= 3 and confidence >= 70:
        return "B"
    return "C"


def _filter_entries(
    entries: list[dict[str, Any]],
    *,
    pair: str | None = None,
    result: str | None = None,
    month: str | None = None,
    quality: str | None = None,
) -> list[dict[str, Any]]:
    filtered = entries
    if pair:
        filtered = [entry for entry in filtered if entry.get("symbol") == pair.upper()]
    if result:
        normalized_result = result.upper()
        filtered = [
            entry for entry in filtered if str(entry.get("result") or entry.get("status") or "").upper() == normalized_result
        ]
    if month:
        filtered = [entry for entry in filtered if str(entry.get("closed_at") or entry.get("timestamp") or "").startswith(month)]
    if quality:
        filtered = [entry for entry in filtered if str(entry.get("quality") or "").upper() == quality.upper()]
    return filtered

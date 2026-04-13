from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from trading_bot.core.journal import ACTIVE_STRATEGY, load_journal_entries
from trading_bot.core.strategy_registry import normalize_strategy_scope, strategy_matches_scope


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
VALIDATION_MODE_PATH = DATA_DIR / "validation_mode.json"
DEFAULT_TIMEZONE = "Europe/Vienna"


def load_validation_settings() -> dict[str, Any]:
    if not VALIDATION_MODE_PATH.exists():
        default_settings = _default_validation_settings()
        save_validation_settings(default_settings)
        return default_settings

    try:
        raw = VALIDATION_MODE_PATH.read_text(encoding="utf-8-sig")
    except OSError:
        return _default_validation_settings()

    cleaned = raw.replace("\x00", "").strip()
    if not cleaned:
        default_settings = _default_validation_settings()
        save_validation_settings(default_settings)
        return default_settings

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        backup_path = VALIDATION_MODE_PATH.with_suffix(".corrupt.json")
        try:
            backup_path.write_text(raw, encoding="utf-8", errors="ignore")
        except OSError:
            pass
        default_settings = _default_validation_settings()
        save_validation_settings(default_settings)
        return default_settings

    if not isinstance(payload, dict):
        default_settings = _default_validation_settings()
        save_validation_settings(default_settings)
        return default_settings
    return _with_defaults(payload)


def save_validation_settings(settings: dict[str, Any]) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    normalized = _with_defaults(settings)
    try:
        VALIDATION_MODE_PATH.write_text(json.dumps(normalized, separators=(",", ":")), encoding="utf-8")
    except OSError as exc:
        print(f"[WARN] Failed to save validation settings: {exc}")
    return VALIDATION_MODE_PATH


def build_validation_snapshot(
    *,
    entries: list[dict[str, Any]] | None = None,
    settings: dict[str, Any] | None = None,
    shadow_mode: str = "exclude",
    sessions: list[str] | None = None,
) -> dict[str, Any]:
    settings = _with_defaults(settings or load_validation_settings())
    entries = list(entries) if entries is not None else load_journal_entries()
    timezone = _resolve_timezone(settings.get("timezone"))
    active_strategy = normalize_strategy_scope(settings.get("active_strategy") or ACTIVE_STRATEGY)
    require_triggered_entry = bool(settings.get("require_triggered_entry", True))

    normalized_shadow_mode = str(shadow_mode or "exclude").strip().lower()
    session_filter = {str(item).strip().lower() for item in sessions or [] if str(item).strip()}
    strategy_entries = [
        entry
        for entry in entries
        if strategy_matches_scope(entry.get("strategy"), active_strategy)
        and _matches_shadow_filter(entry, normalized_shadow_mode)
        and _matches_session_filter(entry, session_filter)
        and str(entry.get("result") or "").upper() in {"WIN", "LOSS"}
    ]
    strategy_entries.sort(key=_entry_sort_key)

    validated_trades = [
        _build_validated_trade(entry, settings=settings)
        for entry in strategy_entries
        if not require_triggered_entry or bool(entry.get("entry_triggered"))
    ]
    validated_closed = [trade for trade in validated_trades if trade is not None]

    raw_net_r = round(sum(float(trade["raw_rr"]) for trade in validated_closed), 2)
    adjusted_net_r = round(sum(float(trade["adjusted_rr"]) for trade in validated_closed), 2)
    gross_profit = sum(max(float(trade["adjusted_rr"]), 0.0) for trade in validated_closed)
    gross_loss = abs(sum(min(float(trade["adjusted_rr"]), 0.0) for trade in validated_closed))
    adjusted_profit_factor = round(gross_profit / gross_loss, 2) if gross_loss else round(gross_profit, 2)
    adjusted_win_rate = (
        round((sum(1 for trade in validated_closed if float(trade["adjusted_rr"]) > 0) / len(validated_closed)) * 100, 2)
        if validated_closed
        else 0.0
    )
    adjusted_expectancy_r = round(adjusted_net_r / len(validated_closed), 2) if validated_closed else 0.0

    symbol_breakdown = _build_group_breakdown(validated_closed, key_name="symbol")
    session_breakdown = _build_group_breakdown(validated_closed, key_name="session")
    equity_curve = _build_equity_curve(validated_closed)
    recent_days = _build_period_breakdown(validated_closed, timezone=timezone, period="day")
    recent_weeks = _build_period_breakdown(validated_closed, timezone=timezone, period="week")
    underperforming_symbols = _derive_underperforming_symbols(symbol_breakdown, settings=settings)

    return {
        "enabled": bool(settings.get("enabled", True)),
        "active_strategy": active_strategy,
        "shadow_mode": normalized_shadow_mode,
        "timezone": timezone.key,
        "require_triggered_entry": require_triggered_entry,
        "sessions_filter": sorted(session_filter),
        "validated_closed_trades": len(validated_closed),
        "raw_net_r": raw_net_r,
        "adjusted_net_r": adjusted_net_r,
        "adjusted_profit_factor": adjusted_profit_factor,
        "adjusted_win_rate": adjusted_win_rate,
        "adjusted_expectancy_r": adjusted_expectancy_r,
        "underperforming_symbols": underperforming_symbols,
        "symbol_breakdown": symbol_breakdown,
        "session_breakdown": session_breakdown,
        "equity_curve": equity_curve,
        "recent_days": recent_days,
        "recent_weeks": recent_weeks,
        "recent_trades": validated_closed[-20:],
        "top_symbols": symbol_breakdown[:5],
        "bottom_symbols": sorted(
            symbol_breakdown,
            key=lambda item: (float(item.get("adjusted_net_r") or 0.0), float(item.get("adjusted_expectancy_r") or 0.0), str(item.get("symbol") or "")),
        )[:5],
        "cost_model": settings.get("cost_model", {}),
        "settings": settings,
    }


def _default_validation_settings() -> dict[str, Any]:
    return {
        "enabled": True,
        "timezone": DEFAULT_TIMEZONE,
        "active_strategy": normalize_strategy_scope(ACTIVE_STRATEGY),
        "require_triggered_entry": True,
        "auto_disable_underperformers": True,
        "minimum_symbol_trades": 3,
        "maximum_negative_expectancy_r": 0.0,
        "maximum_negative_net_r": -0.5,
        "cost_model": {
            "default": {"spread_bps": 1.5, "slippage_bps": 2.5, "fee_bps": 0.0},
            "forex": {"spread_bps": 1.5, "slippage_bps": 2.5, "fee_bps": 0.0},
            "metals": {"spread_bps": 2.5, "slippage_bps": 3.5, "fee_bps": 0.0},
            "indices": {"spread_bps": 2.0, "slippage_bps": 4.0, "fee_bps": 0.0},
            "crypto": {"spread_bps": 4.0, "slippage_bps": 6.0, "fee_bps": 6.0},
        },
    }


def _with_defaults(settings: dict[str, Any]) -> dict[str, Any]:
    defaults = _default_validation_settings()
    merged = {**defaults, **dict(settings or {})}
    cost_model = {
        **defaults["cost_model"],
        **dict((settings or {}).get("cost_model") or {}),
    }
    merged["cost_model"] = cost_model
    for bucket, values in list(cost_model.items()):
        merged["cost_model"][bucket] = {
            "spread_bps": float((values or {}).get("spread_bps", 0.0)),
            "slippage_bps": float((values or {}).get("slippage_bps", 0.0)),
            "fee_bps": float((values or {}).get("fee_bps", 0.0)),
        }
    merged["active_strategy"] = normalize_strategy_scope(merged.get("active_strategy") or ACTIVE_STRATEGY)
    merged["timezone"] = _resolve_timezone(merged.get("timezone")).key
    return merged


def _resolve_timezone(value: Any) -> ZoneInfo:
    try:
        return ZoneInfo(str(value or DEFAULT_TIMEZONE))
    except Exception:
        return ZoneInfo("UTC")


def _entry_sort_key(entry: dict[str, Any]) -> datetime:
    timestamp = entry.get("closed_at") or entry.get("timestamp") or datetime.now(UTC).isoformat()
    try:
        parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except ValueError:
        parsed = datetime.now(UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _matches_shadow_filter(entry: dict[str, Any], shadow_mode: str) -> bool:
    is_shadow = bool(entry.get("shadow_mode"))
    if shadow_mode == "only":
        return is_shadow
    if shadow_mode == "include":
        return True
    return not is_shadow


def _matches_session_filter(entry: dict[str, Any], sessions: set[str]) -> bool:
    if not sessions:
        return True
    session = str(entry.get("session") or "").strip().lower()
    return session in sessions


def _build_validated_trade(entry: dict[str, Any], *, settings: dict[str, Any]) -> dict[str, Any]:
    raw_rr = float(entry.get("rr_achieved") or 0.0)
    risk_distance = abs(float(entry.get("entry") or 0.0) - float(entry.get("stop_loss") or 0.0))
    entry_price = abs(float(entry.get("entry") or 0.0))
    cost_breakdown = _estimate_cost_breakdown(
        symbol=str(entry.get("symbol") or ""),
        entry_price=entry_price,
        risk_distance=risk_distance,
        settings=settings,
    )
    adjusted_rr = round(raw_rr - float(cost_breakdown["total_cost_r"]), 2)
    closed_at = entry.get("closed_at") or entry.get("timestamp") or datetime.now(UTC).isoformat()
    return {
        "symbol": str(entry.get("symbol") or "").upper(),
        "session": str(entry.get("session") or "unknown").strip().lower() or "unknown",
        "result": str(entry.get("result") or "").upper(),
        "raw_rr": round(raw_rr, 2),
        "adjusted_rr": adjusted_rr,
        "cost_r": round(float(cost_breakdown["total_cost_r"]), 2),
        "cost_breakdown": cost_breakdown,
        "close_reason": entry.get("close_reason"),
        "closed_at": str(closed_at),
    }


def _estimate_cost_breakdown(
    *,
    symbol: str,
    entry_price: float,
    risk_distance: float,
    settings: dict[str, Any],
) -> dict[str, Any]:
    asset_class = _classify_symbol(symbol)
    cost_model = dict(settings.get("cost_model") or {})
    bucket = dict(cost_model.get(asset_class) or cost_model.get("default") or {})
    spread_bps = float(bucket.get("spread_bps") or 0.0)
    slippage_bps = float(bucket.get("slippage_bps") or 0.0)
    fee_bps = float(bucket.get("fee_bps") or 0.0)
    total_bps = spread_bps + slippage_bps + fee_bps
    if risk_distance <= 0 or entry_price <= 0:
        total_cost_r = 0.0
    else:
        total_cost_r = (entry_price * (total_bps / 10000.0)) / risk_distance
    return {
        "asset_class": asset_class,
        "spread_bps": spread_bps,
        "slippage_bps": slippage_bps,
        "fee_bps": fee_bps,
        "total_cost_r": round(total_cost_r, 4),
    }


def _classify_symbol(symbol: str) -> str:
    normalized = str(symbol or "").upper()
    if normalized.endswith("USDT"):
        return "crypto"
    if normalized in {"NAS100", "SP500", "US30", "GER40", "UK100", "JP225"}:
        return "indices"
    if normalized.startswith("XAU") or normalized.startswith("XAG"):
        return "metals"
    if len(normalized) == 6 and normalized.isalpha():
        return "forex"
    return "default"


def _build_group_breakdown(validated_closed: list[dict[str, Any]], *, key_name: str) -> list[dict[str, Any]]:
    grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in validated_closed:
        group_key = str(trade.get(key_name) or "unknown").upper() if key_name == "symbol" else str(trade.get(key_name) or "unknown")
        grouped[group_key].append(trade)

    rows = []
    for group_key, trades in grouped.items():
        raw_net_r = round(sum(float(item["raw_rr"]) for item in trades), 2)
        adjusted_net_r = round(sum(float(item["adjusted_rr"]) for item in trades), 2)
        wins = sum(1 for item in trades if float(item["adjusted_rr"]) > 0)
        losses = len(trades) - wins
        gross_profit = sum(max(float(item["adjusted_rr"]), 0.0) for item in trades)
        gross_loss = abs(sum(min(float(item["adjusted_rr"]), 0.0) for item in trades))
        rows.append(
            {
                key_name: group_key,
                "closed_trades": len(trades),
                "wins": wins,
                "losses": losses,
                "raw_net_r": raw_net_r,
                "adjusted_net_r": adjusted_net_r,
                "adjusted_expectancy_r": round(adjusted_net_r / len(trades), 2) if trades else 0.0,
                "adjusted_win_rate": round((wins / len(trades)) * 100, 2) if trades else 0.0,
                "adjusted_profit_factor": round(gross_profit / gross_loss, 2) if gross_loss else round(gross_profit, 2),
                "avg_cost_r": round(sum(float(item["cost_r"]) for item in trades) / len(trades), 2) if trades else 0.0,
            }
        )

    sort_key = "symbol" if key_name == "symbol" else "session"
    rows.sort(
        key=lambda item: (float(item["adjusted_net_r"]), float(item["adjusted_expectancy_r"]), str(item.get(sort_key) or "")),
        reverse=True,
    )
    return rows


def _build_equity_curve(validated_closed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cumulative_raw = 0.0
    cumulative_adjusted = 0.0
    points: list[dict[str, Any]] = []
    for index, trade in enumerate(validated_closed, start=1):
        cumulative_raw += float(trade["raw_rr"])
        cumulative_adjusted += float(trade["adjusted_rr"])
        points.append(
            {
                "index": index,
                "symbol": trade["symbol"],
                "closed_at": trade["closed_at"],
                "raw_cumulative_r": round(cumulative_raw, 2),
                "adjusted_cumulative_r": round(cumulative_adjusted, 2),
            }
        )
    return points[-50:]


def _build_period_breakdown(
    validated_closed: list[dict[str, Any]],
    *,
    timezone: ZoneInfo,
    period: str,
) -> list[dict[str, Any]]:
    grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in validated_closed:
        local_dt = _trade_local_datetime(trade, timezone=timezone)
        if period == "week":
            iso = local_dt.isocalendar()
            key = f"{iso.year}-W{iso.week:02d}"
        else:
            key = local_dt.date().isoformat()
        grouped[key].append(trade)

    rows = []
    for key, trades in grouped.items():
        symbol_totals: defaultdict[str, float] = defaultdict(float)
        for trade in trades:
            symbol_totals[str(trade.get("symbol") or "").upper()] += float(trade["adjusted_rr"])
        best_symbol = max(symbol_totals.items(), key=lambda item: item[1], default=(None, 0.0))
        worst_symbol = min(symbol_totals.items(), key=lambda item: item[1], default=(None, 0.0))
        rows.append(
            {
                "period": key,
                "trade_count": len(trades),
                "adjusted_net_r": round(sum(float(item["adjusted_rr"]) for item in trades), 2),
                "best_symbol": {"symbol": best_symbol[0], "adjusted_net_r": round(best_symbol[1], 2)} if best_symbol[0] else None,
                "worst_symbol": {"symbol": worst_symbol[0], "adjusted_net_r": round(worst_symbol[1], 2)} if worst_symbol[0] else None,
            }
        )
    rows.sort(key=lambda item: item["period"], reverse=True)
    return rows[:8]


def _trade_local_datetime(trade: dict[str, Any], *, timezone: ZoneInfo) -> datetime:
    timestamp = trade.get("closed_at") or datetime.now(UTC).isoformat()
    try:
        parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except ValueError:
        parsed = datetime.now(UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(timezone)


def _derive_underperforming_symbols(symbol_breakdown: list[dict[str, Any]], *, settings: dict[str, Any]) -> list[str]:
    if not bool(settings.get("auto_disable_underperformers", True)):
        return []

    minimum_symbol_trades = int(settings.get("minimum_symbol_trades") or 0)
    maximum_negative_expectancy_r = float(settings.get("maximum_negative_expectancy_r") or 0.0)
    maximum_negative_net_r = float(settings.get("maximum_negative_net_r") or 0.0)

    symbols = [
        str(item.get("symbol") or "").upper()
        for item in symbol_breakdown
        if int(item.get("closed_trades") or 0) >= minimum_symbol_trades
        and (
            float(item.get("adjusted_expectancy_r") or 0.0) <= maximum_negative_expectancy_r
            or float(item.get("adjusted_net_r") or 0.0) <= maximum_negative_net_r
        )
    ]
    return list(dict.fromkeys(symbols))

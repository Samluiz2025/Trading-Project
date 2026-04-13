from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trading_bot.core.data_fetcher import FetchConfig, fetch_ohlc
from trading_bot.core.strategy_registry import ALL_LIVE_STRATEGIES, is_live_strategy


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
DIGITAL_TWIN_PATH = DATA_DIR / "digital_twin_state.json"
DEFAULT_BALANCE = 10_000.0
DEFAULT_RISK_PER_TRADE = 200.0
ACTIVE_STRATEGY = ALL_LIVE_STRATEGIES


def load_digital_twin_state() -> dict[str, Any]:
    """Load the persistent digital twin state or create a clean default snapshot."""

    if not DIGITAL_TWIN_PATH.exists():
        return _default_twin_state()
    try:
        raw = DIGITAL_TWIN_PATH.read_text(encoding="utf-8-sig")
    except OSError:
        return _default_twin_state()

    cleaned = raw.replace("\x00", "").strip()
    if not cleaned:
        default_state = _default_twin_state()
        save_digital_twin_state(default_state)
        return default_state

    try:
        state = json.loads(cleaned)
    except json.JSONDecodeError:
        backup_path = DIGITAL_TWIN_PATH.with_suffix(".corrupt.json")
        try:
            backup_path.write_text(raw, encoding="utf-8", errors="ignore")
        except OSError:
            pass
        default_state = _default_twin_state()
        save_digital_twin_state(default_state)
        return default_state

    if not isinstance(state, dict):
        default_state = _default_twin_state()
        save_digital_twin_state(default_state)
        return default_state
    return _with_defaults(state)


def save_digital_twin_state(state: dict[str, Any]) -> Path:
    """Persist the digital twin state for reuse across monitor restarts."""

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        DIGITAL_TWIN_PATH.write_text(json.dumps(_json_safe(state), separators=(",", ":")), encoding="utf-8")
    except OSError as exc:
        print(f"[WARN] Failed to save digital twin state: {exc}")
    return DIGITAL_TWIN_PATH


def observe_strategy_result(
    *,
    symbol: str,
    result: dict[str, Any],
    news_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record what each strategy saw this cycle so the twin can compare models over time."""

    state = load_digital_twin_state()
    now = datetime.now(UTC).isoformat()
    state["last_observed_at"] = now
    state["last_symbol"] = symbol.upper()

    news_lock = bool((news_context or {}).get("news_lock", {}).get("locked"))
    pair_news_bias = str((news_context or {}).get("pair_news_bias", "neutral")).upper()

    for strategy_name, strategy_payload in _iter_strategy_payloads(result):
        stats = state["strategy_stats"].setdefault(strategy_name, _default_strategy_stats())
        stats["observations"] += 1
        stats["last_seen"] = now
        stats["last_symbol"] = symbol.upper()
        if news_lock:
            stats["news_locked_observations"] += 1

        status = str(strategy_payload.get("status") or "").upper()
        if status == "VALID_TRADE":
            stats["valid_signals"] += 1
        else:
            stats["rejected_signals"] += 1
            stalker = strategy_payload.get("stalker") or strategy_payload.get("details", {}).get("stalker") or {}
            if str(stalker.get("state") or "") == "near_valid":
                stats["near_valid"] += 1

        lifecycle = str(strategy_payload.get("lifecycle") or strategy_payload.get("details", {}).get("lifecycle", {}).get("state") or "")
        if lifecycle:
            stats["last_lifecycle"] = lifecycle

        last_missing = strategy_payload.get("missing", []) or []
        stats["last_missing"] = last_missing[:6]
        stats["last_news_bias"] = pair_news_bias

    state["news_overview"] = {
        "pair_news_bias": pair_news_bias,
        "news_lock_active": news_lock,
        "events": _json_safe((news_context or {}).get("news_lock", {}).get("events", [])[:3]),
    }
    save_digital_twin_state(state)
    return state


def register_virtual_trade(
    *,
    symbol: str,
    setup: dict[str, Any],
    source: str,
    timeframe: str = "1h",
    analysis_snapshot: dict[str, Any] | None = None,
    news_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Mirror a valid setup into the twin tradebook so the system can self-evaluate live."""

    if setup.get("status") != "VALID_TRADE":
        return None

    state = load_digital_twin_state()
    signature = _build_trade_signature(symbol=symbol, setup=setup, timeframe=timeframe)
    if any(trade.get("signature") == signature and trade.get("status") == "OPEN" for trade in state["open_trades"]):
        return None

    entry = float(setup["entry"])
    stop_loss = float(setup["sl"])
    take_profit = float(setup["tp"])
    risk_distance = abs(entry - stop_loss)
    if risk_distance <= 0:
        return None

    account = state["account"]
    risk_amount = float(account.get("risk_per_trade", DEFAULT_RISK_PER_TRADE))
    position_units = risk_amount / risk_distance
    side = str(setup.get("bias") or "").upper()
    rr = float(setup.get("risk_reward_ratio") or 0.0)
    projected_profit = round(risk_amount * rr, 2)
    now = datetime.now(UTC).isoformat()
    strategies = list(setup.get("strategies") or [])
    primary_strategy = strategies[0] if strategies else "Unknown"

    trade = {
        "id": signature,
        "signature": signature,
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "source": source,
        "status": "OPEN",
        "opened_at": now,
        "bias": side,
        "entry": round(entry, 4),
        "stop_loss": round(stop_loss, 4),
        "take_profit": round(take_profit, 4),
        "risk_reward_ratio": rr,
        "risk_amount": round(risk_amount, 2),
        "projected_profit": projected_profit,
        "position_units": round(position_units, 4),
        "confidence": setup.get("confidence"),
        "confidence_score": int(setup.get("confidence_score") or 0),
        "tier": setup.get("tier"),
        "ranking_score": setup.get("ranking_score"),
        "strategies": strategies,
        "primary_strategy": primary_strategy,
        "confluences": list(setup.get("confluences") or []),
        "lifecycle": setup.get("lifecycle"),
        "entry_triggered": False,
        "triggered_at": None,
        "news_context": {
            "pair_news_bias": str((news_context or {}).get("pair_news_bias", "neutral")).upper(),
            "news_lock_active": bool((news_context or {}).get("news_lock", {}).get("locked")),
            "upcoming_events": _json_safe((news_context or {}).get("news_lock", {}).get("events", [])[:5]),
        },
        "analysis_snapshot": _json_safe(analysis_snapshot or {}),
        "unrealized_pnl": 0.0,
        "max_favorable_excursion": 0.0,
        "max_adverse_excursion": 0.0,
    }
    state["open_trades"].append(trade)
    state["account"]["open_positions"] = len(state["open_trades"])
    state["account"]["last_activity_at"] = now
    _update_strategy_open_count(state, primary_strategy, delta=1)
    save_digital_twin_state(state)
    return trade


def update_virtual_trade_outcomes(default_source: str = "auto") -> list[dict[str, Any]]:
    """Revalue and close open virtual trades as price reaches SL or TP."""

    state = load_digital_twin_state()
    _archive_legacy_open_trades(state)
    open_trades = list(state["open_trades"])
    updated_closed: list[dict[str, Any]] = []
    remaining_open: list[dict[str, Any]] = []

    for trade in open_trades:
        try:
            candles = fetch_ohlc(
                FetchConfig(
                    symbol=str(trade["symbol"]),
                    interval=_monitor_interval_for_trade(trade),
                    limit=6,
                    source=str(trade.get("source") or default_source),
                )
            )
        except Exception:
            remaining_open.append(trade)
            continue

        if candles.empty:
            remaining_open.append(trade)
            continue

        latest = candles.iloc[-1]
        current_close = float(latest["close"])
        _update_unrealized_metrics(trade, current_close)
        close_update = _resolve_virtual_trade(trade, latest)
        if close_update is None:
            remaining_open.append(trade)
            continue

        trade.update(close_update)
        if trade.get("status") in {"WIN", "LOSS"}:
            updated_closed.append(dict(trade))
            state["closed_trades"].append(dict(trade))
            _apply_account_close(state, trade)
            _update_strategy_after_close(state, trade)
        else:
            remaining_open.append(trade)

    state["open_trades"] = remaining_open
    state["account"]["open_positions"] = len(remaining_open)
    state["account"]["equity"] = round(
        float(state["account"]["balance"]) + sum(float(trade.get("unrealized_pnl") or 0.0) for trade in remaining_open),
        2,
    )
    state["account"]["peak_balance"] = max(float(state["account"].get("peak_balance") or DEFAULT_BALANCE), float(state["account"]["balance"]))
    peak_balance = max(float(state["account"]["peak_balance"]), 1e-9)
    state["account"]["drawdown_percent"] = round(
        max(0.0, ((peak_balance - float(state["account"]["equity"])) / peak_balance) * 100),
        2,
    )
    state["last_updated_at"] = datetime.now(UTC).isoformat()
    save_digital_twin_state(state)
    return updated_closed


def get_digital_twin_snapshot() -> dict[str, Any]:
    """Return a dashboard-friendly summary of the virtual account and strategy twin."""

    state = load_digital_twin_state()
    closed = list(state.get("closed_trades", []))
    wins = [trade for trade in closed if trade.get("status") == "WIN"]
    losses = [trade for trade in closed if trade.get("status") == "LOSS"]
    total_realized = round(sum(float(trade.get("realized_pnl") or 0.0) for trade in closed), 2)
    strategy_leaderboard = _strategy_leaderboard(state)
    return {
        "account": state.get("account", {}),
        "open_trades": state.get("open_trades", [])[:10],
        "closed_trades": list(reversed(closed[-10:])),
        "summary": {
            "closed_count": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round((len(wins) / len(closed)) * 100, 2) if closed else 0.0,
            "realized_pnl": total_realized,
            "strategy_leaderboard": strategy_leaderboard,
            "news_overview": state.get("news_overview", {}),
        },
        "strategy_stats": state.get("strategy_stats", {}),
        "last_updated_at": state.get("last_updated_at"),
    }


def _default_twin_state() -> dict[str, Any]:
    return {
        "account": {
            "initial_balance": DEFAULT_BALANCE,
            "balance": DEFAULT_BALANCE,
            "equity": DEFAULT_BALANCE,
            "risk_per_trade": DEFAULT_RISK_PER_TRADE,
            "open_positions": 0,
            "peak_balance": DEFAULT_BALANCE,
            "drawdown_percent": 0.0,
            "last_activity_at": None,
        },
        "open_trades": [],
        "closed_trades": [],
        "strategy_stats": {},
        "last_observed_at": None,
        "last_updated_at": None,
        "last_symbol": None,
        "news_overview": {"pair_news_bias": "NEUTRAL", "news_lock_active": False, "events": []},
    }


def _with_defaults(state: dict[str, Any]) -> dict[str, Any]:
    default = _default_twin_state()
    merged = {**default, **state}
    merged["account"] = {**default["account"], **state.get("account", {})}
    merged["strategy_stats"] = state.get("strategy_stats", {})
    merged["open_trades"] = state.get("open_trades", [])
    merged["closed_trades"] = state.get("closed_trades", [])
    merged["news_overview"] = {**default["news_overview"], **state.get("news_overview", {})}
    return merged


def _iter_strategy_payloads(result: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    strategy_results = result.get("strategy_results", {})
    mapping = {
        "Sweep Reversal": strategy_results.get("strict_liquidity"),
        "Trend Pullback Continuation": strategy_results.get("pullback"),
        "HTF Zone Reaction": strategy_results.get("htf_zone"),
    }
    return [(name, payload) for name, payload in mapping.items() if isinstance(payload, dict)]


def _default_strategy_stats() -> dict[str, Any]:
    return {
        "observations": 0,
        "valid_signals": 0,
        "rejected_signals": 0,
        "near_valid": 0,
        "virtual_trades": 0,
        "open_trades": 0,
        "wins": 0,
        "losses": 0,
        "realized_pnl": 0.0,
        "avg_rr": 0.0,
        "news_locked_observations": 0,
        "last_seen": None,
        "last_symbol": None,
        "last_lifecycle": None,
        "last_missing": [],
        "last_news_bias": "NEUTRAL",
    }


def _build_trade_signature(*, symbol: str, setup: dict[str, Any], timeframe: str) -> str:
    strategies = ",".join(setup.get("strategies", []))
    return "|".join(
        [
            symbol.upper(),
            timeframe,
            str(setup.get("bias") or ""),
            f"{float(setup.get('entry') or 0):.4f}",
            f"{float(setup.get('sl') or 0):.4f}",
            f"{float(setup.get('tp') or 0):.4f}",
            strategies,
        ]
    )


def _update_strategy_open_count(state: dict[str, Any], strategy_name: str, *, delta: int) -> None:
    stats = state["strategy_stats"].setdefault(strategy_name, _default_strategy_stats())
    stats["virtual_trades"] += max(delta, 0)
    stats["open_trades"] = max(0, int(stats.get("open_trades", 0)) + delta)


def _update_unrealized_metrics(trade: dict[str, Any], current_close: float) -> None:
    if not bool(trade.get("entry_triggered")):
        trade["unrealized_pnl"] = 0.0
        return
    entry = float(trade["entry"])
    units = float(trade.get("position_units") or 0.0)
    side = str(trade.get("bias") or "").upper()
    pnl = (current_close - entry) * units if side == "BUY" else (entry - current_close) * units
    trade["unrealized_pnl"] = round(pnl, 2)
    trade["max_favorable_excursion"] = round(max(float(trade.get("max_favorable_excursion") or 0.0), pnl), 2)
    trade["max_adverse_excursion"] = round(min(float(trade.get("max_adverse_excursion") or 0.0), pnl), 2)


def _resolve_virtual_trade(trade: dict[str, Any], latest_candle) -> dict[str, Any] | None:
    entry = float(trade["entry"])
    stop_loss = float(trade["stop_loss"])
    take_profit = float(trade["take_profit"])
    candle_low = float(latest_candle["low"])
    candle_high = float(latest_candle["high"])
    side = str(trade.get("bias") or "").upper()
    risk_amount = float(trade.get("risk_amount") or DEFAULT_RISK_PER_TRADE)
    reward_amount = float(trade.get("projected_profit") or 0.0)
    entry_triggered = bool(trade.get("entry_triggered"))
    entry_hit = candle_low <= entry <= candle_high

    updates: dict[str, Any] = {}
    if not entry_triggered:
        if not entry_hit:
            return None
        updates["entry_triggered"] = True
        updates["triggered_at"] = datetime.now(UTC).isoformat()
        return updates

    if side == "BUY":
        stop_hit = candle_low <= stop_loss
        target_hit = candle_high >= take_profit
    else:
        stop_hit = candle_high >= stop_loss
        target_hit = candle_low <= take_profit

    if not stop_hit and not target_hit:
        return updates or None

    if stop_hit:
        return {
            **updates,
            "status": "LOSS",
            "closed_at": datetime.now(UTC).isoformat(),
            "close_reason": "STOP_LOSS_HIT",
            "realized_pnl": round(-risk_amount, 2),
            "rr_achieved": -1.0,
            "unrealized_pnl": 0.0,
        }

    return {
        **updates,
        "status": "WIN",
        "closed_at": datetime.now(UTC).isoformat(),
        "close_reason": "TAKE_PROFIT_HIT",
        "realized_pnl": round(reward_amount, 2),
        "rr_achieved": round(float(trade.get("risk_reward_ratio") or 0.0), 2),
        "unrealized_pnl": 0.0,
    }


def _apply_account_close(state: dict[str, Any], trade: dict[str, Any]) -> None:
    account = state["account"]
    account["balance"] = round(float(account.get("balance") or DEFAULT_BALANCE) + float(trade.get("realized_pnl") or 0.0), 2)
    account["last_activity_at"] = trade.get("closed_at")


def _monitor_interval_for_trade(trade: dict[str, Any]) -> str:
    if is_live_strategy(trade.get("primary_strategy")):
        return "15m"
    return str(trade.get("timeframe") or "1h")


def _archive_legacy_open_trades(state: dict[str, Any]) -> None:
    archived_at = datetime.now(UTC).isoformat()
    remaining_open: list[dict[str, Any]] = []
    changed = False
    for trade in list(state.get("open_trades", [])):
        if is_live_strategy(trade.get("primary_strategy")):
            remaining_open.append(trade)
            continue
        archived = dict(trade)
        archived["status"] = "ARCHIVED"
        archived["closed_at"] = archived_at
        archived["close_reason"] = "RETIRED_STRATEGY"
        archived["realized_pnl"] = 0.0
        archived["rr_achieved"] = 0.0
        archived["unrealized_pnl"] = 0.0
        state["closed_trades"].append(archived)
        changed = True
    if changed:
        state["open_trades"] = remaining_open
        state["account"]["open_positions"] = len(remaining_open)


def _update_strategy_after_close(state: dict[str, Any], trade: dict[str, Any]) -> None:
    strategy_name = str(trade.get("primary_strategy") or "Unknown")
    stats = state["strategy_stats"].setdefault(strategy_name, _default_strategy_stats())
    stats["open_trades"] = max(0, int(stats.get("open_trades", 0)) - 1)
    if trade.get("status") == "WIN":
        stats["wins"] += 1
    elif trade.get("status") == "LOSS":
        stats["losses"] += 1
    stats["realized_pnl"] = round(float(stats.get("realized_pnl") or 0.0) + float(trade.get("realized_pnl") or 0.0), 2)
    closed_count = max(1, stats["wins"] + stats["losses"])
    previous_total_rr = float(stats.get("avg_rr") or 0.0) * max(0, closed_count - 1)
    stats["avg_rr"] = round((previous_total_rr + float(trade.get("rr_achieved") or 0.0)) / closed_count, 2)


def _strategy_leaderboard(state: dict[str, Any]) -> list[dict[str, Any]]:
    leaderboard = []
    for name, stats in state.get("strategy_stats", {}).items():
        closed = int(stats.get("wins", 0)) + int(stats.get("losses", 0))
        win_rate = (int(stats.get("wins", 0)) / closed) * 100 if closed else 0.0
        leaderboard.append(
            {
                "strategy": name,
                "observations": stats.get("observations", 0),
                "valid_signals": stats.get("valid_signals", 0),
                "virtual_trades": stats.get("virtual_trades", 0),
                "open_trades": stats.get("open_trades", 0),
                "wins": stats.get("wins", 0),
                "losses": stats.get("losses", 0),
                "win_rate": round(win_rate, 2),
                "realized_pnl": round(float(stats.get("realized_pnl") or 0.0), 2),
                "avg_rr": round(float(stats.get("avg_rr") or 0.0), 2),
                "near_valid": stats.get("near_valid", 0),
                "news_locked_observations": stats.get("news_locked_observations", 0),
            }
        )
    leaderboard.sort(key=lambda item: (-float(item.get("realized_pnl", 0.0)), -float(item.get("win_rate", 0.0)), item["strategy"]))
    return leaderboard


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    if hasattr(value, "__dict__"):
        try:
            return _json_safe(vars(value))
        except Exception:
            return str(value)
    return str(value)

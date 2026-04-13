from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pandas as pd
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from trading_bot.core.alerts_store import load_alerts
from trading_bot.core.backtester import backtest_symbol
from trading_bot.core.calibration_mode import apply_calibration, build_calibration_snapshot, get_recent_calibration_history
from trading_bot.core.confluence_engine import evaluate_symbol
from trading_bot.core.data_fetcher import DataFetchError, FetchConfig, fetch_ohlc
from trading_bot.core.digital_twin import get_digital_twin_snapshot
from trading_bot.core.edge_control import build_edge_control_snapshot, evaluate_edge_control
from trading_bot.core.fractal_engine import analyze_fractal_context
from trading_bot.core.instrument_universe import APPROVED_SYMBOLS, get_instrument_universe, is_supported_symbol
from trading_bot.core.journal import ensure_trade_logged, find_trade_by_signature, get_recent_journal, log_rejected_analysis, summarize_journal
from trading_bot.core.monitor_state import build_scanner_health_snapshot, load_monitor_state
from trading_bot.core.news_engine import (
    JsonEconomicCalendarProvider,
    build_news_alerts,
    derive_news_bias,
    fetch_market_moving_events,
    get_pair_news_bias,
    get_news_lock,
    load_symbol_news_context,
    rank_events_for_symbol,
    split_symbol_currencies,
)
from trading_bot.core.performance_tracker import build_performance_snapshot
from trading_bot.core.strategy_registry import PRIMARY_STRATEGY, strategy_result_key
from trading_bot.core.validation_mode import build_validation_snapshot
from trading_bot.core.weekly_outlook_job import run_weekly_outlook_job
from trading_bot.core.weekly_outlook_report import WEEKLY_OUTLOOK_DIR


DataSource = Literal["auto", "binance", "mock", "yfinance", "oanda", "alphavantage", "twelvedata", "stooq"]
BASE_DIR = Path(__file__).resolve().parents[2]
FRONTEND_DIR = BASE_DIR / "frontend"
ECONOMIC_CALENDAR_PATH = BASE_DIR / "trading_bot" / "data" / "economic_calendar.json"
_CACHE: dict[str, tuple[float, object]] = {}
DATA_TTL_SECONDS = 2.0
PERFORMANCE_TTL_SECONDS = 4.0
JOURNAL_TTL_SECONDS = 3.0
WATCHLIST_TTL_SECONDS = 20.0
NEWS_TTL_SECONDS = 60.0
SCAN_REPORT_TTL_SECONDS = 45.0
BROADER_SCAN_SYMBOLS = [
    "EURCHF",
    "EURJPY",
    "AUDCHF",
    "AUDNZD",
    "NZDCHF",
    "CADCHF",
    "CHFJPY",
    "GBPNZD",
    "USDCAD",
    "AUDCAD",
]


app = FastAPI(
    title="Trading Intelligence Platform",
    version="1.0.0",
    description="Strict multi-strategy intelligent trading platform with a resilient dashboard.",
)
app.mount("/frontend", StaticFiles(directory=FRONTEND_DIR), name="frontend")


@app.get("/")
def root() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/data")
def get_data(
    symbol: str = Query(default="EURUSD"),
    interval: Literal["15m", "30m", "1h"] = Query(default="1h"),
    source: DataSource = Query(default="auto"),
    lite: bool = Query(default=False),
) -> dict:
    symbol = symbol.upper()
    if not is_supported_symbol(symbol):
        return _error_payload(
            symbol=symbol,
            source=source,
            interval=_normalize_execution_interval(interval),
            message="Unsupported symbol. Use only approved watchlist symbols such as ETHUSDT, BTCUSDT, XAUUSD, NAS100, EURUSD, GBPUSD, AUDUSD, NZDUSD, USDCHF, USDJPY, AUDJPY, GBPJPY.",
        )

    normalized_interval = _normalize_execution_interval(interval)
    cache_key = f"data|{symbol}|{normalized_interval}|{source}"
    cached = _cache_get(cache_key, DATA_TTL_SECONDS)
    if cached is not None:
        return cached

    try:
        weekly_data = fetch_ohlc(FetchConfig(symbol=symbol, interval="1w", limit=160, source=source))
        daily_data = fetch_ohlc(FetchConfig(symbol=symbol, interval="1d", limit=220, source=source))
        h4_data = fetch_ohlc(FetchConfig(symbol=symbol, interval="4h", limit=220, source=source))
        h1_data = fetch_ohlc(FetchConfig(symbol=symbol, interval="1h", limit=320, source=source))
        ltf_data = fetch_ohlc(FetchConfig(symbol=symbol, interval="15m", limit=320, source=source)) if normalized_interval == "15m" else None

        setup = evaluate_symbol(symbol=symbol, weekly_data=weekly_data, daily_data=daily_data, h1_data=h1_data, ltf_data=ltf_data, h4_data=h4_data)
        setup = _apply_news_lock_to_setup(symbol, setup)
        setup = _apply_edge_control_to_setup(setup)
        latest_price = round(float((ltf_data if ltf_data is not None else h1_data).iloc[-1]["close"]), 4)
        payload = _build_dashboard_payload(
            symbol=symbol,
            source=source,
            interval=normalized_interval,
            setup=setup,
            weekly_data=weekly_data,
            daily_data=daily_data,
            h4_data=h4_data,
            h1_data=h1_data,
            ltf_data=ltf_data,
            latest_price=latest_price,
            include_extras=not lite,
            record_side_effects=not lite,
        )
    except DataFetchError as exc:
        payload = _error_payload(symbol=symbol, source=source, interval=normalized_interval, message=str(exc))
    except Exception as exc:
        payload = _error_payload(symbol=symbol, source=source, interval=normalized_interval, message=f"Error fetching data: {exc}")

    _cache_set(cache_key, payload)
    return payload


@app.get("/status")
def status() -> dict:
    monitor_state = load_monitor_state()
    scanner_health = build_scanner_health_snapshot(monitor_state)
    digital_twin = get_digital_twin_snapshot()
    edge_control = build_edge_control_snapshot()
    validation = build_validation_snapshot()
    calibration = build_calibration_snapshot()
    return {
        "status": "ok",
        "message": "System running",
        "timestamp": datetime.now(UTC).isoformat(),
        "monitor": monitor_state,
        "scanner_health": scanner_health,
        "digital_twin": digital_twin,
        "edge_control": edge_control,
        "validation": validation,
        "calibration": calibration,
    }


@app.get("/journal")
def journal(
    limit: int = Query(default=25, ge=1, le=200),
    pair: str | None = Query(default=None),
    result: str | None = Query(default=None),
    month: str | None = Query(default=None),
    quality: str | None = Query(default=None),
) -> dict:
    cache_key = f"journal|{limit}|{pair}|{result}|{month}|{quality}"
    cached = _cache_get(cache_key, JOURNAL_TTL_SECONDS)
    if cached is not None:
        return cached
    payload = {
        "entries": get_recent_journal(limit=limit, pair=pair, result=result, month=month, quality=quality),
        "summary": summarize_journal(pair=pair, result=result, month=month, quality=quality),
        "filters": {"pair": pair, "result": result, "month": month, "quality": quality},
    }
    _cache_set(cache_key, payload)
    return payload


@app.get("/alerts")
def alerts(limit: int = Query(default=25, ge=1, le=200)) -> dict:
    return {"entries": list(reversed(load_alerts(limit=limit)))}


@app.get("/edge-control")
def edge_control() -> dict:
    return build_edge_control_snapshot()


@app.get("/validation")
def validation() -> dict:
    return build_validation_snapshot()


@app.get("/calibration")
def calibration() -> dict:
    return build_calibration_snapshot()


@app.post("/calibration/apply")
def calibration_apply() -> dict:
    return apply_calibration()


@app.get("/calibration/history")
def calibration_history(limit: int = Query(default=10, ge=1, le=100)) -> dict:
    return {"entries": get_recent_calibration_history(limit=limit)}


@app.get("/performance")
def performance() -> dict:
    cache_key = "performance"
    cached = _cache_get(cache_key, PERFORMANCE_TTL_SECONDS)
    if cached is not None:
        return cached
    payload = build_performance_snapshot()
    _cache_set(cache_key, payload)
    return payload


@app.get("/digital-twin")
def digital_twin() -> dict:
    return get_digital_twin_snapshot()


@app.get("/fractal")
def fractal(symbol: str = Query(default="BTCUSDT"), source: DataSource = Query(default="auto")) -> dict:
    symbol = symbol.upper()
    if not is_supported_symbol(symbol):
        return {"status": "ERROR", "message": "Unsupported symbol for fractal scan.", "symbol": symbol}
    try:
        daily_data = fetch_ohlc(FetchConfig(symbol=symbol, interval="1d", limit=260, source=source))
        h4_data = fetch_ohlc(FetchConfig(symbol=symbol, interval="4h", limit=320, source=source))
        return analyze_fractal_context(symbol=symbol, daily_data=daily_data, h4_data=h4_data)
    except Exception as exc:
        return {"status": "ERROR", "message": str(exc), "symbol": symbol}


@app.get("/watchlist")
def watchlist(source: DataSource = Query(default="auto"), fast: bool = Query(default=False)) -> dict:
    cache_key = f"watchlist|{source}|{fast}"
    cached = _cache_get(cache_key, WATCHLIST_TTL_SECONDS)
    if cached is not None:
        return cached

    weekly_limit, daily_limit, h4_limit, h1_limit, ltf_limit = _scan_limits(fast)
    items: list[dict] = []
    for symbol in APPROVED_SYMBOLS:
        item_source = _default_source_for_symbol(symbol, source)
        try:
            weekly_data = fetch_ohlc(FetchConfig(symbol=symbol, interval="1w", limit=weekly_limit, source=item_source))
            daily_data = fetch_ohlc(FetchConfig(symbol=symbol, interval="1d", limit=daily_limit, source=item_source))
            h4_data = fetch_ohlc(FetchConfig(symbol=symbol, interval="4h", limit=h4_limit, source=item_source))
            h1_data = fetch_ohlc(FetchConfig(symbol=symbol, interval="1h", limit=h1_limit, source=item_source))
            ltf_data = None if fast else fetch_ohlc(FetchConfig(symbol=symbol, interval="15m", limit=ltf_limit, source=item_source))
            setup = evaluate_symbol(symbol=symbol, weekly_data=weekly_data, daily_data=daily_data, h1_data=h1_data, ltf_data=ltf_data, h4_data=h4_data)
            setup = _apply_news_lock_to_setup(symbol, setup)
            items.append(
                {
                    "symbol": symbol,
                    "source": item_source,
                    "status": setup.get("status", "ERROR"),
                    "bias": setup.get("bias") or setup.get("final_bias") or "NEUTRAL",
                    "daily_bias": str(setup.get("daily_bias", "UNKNOWN")).upper(),
                    "h1_bias": str(setup.get("h1_bias", "UNKNOWN")).upper(),
                    "confidence": setup.get("confidence", "LOW"),
                    "confidence_score": setup.get("confidence_score", 0),
                    "risk_reward_ratio": setup.get("risk_reward_ratio"),
                    "lifecycle": setup.get("lifecycle"),
                    "stalker": setup.get("stalker"),
                    "entry": setup.get("entry"),
                    "sl": setup.get("sl"),
                    "tp": setup.get("tp"),
                    "latest_price": round(float(h1_data.iloc[-1]["close"]), 4),
                    "message": setup.get("message", ""),
                    "confluences": setup.get("confluences", []),
                    "analysis_context": _build_analysis_context(setup),
                }
            )
        except Exception as exc:
            items.append(
                {
                    "symbol": symbol,
                    "source": item_source,
                    "status": "ERROR",
                    "bias": "NEUTRAL",
                    "daily_bias": "UNKNOWN",
                    "h1_bias": "UNKNOWN",
                    "confidence": "LOW",
                    "confidence_score": 0,
                    "risk_reward_ratio": None,
                    "lifecycle": "unknown",
                    "stalker": None,
                    "entry": None,
                    "sl": None,
                    "tp": None,
                    "latest_price": None,
                    "message": str(exc),
                    "confluences": [],
                    "analysis_context": {},
                }
            )

    ranked_items = _rank_watchlist_items(items)
    payload = {"symbols": ranked_items, "timestamp": datetime.now(UTC).isoformat()}
    _cache_set(cache_key, payload)
    return payload


@app.get("/scan-report")
def scan_report(source: DataSource = Query(default="auto"), broader: bool = Query(default=False), fast: bool = Query(default=False)) -> dict:
    cache_key = f"scan_report|{source}|{broader}|{fast}"
    cached = _cache_get(cache_key, SCAN_REPORT_TTL_SECONDS)
    if cached is not None:
        return cached

    symbols = APPROVED_SYMBOLS if not broader else list(dict.fromkeys(APPROVED_SYMBOLS + BROADER_SCAN_SYMBOLS))
    weekly_limit, daily_limit, h4_limit, h1_limit, ltf_limit = _scan_limits(fast)
    items: list[dict] = []
    for symbol in symbols:
        item_source = _default_source_for_symbol(symbol, source)
        try:
            weekly_data = fetch_ohlc(FetchConfig(symbol=symbol, interval="1w", limit=weekly_limit, source=item_source))
            daily_data = fetch_ohlc(FetchConfig(symbol=symbol, interval="1d", limit=daily_limit, source=item_source))
            h4_data = fetch_ohlc(FetchConfig(symbol=symbol, interval="4h", limit=h4_limit, source=item_source))
            h1_data = fetch_ohlc(FetchConfig(symbol=symbol, interval="1h", limit=h1_limit, source=item_source))
            ltf_data = None if fast else fetch_ohlc(FetchConfig(symbol=symbol, interval="15m", limit=ltf_limit, source=item_source))
            setup = evaluate_symbol(symbol=symbol, weekly_data=weekly_data, daily_data=daily_data, h1_data=h1_data, ltf_data=ltf_data, h4_data=h4_data)
            setup = _apply_news_lock_to_setup(symbol, setup)
            items.append(
                {
                    "symbol": symbol,
                    "source": item_source,
                    "status": setup.get("status", "ERROR"),
                    "bias": setup.get("bias") or setup.get("daily_bias") or "NEUTRAL",
                    "daily_bias": str(setup.get("daily_bias", "UNKNOWN")).upper(),
                    "h1_bias": str(setup.get("h1_bias", "UNKNOWN")).upper(),
                    "confidence": setup.get("confidence", "LOW"),
                    "confidence_score": setup.get("confidence_score", 0),
                    "risk_reward_ratio": setup.get("risk_reward_ratio"),
                    "lifecycle": setup.get("lifecycle"),
                    "stalker": setup.get("stalker"),
                    "entry": setup.get("entry"),
                    "sl": setup.get("sl"),
                    "tp": setup.get("tp"),
                    "latest_price": round(float(h1_data.iloc[-1]["close"]), 4),
                    "message": setup.get("message", ""),
                    "missing": setup.get("missing", []),
                    "strategies": setup.get("strategies", []),
                    "confluences": setup.get("confluences", []),
                    "analysis_context": _build_analysis_context(setup),
                }
            )
        except Exception as exc:
            items.append(
                {
                    "symbol": symbol,
                    "source": item_source,
                    "status": "ERROR",
                    "message": str(exc),
                    "daily_bias": "UNKNOWN",
                    "h1_bias": "UNKNOWN",
                    "stalker": None,
                    "risk_reward_ratio": None,
                    "analysis_context": {},
                }
            )

    ranked_items = _rank_watchlist_items(items)
    valid = [item for item in ranked_items if item.get("status") == "VALID_TRADE"]
    near_valid = [item for item in ranked_items if item.get("status") != "VALID_TRADE" and str((item.get("stalker") or {}).get("state")) == "near_valid"]
    developing = [item for item in ranked_items if item.get("status") != "VALID_TRADE" and str((item.get("stalker") or {}).get("state")) == "developing"]
    payload = {
        "timestamp": datetime.now(UTC).isoformat(),
        "broader": broader,
        "symbols": ranked_items,
        "valid": valid[:8],
        "near_valid": near_valid[:10],
        "developing": developing[:10],
    }
    _cache_set(cache_key, payload)
    return payload


@app.get("/news")
def news(symbol: str = Query(default="EURUSD")) -> dict:
    symbol = symbol.upper()
    if not is_supported_symbol(symbol):
        return {"configured": False, "events": [], "message": "Unsupported symbol."}

    cache_key = f"news|{symbol}"
    cached = _cache_get(cache_key, NEWS_TTL_SECONDS)
    if cached is not None:
        return cached

    live_context = load_symbol_news_context(symbol, calendar_path=ECONOMIC_CALENDAR_PATH)
    if not live_context.get("configured"):
        payload = {
            **live_context,
            "symbol": symbol,
            "message": "No scheduled calendar provider configured. Live headlines may still be available.",
            "alerts": {"upcoming": [], "released": [], "sudden": []},
        }
        _cache_set(cache_key, payload)
        return payload

    events = live_context.get("events", [])
    currencies = list(split_symbol_currencies(symbol))
    bias_by_currency = derive_news_bias(currencies=currencies, events=events)
    pair_bias = get_pair_news_bias(symbol, bias_by_currency)
    upcoming, released, sudden = build_news_alerts(symbol=symbol, events=events)
    ranked_events = rank_events_for_symbol(symbol, events)
    news_lock = get_news_lock(symbol, events)

    payload = {
        "configured": True,
        "symbol": symbol,
        "provider": live_context.get("provider", "unknown"),
        "pair_news_bias": pair_bias.upper(),
        "currencies": currencies,
        "events": ranked_events[:12],
        "alerts": {
            "upcoming": upcoming,
            "released": released,
            "sudden": sudden,
        },
        "news_lock": news_lock,
        "headlines": live_context.get("headlines", []),
        "message": "Pair-specific news loaded." if events else "No relevant market-moving news in range.",
    }
    _cache_set(cache_key, payload)
    return payload


@app.get("/backtest")
def backtest(
    symbol: str = Query(default="EURUSD"),
    source: DataSource = Query(default="auto"),
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
) -> dict:
    symbol = symbol.upper()
    if not is_supported_symbol(symbol):
        return {
            "status": "ERROR",
            "message": "Unsupported symbol. Use only approved watchlist symbols such as ETHUSDT, BTCUSDT, XAUUSD, NAS100, EURUSD, GBPUSD, AUDUSD, NZDUSD, USDCHF, USDJPY, AUDJPY, GBPJPY.",
            "symbol": symbol,
        }

    try:
        daily_data = fetch_ohlc(FetchConfig(symbol=symbol, interval="1d", limit=260, source=source))
        h1_data = fetch_ohlc(FetchConfig(symbol=symbol, interval="1h", limit=420, source=source))
        ltf_data = fetch_ohlc(FetchConfig(symbol=symbol, interval="15m", limit=640, source=source))
        return backtest_symbol(
            symbol=symbol,
            daily_data=daily_data,
            h1_data=h1_data,
            ltf_data=ltf_data,
            start_date=start_date,
            end_date=end_date,
        )
    except Exception as exc:
        return {"status": "ERROR", "message": str(exc), "symbol": symbol.upper()}


@app.get("/weekly-outlook/run")
def weekly_outlook_run(
    source: DataSource = Query(default="auto"),
    timezone: str = Query(default="Europe/Vienna"),
) -> dict:
    try:
        result = run_weekly_outlook_job(
            symbols=get_instrument_universe("forex"),
            source=source,
            timezone_name=timezone,
        )
        return {
            "status": "ok",
            "saved_paths": result["saved_paths"],
            "report": result["report"],
        }
    except Exception as exc:
        return {"status": "ERROR", "message": str(exc)}


@app.get("/weekly-outlook/latest")
def weekly_outlook_latest() -> dict:
    latest_path = WEEKLY_OUTLOOK_DIR / "weekly_outlook_latest.json"
    if not latest_path.exists():
        return {"status": "NO_REPORT", "message": "No weekly outlook has been generated yet."}
    try:
        return json.loads(latest_path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return {"status": "ERROR", "message": f"Failed to load latest weekly outlook: {exc}"}


def _build_dashboard_payload(
    *,
    symbol: str,
    source: str,
    interval: str,
    setup: dict,
    weekly_data,
    daily_data,
    h4_data,
    h1_data,
    ltf_data,
    latest_price: float,
    include_extras: bool = True,
    record_side_effects: bool = True,
) -> dict:
    performance = build_performance_snapshot() if include_extras else {}
    digital_twin = get_digital_twin_snapshot() if include_extras else {}
    edge_control = build_edge_control_snapshot() if include_extras else {}
    validation = build_validation_snapshot() if include_extras else {}
    calibration = build_calibration_snapshot() if include_extras else {}
    journal_entries = get_recent_journal(limit=12) if include_extras else []
    alerts = list(reversed(load_alerts(limit=12))) if include_extras else []
    daily_bias = setup.get("daily_bias", "neutral")
    h1_bias = setup.get("h1_bias", "neutral")
    lifecycle = _resolve_display_lifecycle(symbol=symbol, setup=setup)
    fractal_context = analyze_fractal_context(symbol=symbol, daily_data=daily_data, h4_data=h4_data)

    if record_side_effects and setup["status"] == "VALID_TRADE":
        ensure_trade_logged(
            symbol=symbol,
            strategy="+".join(setup.get("strategies", [])),
            entry=float(setup["entry"]),
            stop_loss=float(setup["sl"]),
            take_profit=float(setup["tp"]),
            confluences=setup.get("confluences", []),
            confidence=int(setup.get("confidence_score", 0)),
            timeframe="1h",
            source=source,
            timeframes_used=["1d", "1h", "15m"] if ltf_data is not None else ["1d", "1h"],
            profit_factor=performance.get("profit_factor"),
            analysis_snapshot={
                "analysis_context": _build_analysis_context(setup),
                "chart_overlays": _build_chart_overlays(setup),
                "recent_candles": _serialize_recent_candles(ltf_data if ltf_data is not None else h1_data),
                "symbol": symbol,
                "source": source,
                "interval": interval,
            },
        )
    elif record_side_effects:
        log_rejected_analysis(
            symbol=symbol,
            strategy=str(setup.get("strategy") or "+".join(setup.get("strategies_checked") or setup.get("strategies") or []) or PRIMARY_STRATEGY),
            missing=setup.get("missing", []),
            timeframe="1h",
            source=source,
            message=setup.get("message", "No valid setup available"),
        )

    return {
        "status": setup["status"],
        "message": setup.get("message", "System running"),
        "symbol": symbol.upper(),
        "source": source,
        "interval": interval,
        "latest_price": latest_price,
        "daily_bias": str(daily_bias).upper(),
        "h1_bias": str(h1_bias).upper(),
        "final_bias": setup.get("bias", "NEUTRAL"),
        "confidence": setup.get("confidence", "LOW"),
        "confidence_score": setup.get("confidence_score", 0),
        "risk_reward_ratio": setup.get("risk_reward_ratio"),
        "lifecycle": lifecycle,
        "stalker": setup.get("stalker"),
        "entry": setup.get("entry"),
        "sl": setup.get("sl"),
        "tp": setup.get("tp"),
        "strategies": setup.get("strategies", []),
        "confluences": setup.get("confluences", []),
        "missing": setup.get("missing", []),
        "alerts": alerts,
        "journal": journal_entries,
        "performance": performance,
        "digital_twin": digital_twin,
        "edge_control": edge_control,
        "edge_control_decision": setup.get("edge_control", {}),
        "validation": validation,
        "calibration": calibration,
        "fractal": fractal_context,
        "setup_details": setup.get("strategy_results", {}),
        "analysis_context": _build_analysis_context(setup),
        "chart_overlays": _build_chart_overlays(setup),
        "timeframes": {
            "weekly": {"bias": str(detect_market_structure(weekly_data.tail(40).reset_index(drop=True)).get("trend", "unknown")).upper(), "latest_price": round(float(weekly_data.iloc[-1]["close"]), 4)},
            "daily": {"bias": str(daily_bias).upper(), "latest_price": round(float(daily_data.iloc[-1]["close"]), 4)},
            "h4": {"bias": str(detect_market_structure(h4_data.tail(120).reset_index(drop=True)).get("trend", "unknown")).upper(), "latest_price": round(float(h4_data.iloc[-1]["close"]), 4)},
            "h1": {"bias": str(h1_bias).upper(), "latest_price": round(float(h1_data.iloc[-1]["close"]), 4)},
            "ltf": {
                "bias": str(detect_market_structure(ltf_data.tail(120).reset_index(drop=True)).get("trend", "unused")).upper() if ltf_data is not None else "UNUSED",
                "latest_price": round(float(ltf_data.iloc[-1]["close"]), 4) if ltf_data is not None else None,
                "used": ltf_data is not None,
                "timeframe": "15m",
            },
        },
        "timestamp": setup.get("timestamp") or datetime.now(UTC).isoformat(),
    }


def _build_analysis_context(setup: dict) -> dict:
    details = _selected_strategy_details(setup)
    return {
        "daily_bias": setup.get("daily_bias"),
        "weekly_structure": None,
        "h4_structure": None,
        "h1_structure": details.get("h1_structure"),
        "m15_confirmation": details.get("m15_confirmation"),
        "liquidity": details.get("liquidity"),
        "sweep": details.get("sweep"),
        "entry_model": details.get("entry_model"),
        "target": details.get("target"),
        "session": details.get("session"),
        "lifecycle": setup.get("lifecycle"),
        "checklist": [],
        "stalker": None,
        "news": {},
    }


def _build_chart_overlays(setup: dict) -> dict:
    details = _selected_strategy_details(setup)
    return {
        "order_block": details.get("order_block"),
        "fvg": None,
        "inducement": None,
        "daily_order_block": None,
        "reaction": None,
        "choch": None,
        "idm": None,
        "poi": None,
        "liquidity": details.get("liquidity"),
        "sweep": details.get("sweep"),
        "m15_confirmation": details.get("m15_confirmation"),
        "trade_levels": {
            "entry": setup.get("entry"),
            "sl": setup.get("sl"),
            "tp": setup.get("tp"),
        },
    }


def _selected_strategy_details(setup: dict) -> dict:
    strategy_name = str(setup.get("strategy") or "")
    result_key = strategy_result_key(strategy_name)
    strategy_results = setup.get("strategy_results", {}) or {}
    selected = strategy_results.get(result_key or "", {}) if result_key else {}
    if not isinstance(selected, dict):
        selected = {}
    return selected.get("details") or {}


def _serialize_recent_candles(data) -> list[dict]:
    rows = []
    for _, candle in data.tail(60).iterrows():
        rows.append(
            {
                "time": pd.Timestamp(candle["time"]).isoformat(),
                "open": round(float(candle["open"]), 4),
                "high": round(float(candle["high"]), 4),
                "low": round(float(candle["low"]), 4),
                "close": round(float(candle["close"]), 4),
            }
        )
    return rows


def _error_payload(*, symbol: str, source: str, interval: str, message: str) -> dict:
    return {
        "status": "ERROR",
        "message": f"Error fetching data: {message}",
        "symbol": symbol.upper(),
        "source": source,
        "interval": interval,
        "latest_price": None,
        "daily_bias": "UNKNOWN",
        "h1_bias": "UNKNOWN",
        "final_bias": "NEUTRAL",
        "confidence": "LOW",
        "confidence_score": 0,
        "risk_reward_ratio": None,
        "lifecycle": "unknown",
        "stalker": None,
        "entry": None,
        "sl": None,
        "tp": None,
        "strategies": [],
        "confluences": [],
        "missing": [],
        "alerts": [],
        "journal": [],
        "performance": {},
        "digital_twin": get_digital_twin_snapshot(),
        "edge_control": build_edge_control_snapshot(),
        "edge_control_decision": {},
        "validation": build_validation_snapshot(),
        "calibration": build_calibration_snapshot(),
        "setup_details": {},
        "analysis_context": {},
        "chart_overlays": {},
        "timeframes": {},
        "timestamp": datetime.now(UTC).isoformat(),
    }


def _apply_news_lock_to_setup(symbol: str, setup: dict) -> dict:
    news_context = load_symbol_news_context(symbol, calendar_path=ECONOMIC_CALENDAR_PATH)
    if not setup.get("strategy_results"):
        setup["strategy_results"] = {}
    setup["strategy_results"]["news"] = news_context

    if setup.get("status") == "VALID_TRADE" and news_context.get("news_lock", {}).get("locked"):
        missing = list(setup.get("missing", []))
        if "News lock" not in missing:
            missing.append("News lock")
        return {
            **setup,
            "status": "NO TRADE",
            "message": "No valid setup available",
            "missing": missing,
        }
    return setup


def _apply_edge_control_to_setup(setup: dict) -> dict:
    snapshot = build_edge_control_snapshot()
    decision = evaluate_edge_control(setup, snapshot=snapshot)
    if not setup.get("strategy_results"):
        setup["strategy_results"] = {}
    setup["strategy_results"]["edge_control"] = decision
    setup["edge_control"] = decision

    if setup.get("status") == "VALID_TRADE" and not decision.get("allowed", True):
        missing = list(setup.get("missing", []))
        for reason in decision.get("reasons", []):
            if reason not in missing:
                missing.append(reason)
        return {
            **setup,
            "status": "NO TRADE",
            "message": "Blocked by edge control",
            "reason": "Blocked by edge control",
            "missing": missing,
            "edge_control": decision,
        }
    return setup


def _resolve_display_lifecycle(symbol: str, setup: dict) -> str:
    base_lifecycle = str(setup.get("lifecycle") or "forming")
    if setup.get("status") != "VALID_TRADE":
        return base_lifecycle

    strategy_name = "+".join(setup.get("strategies", []))
    signature = "|".join([symbol.upper(), strategy_name, "1h", f"{float(setup.get('entry') or 0):.4f}"])
    journal_entry = find_trade_by_signature(signature)
    if not journal_entry:
        return base_lifecycle
    if journal_entry.get("status") == "OPEN":
        return "active"
    if journal_entry.get("status") in {"WIN", "LOSS"}:
        return "closed"
    return base_lifecycle


def _cache_get(key: str, ttl_seconds: float):
    cached = _CACHE.get(key)
    if cached is None:
        return None
    created_at, value = cached
    if (time.monotonic() - created_at) > ttl_seconds:
        _CACHE.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: object) -> None:
    _CACHE[key] = (time.monotonic(), value)


def _normalize_execution_interval(interval: str) -> str:
    if interval == "30m":
        return "15m"
    return interval


def _default_source_for_symbol(symbol: str, requested_source: str) -> str:
    if requested_source != "auto":
        return requested_source
    if symbol.endswith("USDT"):
        return "binance"
    return "yfinance"


def _scan_limits(fast: bool) -> tuple[int, int, int, int, int]:
    if fast:
        return 60, 90, 90, 120, 0
    return 160, 220, 220, 320, 320


def _rank_watchlist_items(items: list[dict]) -> list[dict]:
    scored = []
    for item in items:
        score = _watchlist_score(item)
        tier = _watchlist_tier(item, score)
        scored.append(
            {
                **item,
                "ranking_score": round(score, 2),
                "tier": tier,
            }
        )

    scored.sort(
        key=lambda item: (
            0 if item["tier"] == "A-tier" else 1 if item["tier"] == "B-tier" else 2,
            -float(item.get("ranking_score", 0)),
            item.get("symbol", ""),
        )
    )

    for index, item in enumerate(scored, start=1):
        item["rank"] = index
    return scored


def _watchlist_score(item: dict) -> float:
    score = 0.0
    analysis = item.get("analysis_context") or {}
    if item.get("status") == "VALID_TRADE":
        score += 100
    elif item.get("status") in {"NO_TRADE", "WAIT_CONFIRMATION"}:
        score += 28
        stalker = item.get("stalker") or {}
        score += float(stalker.get("score") or 0) * 0.5

    if str(item.get("daily_bias", "")).lower() == str(item.get("h1_bias", "")).lower():
        score += 20

    confidence_score = float(item.get("confidence_score") or 0)
    score += confidence_score * 0.5

    rr = float(item.get("risk_reward_ratio") or 0)
    score += rr * 8

    confidence_label = str(item.get("confidence", "")).upper()
    if confidence_label == "HIGH":
        score += 12
    elif confidence_label == "LOW":
        score += 4

    if item.get("entry") is not None:
        latest_price = item.get("latest_price")
        entry = item.get("entry")
        if latest_price not in {None, 0} and entry not in {None, 0}:
            distance_ratio = abs(float(latest_price) - float(entry)) / max(abs(float(entry)), 1e-9)
            score += max(0.0, 12 - (distance_ratio * 2000))

    checklist = analysis.get("checklist") or []
    if checklist:
        confirmed = sum(1 for item in checklist if item.get("ok"))
        score += confirmed * 1.5

    lifecycle_state = str(item.get("lifecycle") or "").lower()
    if lifecycle_state == "zone_watch":
        score += 10
    elif lifecycle_state == "entry_reached":
        score += 12
    elif lifecycle_state == "forming":
        score += 4

    regime = str((analysis.get("regime") or {}).get("regime") or "")
    if regime == "trend_day":
        score += 10
    elif regime == "mixed":
        score += 5

    return score


def _watchlist_tier(item: dict, score: float) -> str:
    stalker = item.get("stalker") or {}
    if item.get("status") != "VALID_TRADE":
        if str(stalker.get("state") or "") == "near_valid" and score >= 82:
            return "B-tier"
        if str(stalker.get("state") or "") == "developing" and score >= 68:
            return "watch"
        return "skip"
    if score >= 150:
        return "A-tier"
    if score >= 120:
        return "B-tier"
    return "skip"

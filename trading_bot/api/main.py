from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from trading_bot.core.alerts_store import load_alerts
from trading_bot.core.backtester import backtest_symbol
from trading_bot.core.confluence_engine import evaluate_symbol
from trading_bot.core.data_fetcher import DataFetchError, FetchConfig, fetch_ohlc
from trading_bot.core.journal import ensure_trade_logged, get_recent_journal, log_rejected_analysis
from trading_bot.core.performance_tracker import build_performance_snapshot


DataSource = Literal["auto", "binance", "mock", "yfinance", "oanda", "alphavantage", "twelvedata", "stooq"]
BASE_DIR = Path(__file__).resolve().parents[2]
FRONTEND_DIR = BASE_DIR / "frontend"
_CACHE: dict[str, tuple[float, object]] = {}
DATA_TTL_SECONDS = 2.0
PERFORMANCE_TTL_SECONDS = 4.0
JOURNAL_TTL_SECONDS = 3.0


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
) -> dict:
    normalized_interval = _normalize_execution_interval(interval)
    cache_key = f"data|{symbol.upper()}|{normalized_interval}|{source}"
    cached = _cache_get(cache_key, DATA_TTL_SECONDS)
    if cached is not None:
        return cached

    try:
        daily_data = fetch_ohlc(FetchConfig(symbol=symbol, interval="1d", limit=220, source=source))
        h1_data = fetch_ohlc(FetchConfig(symbol=symbol, interval="1h", limit=320, source=source))
        m30_data = fetch_ohlc(FetchConfig(symbol=symbol, interval="30m", limit=240, source=source)) if normalized_interval == "30m" else None

        setup = evaluate_symbol(symbol=symbol, daily_data=daily_data, h1_data=h1_data, m30_data=m30_data)
        latest_price = round(float((m30_data if m30_data is not None else h1_data).iloc[-1]["close"]), 4)
        payload = _build_dashboard_payload(
            symbol=symbol,
            source=source,
            interval=normalized_interval,
            setup=setup,
            daily_data=daily_data,
            h1_data=h1_data,
            m30_data=m30_data,
            latest_price=latest_price,
        )
    except DataFetchError as exc:
        payload = _error_payload(symbol=symbol, source=source, interval=normalized_interval, message=str(exc))
    except Exception as exc:
        payload = _error_payload(symbol=symbol, source=source, interval=normalized_interval, message=f"Error fetching data: {exc}")

    _cache_set(cache_key, payload)
    return payload


@app.get("/status")
def status() -> dict:
    return {
        "status": "ok",
        "message": "System running",
        "timestamp": datetime.now(UTC).isoformat(),
    }


@app.get("/journal")
def journal(limit: int = Query(default=25, ge=1, le=200)) -> dict:
    cache_key = f"journal|{limit}"
    cached = _cache_get(cache_key, JOURNAL_TTL_SECONDS)
    if cached is not None:
        return cached
    payload = {"entries": get_recent_journal(limit=limit)}
    _cache_set(cache_key, payload)
    return payload


@app.get("/performance")
def performance() -> dict:
    cache_key = "performance"
    cached = _cache_get(cache_key, PERFORMANCE_TTL_SECONDS)
    if cached is not None:
        return cached
    payload = build_performance_snapshot()
    _cache_set(cache_key, payload)
    return payload


@app.get("/backtest")
def backtest(
    symbol: str = Query(default="EURUSD"),
    source: DataSource = Query(default="auto"),
) -> dict:
    try:
        daily_data = fetch_ohlc(FetchConfig(symbol=symbol, interval="1d", limit=260, source=source))
        h1_data = fetch_ohlc(FetchConfig(symbol=symbol, interval="1h", limit=420, source=source))
        m30_data = fetch_ohlc(FetchConfig(symbol=symbol, interval="30m", limit=520, source=source))
        return backtest_symbol(symbol=symbol, daily_data=daily_data, h1_data=h1_data, m30_data=m30_data)
    except Exception as exc:
        return {"status": "ERROR", "message": str(exc), "symbol": symbol.upper()}


def _build_dashboard_payload(
    *,
    symbol: str,
    source: str,
    interval: str,
    setup: dict,
    daily_data,
    h1_data,
    m30_data,
    latest_price: float,
) -> dict:
    performance = build_performance_snapshot()
    journal_entries = get_recent_journal(limit=12)
    alerts = list(reversed(load_alerts(limit=12)))
    daily_bias = setup.get("daily_bias", "neutral")
    h1_bias = setup.get("h1_bias", "neutral")

    if setup["status"] == "VALID_TRADE":
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
            timeframes_used=["1d", "1h", "30m"] if m30_data is not None else ["1d", "1h"],
            profit_factor=performance.get("profit_factor"),
        )
    else:
        log_rejected_analysis(
            symbol=symbol,
            strategy="SMC",
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
        "entry": setup.get("entry"),
        "sl": setup.get("sl"),
        "tp": setup.get("tp"),
        "strategies": setup.get("strategies", []),
        "confluences": setup.get("confluences", []),
        "missing": setup.get("missing", []),
        "alerts": alerts,
        "journal": journal_entries,
        "performance": performance,
        "setup_details": setup.get("strategy_results", {}),
        "analysis_context": _build_analysis_context(setup),
        "chart_overlays": _build_chart_overlays(setup),
        "timeframes": {
            "daily": {"bias": str(daily_bias).upper(), "latest_price": round(float(daily_data.iloc[-1]["close"]), 4)},
            "h1": {"bias": str(h1_bias).upper(), "latest_price": round(float(h1_data.iloc[-1]["close"]), 4)},
            "m30": {
                "bias": str(setup.get("setup_details", {}).get("smc", {}).get("details", {}).get("refinement", {}).get("trend", "unused")).upper(),
                "latest_price": round(float(m30_data.iloc[-1]["close"]), 4) if m30_data is not None else None,
                "used": m30_data is not None,
            },
        },
        "timestamp": setup.get("timestamp") or datetime.now(UTC).isoformat(),
    }


def _build_analysis_context(setup: dict) -> dict:
    smc = setup.get("strategy_results", {}).get("smc", {})
    details = smc.get("details", {})
    return {
        "daily_bias": details.get("daily_bias"),
        "h1_structure": details.get("h1_structure"),
        "mss": details.get("mss"),
        "bos": details.get("bos"),
        "last_break": details.get("last_break"),
        "inducement": details.get("inducement"),
        "order_block": details.get("order_block"),
        "fvg": details.get("fvg"),
        "refinement": details.get("refinement"),
    }


def _build_chart_overlays(setup: dict) -> dict:
    details = setup.get("strategy_results", {}).get("smc", {}).get("details", {})
    return {
        "order_block": details.get("order_block"),
        "fvg": details.get("fvg"),
        "inducement": details.get("inducement"),
        "trade_levels": {
            "entry": setup.get("entry"),
            "sl": setup.get("sl"),
            "tp": setup.get("tp"),
        },
    }


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
        "entry": None,
        "sl": None,
        "tp": None,
        "strategies": [],
        "confluences": [],
        "missing": [],
        "alerts": [],
        "journal": [],
        "performance": {},
        "setup_details": {},
        "analysis_context": {},
        "chart_overlays": {},
        "timeframes": {},
        "timestamp": datetime.now(UTC).isoformat(),
    }


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
    if interval == "15m":
        return "1h"
    return interval

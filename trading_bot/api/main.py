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
from trading_bot.core.instrument_universe import APPROVED_SYMBOLS, is_supported_symbol
from trading_bot.core.journal import ensure_trade_logged, get_recent_journal, log_rejected_analysis
from trading_bot.core.news_engine import JsonEconomicCalendarProvider, build_news_alerts, derive_news_bias, fetch_market_moving_events, get_pair_news_bias, split_symbol_currencies
from trading_bot.core.performance_tracker import build_performance_snapshot


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
    symbol = symbol.upper()
    if not is_supported_symbol(symbol):
        return _error_payload(
            symbol=symbol,
            source=source,
            interval=_normalize_execution_interval(interval),
            message="Unsupported symbol. Use only: ETHUSDT, GBPUSD, EURUSD, BTCUSDT, XAUUSD, NAS100, USDCHF, USDJPY.",
        )

    normalized_interval = _normalize_execution_interval(interval)
    cache_key = f"data|{symbol}|{normalized_interval}|{source}"
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


@app.get("/watchlist")
def watchlist(source: DataSource = Query(default="auto")) -> dict:
    cache_key = f"watchlist|{source}"
    cached = _cache_get(cache_key, WATCHLIST_TTL_SECONDS)
    if cached is not None:
        return cached

    items: list[dict] = []
    for symbol in APPROVED_SYMBOLS:
        item_source = _default_source_for_symbol(symbol, source)
        try:
            daily_data = fetch_ohlc(FetchConfig(symbol=symbol, interval="1d", limit=220, source=item_source))
            h1_data = fetch_ohlc(FetchConfig(symbol=symbol, interval="1h", limit=320, source=item_source))
            m30_data = fetch_ohlc(FetchConfig(symbol=symbol, interval="30m", limit=240, source=item_source))
            setup = evaluate_symbol(symbol=symbol, daily_data=daily_data, h1_data=h1_data, m30_data=m30_data)
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
                    "entry": setup.get("entry"),
                    "latest_price": round(float(h1_data.iloc[-1]["close"]), 4),
                    "message": setup.get("message", ""),
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
                    "entry": None,
                    "latest_price": None,
                    "message": str(exc),
                }
            )

    payload = {"symbols": items, "timestamp": datetime.now(UTC).isoformat()}
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

    if not ECONOMIC_CALENDAR_PATH.exists():
        payload = {
            "configured": False,
            "events": [],
            "message": "No economic calendar configured. Add trading_bot/data/economic_calendar.json to enable pair-specific news.",
        }
        _cache_set(cache_key, payload)
        return payload

    provider = JsonEconomicCalendarProvider(ECONOMIC_CALENDAR_PATH)
    currencies = list(split_symbol_currencies(symbol))
    events = fetch_market_moving_events(provider=provider, currencies=currencies)
    bias_by_currency = derive_news_bias(currencies=currencies, events=events)
    pair_bias = get_pair_news_bias(symbol, bias_by_currency)
    upcoming, released, sudden = build_news_alerts(symbol=symbol, events=events)

    payload = {
        "configured": True,
        "symbol": symbol,
        "pair_news_bias": pair_bias.upper(),
        "currencies": currencies,
        "events": [
            {
                "event_name": event.event_name,
                "currency": event.currency,
                "impact": event.impact,
                "time": event.time.isoformat(),
                "forecast": event.forecast,
                "previous": event.previous,
                "actual": event.actual,
                "market_moving": event.market_moving,
                "is_scheduled": event.is_scheduled,
            }
            for event in events[:12]
        ],
        "alerts": {
            "upcoming": upcoming,
            "released": released,
            "sudden": sudden,
        },
        "message": "Pair-specific news loaded." if events else "No relevant market-moving news in range.",
    }
    _cache_set(cache_key, payload)
    return payload


@app.get("/backtest")
def backtest(
    symbol: str = Query(default="EURUSD"),
    source: DataSource = Query(default="auto"),
) -> dict:
    symbol = symbol.upper()
    if not is_supported_symbol(symbol):
        return {
            "status": "ERROR",
            "message": "Unsupported symbol. Use only: ETHUSDT, GBPUSD, EURUSD, BTCUSDT, XAUUSD, NAS100, USDCHF, USDJPY.",
            "symbol": symbol,
        }

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


def _default_source_for_symbol(symbol: str, requested_source: str) -> str:
    if requested_source != "auto":
        return requested_source
    if symbol.endswith("USDT"):
        return "binance"
    return "yfinance"

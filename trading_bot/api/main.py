"""
main.py — Trading Intelligence Platform API
Run:  python -m uvicorn trading_bot.api.main:app --reload --port 8000
"""
from __future__ import annotations
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from trading_bot.core.data_fetcher import fetch_all_timeframes, fetch_ohlcv
from trading_bot.core.strategy_strict_liquidity import analyze, APPROVED_SYMBOLS
from trading_bot.core.alert_system import send_setup_alert
from trading_bot.core.performance_tracker import compute_performance
from trading_bot.core.journal import load_journal

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Trading Intelligence Platform", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR     = Path(__file__).resolve().parent.parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
DATA_DIR     = BASE_DIR / "trading_bot" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

JOURNAL_FILE  = DATA_DIR / "trade_journal.json"
ALERTS_FILE   = DATA_DIR / "alerts.json"

# ── In-memory scan cache ──────────────────────────────────────────────────────
_scan_cache: dict = {"symbols": [], "updated_at": None}
_daily_count: int = 0


def _load_json(path: Path, default):
    try:
        if path.exists():
            return json.loads(path.read_text())
    except Exception:
        pass
    return default


def _save_json(path: Path, data):
    try:
        path.write_text(json.dumps(data, indent=2, default=str))
    except Exception as e:
        logger.error("Failed to save %s: %s", path, e)


def _run_analysis(symbol: str, source: str = "auto") -> dict:
    """Run full MTF analysis for one symbol. Returns serialisable dict."""
    try:
        tfs = fetch_all_timeframes(symbol, source)
        result = analyze(
            symbol   = symbol,
            df_daily = tfs.get("daily"),
            df_h4    = tfs.get("h4"),
            df_h1    = tfs.get("h1"),
            df_m15   = tfs.get("m15"),
            daily_count = _daily_count,
        )
        d = result.to_dict()
        d["source"] = source
        # Build checklist from confluences + missing
        checklist = [{"name": c, "ok": True,  "detail": ""} for c in (result.confluences or [])]
        checklist += [{"name": m, "ok": False, "detail": ""} for m in (result.missing   or [])]
        d["analysis_context"] = {
            "checklist": checklist,
            "session":   {"session": result.session},
            "regime":    {"regime": "trending" if (result.adx or 0) >= 18 else "ranging"},
            "order_block": {"confirmed": False},
            "fvg": {"confirmed": False},
            "inducement": {"confirmed": False},
        }
        d["timeframes"] = {
            "daily": {"bias": result.daily_bias, "latest_price": result.latest_price},
            "h1":    {"bias": result.h1_bias,    "latest_price": result.latest_price},
        }
        d["chart_overlays"] = {}
        d["risk_reward_ratio"] = result.rr
        d["final_bias"] = result.bias or result.daily_bias or "NEUTRAL"
        # Map status for frontend; fire Telegram alert on valid trades
        if d.get("status") == "VALID_TRADE":
            d["final_bias"] = result.bias or "NEUTRAL"
            send_setup_alert(result)
        d["stalker"] = {
            "state": _stalk_state(result),
            "score": result.quality_score,
        }
        d["tier"]          = _tier(result)
        d["ranking_score"] = result.quality_score
        d["rank"]          = "-"
        return d
    except Exception as e:
        logger.error("Analysis failed for %s: %s", symbol, e)
        return {
            "status": "ERROR", "symbol": symbol, "message": str(e),
            "final_bias": "NEUTRAL", "daily_bias": "-", "h1_bias": "-",
            "latest_price": None, "confidence": "LOW", "source": source,
            "stalker": {"state": "error", "score": 0},
            "tier": "C", "ranking_score": 0, "rank": "-",
            "analysis_context": {"checklist": [], "session": {}, "regime": {}},
        }


def _stalk_state(r) -> str:
    if r.status == "VALID_TRADE":  return "valid"
    if (r.quality_score or 0) >= 45: return "near_valid"
    if (r.quality_score or 0) >= 25: return "developing"
    return "watching"


def _tier(r) -> str:
    s = r.quality_score or 0
    if s >= 80: return "A"
    if s >= 65: return "B"
    if s >= 45: return "C"
    return "D"


# ── Static files ──────────────────────────────────────────────────────────────
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def root():
    idx = FRONTEND_DIR / "index.html"
    if idx.exists():
        return HTMLResponse(idx.read_text())
    return HTMLResponse("<h2>Frontend not found. Place index.html in /frontend/</h2>")


@app.get("/data")
def get_data(
    symbol:   str = Query(default="EURUSD"),
    interval: str = Query(default="1h"),
    source:   str = Query(default="auto"),
    lite:     str = Query(default="false"),
):
    sym = symbol.upper()
    data = _run_analysis(sym, source)
    return JSONResponse(content=data)


@app.get("/watchlist")
def get_watchlist(
    source: str = Query(default="auto"),
    fast:   str = Query(default="false"),
):
    global _scan_cache
    symbols = []
    for sym in APPROVED_SYMBOLS:
        d = _run_analysis(sym, source)
        d["symbol"] = sym
        symbols.append(d)

    # Rank by quality_score
    symbols.sort(key=lambda x: x.get("ranking_score", 0), reverse=True)
    for i, s in enumerate(symbols, 1):
        s["rank"] = f"#{i}"

    _scan_cache = {"symbols": symbols, "updated_at": datetime.now(timezone.utc).isoformat()}
    return JSONResponse(content=_scan_cache)


@app.get("/scan-report")
def get_scan_report(
    source:  str = Query(default="auto"),
    broader: str = Query(default="false"),
    fast:    str = Query(default="false"),
):
    syms = _scan_cache.get("symbols", [])
    if not syms:
        # quick scan
        syms = []
        for sym in APPROVED_SYMBOLS:
            d = _run_analysis(sym, source)
            d["symbol"] = sym
            syms.append(d)

    valid      = [s for s in syms if s.get("status") == "VALID_TRADE"]
    near_valid = [s for s in syms if s.get("stalker",{}).get("state") == "near_valid"]
    developing = [s for s in syms if s.get("stalker",{}).get("state") == "developing"]
    return JSONResponse({"valid": valid, "near_valid": near_valid, "developing": developing})


@app.get("/journal")
def get_journal(
    limit:    int = Query(default=50),
    pair:     str = Query(default=""),
    strategy: str = Query(default=""),
    result:   str = Query(default=""),
    quality:  str = Query(default=""),
    month:    str = Query(default=""),
):
    entries = _load_json(JOURNAL_FILE, [])
    if pair:     entries = [e for e in entries if e.get("symbol","").upper() == pair.upper()]
    if strategy: entries = [e for e in entries if strategy.lower() in str(e.get("strategy","")).lower()]
    if result:   entries = [e for e in entries if str(e.get("result","")).upper() == result.upper()]
    wins   = sum(1 for e in entries if str(e.get("result","")).upper() == "WIN")
    losses = sum(1 for e in entries if str(e.get("result","")).upper() == "LOSS")
    closed = sum(1 for e in entries if e.get("status") in ("WIN","LOSS","closed"))
    wr     = round(wins/(wins+losses)*100, 1) if (wins+losses) > 0 else 0
    return JSONResponse({
        "entries": entries[-limit:],
        "summary": {
            "count": len(entries), "closed": closed,
            "wins": wins, "losses": losses, "win_rate": wr,
        },
        "filters": {"pair": pair, "strategy": strategy, "result": result},
    })


@app.get("/alerts")
def get_alerts(limit: int = Query(default=30)):
    alerts = _load_json(ALERTS_FILE, [])
    return JSONResponse({"entries": alerts[-limit:]})


@app.get("/performance")
def get_performance():
    entries     = load_journal()          # normalised — handles old + new entry formats
    perf        = compute_performance(entries)
    scanner     = _scan_cache
    valid_count = len([s for s in scanner.get("symbols", []) if s.get("status") == "VALID_TRADE"])
    return JSONResponse({
        "total_trades":   len(entries),
        "closed_trades":  perf["total_trades"],
        "open_trades":    perf["open"],
        "wins":           perf["wins"],
        "losses":         perf["losses"],
        "win_rate":       perf["win_rate_pct"],
        "profit_factor":  perf["profit_factor"],
        "avg_rr_win":     perf["avg_rr_win"],
        "current_streak": perf["current_streak"],
        "per_pair":       perf["per_pair"],
        "per_strategy":   perf["per_strategy"],
        "per_session":    perf["per_session"],
        "recent_trades":  perf["recent_trades"],
        "scan_diagnostics": {
            "evaluated_symbols":  len(APPROVED_SYMBOLS),
            "valid_candidates":   valid_count,
            "selected_candidates": [s for s in scanner.get("symbols", []) if s.get("status") == "VALID_TRADE"],
        },
        "scanner_health": {
            "status":              "healthy" if scanner.get("updated_at") else "idle",
            "last_successful_scan": scanner.get("updated_at", "-"),
            "total_symbols":       len(APPROVED_SYMBOLS),
            "completed_symbols":   len(scanner.get("symbols", [])),
        },
    })


@app.get("/status")
def get_status():
    scanner = _scan_cache
    syms = scanner.get("symbols", [])
    valid = [s for s in syms if s.get("status") == "VALID_TRADE"]
    return JSONResponse({
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "forward_test": {"enabled": False, "name": "Live Monitoring", "mode_label": "OFF"},
        "scanner_health": {
            "status": "healthy" if syms else "idle",
            "total_symbols": len(APPROVED_SYMBOLS),
            "completed_symbols": len(syms),
            "last_progress_at": scanner.get("updated_at", "-"),
        },
    })


@app.get("/news")
def get_news(symbol: str = Query(default="EURUSD")):
    return JSONResponse({
        "symbol": symbol,
        "configured": False,
        "message": "News feed not configured. Add economic_calendar.json to enable.",
        "pair_news_bias": "NEUTRAL",
        "news_lock": {"locked": False},
        "events": [],
        "headlines": [],
    })


@app.get("/forward-test")
def get_forward_test(limit: int = Query(default=25), week_only: str = Query(default="false")):
    return JSONResponse({"entries": [], "summary": {"count": 0, "closed": 0, "open": 0,
                                                      "wins": 0, "losses": 0, "win_rate": 0,
                                                      "total_r": 0, "average_r": 0}})


@app.get("/strategy-lab")
def get_strategy_lab():
    return JSONResponse({"strategies": [], "summary": {}, "promotion_table": []})


@app.get("/broker-drafts")
def get_broker_drafts(limit: int = Query(default=25)):
    return JSONResponse({"entries": [], "summary": {"count": 0, "active": 0, "closed": 0}})


@app.get("/execution-control")
def get_execution_control():
    return JSONResponse({"enabled": False, "draft_only": True, "kill_switch": False,
                          "strategy_freeze": False, "max_open_positions": 3,
                          "current_open_positions": 0, "daily_closed_r": 0,
                          "max_daily_loss_r": 3, "hard_block_reasons": []})


@app.get("/execution-scorecards")
def get_execution_scorecards(days: int = Query(default=7)):
    return JSONResponse({"daily": [], "promotion_history": [], "summary": {}})


@app.get("/digital-twin")
def get_digital_twin():
    return JSONResponse({"digital_twin": {}})


@app.get("/health")
def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


# ── Legacy endpoints (from original main.py) ─────────────────────────────────
@app.get("/bias")
def get_bias(symbol: str = "BTCUSDT", interval: str = "1h", source: str = "auto"):
    tfs = fetch_all_timeframes(symbol, source)
    result = analyze(symbol, tfs["daily"], tfs["h4"], tfs["h1"], tfs["m15"])
    return result.to_dict()


@app.get("/setup")
def get_setup(symbol: str = "BTCUSDT", interval: str = "1h", source: str = "auto"):
    tfs = fetch_all_timeframes(symbol, source)
    result = analyze(symbol, tfs["daily"], tfs["h4"], tfs["h1"], tfs["m15"])
    return result.to_dict()


@app.get("/chart_data")
def get_chart_data(
    symbol: str = Query(default="BTCUSDT"),
    interval: str = Query(default="1h"),
    source: str = Query(default="auto"),
    limit: int = Query(default=100),
):
    df = fetch_ohlcv(symbol, interval, source, limit)
    if df is None:
        return JSONResponse({"error": "No data", "candles": []})
    candles = []
    for ts, row in df.iterrows():
        candles.append({
            "time":  ts.isoformat(),
            "open":  round(float(row["open"]), 6),
            "high":  round(float(row["high"]), 6),
            "low":   round(float(row["low"]),  6),
            "close": round(float(row["close"]),6),
        })
    return JSONResponse({"symbol": symbol, "interval": interval, "candles": candles})
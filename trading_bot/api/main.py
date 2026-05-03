"""
main.py  –  FastAPI backend
─────────────────────────────────────────────────────────────────────────────
Improved endpoints:
  GET /           → dashboard HTML
  GET /data       → full analysis for one symbol
  GET /confluence → confluence map (score breakdown) for one symbol
  GET /watchlist  → quick summary across all approved pairs
  GET /status     → health check
  GET /journal    → recent journal entries
  GET /performance→ win/loss stats
  GET /backtest   → lightweight historical backtest
  GET /news       → pair news context
─────────────────────────────────────────────────────────────────────────────
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

from ..core.strategy_strict_liquidity import (
    analyze, APPROVED_SYMBOLS, MAX_DAILY_SETUPS,
)
from ..core.confluence_engine import build_confluence_report
from ..core.data_fetcher import fetch_all_timeframes
from ..core.journal import load_journal, append_journal
from ..core.performance_tracker import compute_performance

logger = logging.getLogger(__name__)
app = FastAPI(title="Trading Intelligence Platform v2", version="2.0.0")

BASE = Path(__file__).resolve().parent.parent.parent
FRONTEND_DIR = BASE / "frontend"
DATA_DIR     = BASE / "trading_bot" / "data"

# Mount static frontend
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

# ── Daily setup counter (shared with scanner via env or simple file) ──────────
def _get_daily_count() -> int:
    try:
        f = DATA_DIR / "daily_count.json"
        if f.exists():
            data = json.loads(f.read_text())
            today = datetime.now(timezone.utc).date().isoformat()
            if data.get("date") == today:
                return int(data.get("count", 0))
    except Exception:
        pass
    return 0


# ── Root ──────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def root():
    index = FRONTEND_DIR / "index.html"
    if index.exists():
        return HTMLResponse(content=index.read_text(), status_code=200)
    return HTMLResponse(content="<h1>Trading Intelligence Platform v2</h1>", status_code=200)


# ── Main analysis endpoint ────────────────────────────────────────────────────

@app.get("/data")
async def get_data(
    symbol:   str = Query("GBPUSD"),
    interval: str = Query("1h"),
    source:   str = Query("auto"),
):
    symbol = symbol.upper()
    tfs = fetch_all_timeframes(symbol, source)
    result = analyze(
        symbol      = symbol,
        df_daily    = tfs["daily"],
        df_h4       = tfs["h4"],
        df_h1       = tfs["h1"],
        df_m15      = tfs["m15"],
        daily_count = _get_daily_count(),
    )
    return JSONResponse(result.to_dict())


# ── Confluence map endpoint (NEW) ─────────────────────────────────────────────

@app.get("/confluence")
async def get_confluence(
    symbol: str = Query("GBPUSD"),
    source: str = Query("auto"),
):
    symbol = symbol.upper()
    tfs = fetch_all_timeframes(symbol, source)
    report = build_confluence_report(
        symbol   = symbol,
        df_daily = tfs["daily"],
        df_h4    = tfs["h4"],
        df_h1    = tfs["h1"],
        df_m15   = tfs["m15"],
    )
    return JSONResponse(report.to_dict())


# ── Watchlist summary ─────────────────────────────────────────────────────────

@app.get("/watchlist")
async def get_watchlist(source: str = Query("auto")):
    results = []
    daily_count = _get_daily_count()
    for sym in APPROVED_SYMBOLS:
        try:
            tfs = fetch_all_timeframes(sym, source)
            report = build_confluence_report(
                symbol=sym, df_daily=tfs["daily"], df_h4=tfs["h4"],
                df_h1=tfs["h1"], df_m15=tfs["m15"],
            )
            result = analyze(
                symbol=sym, df_daily=tfs["daily"], df_h4=tfs["h4"],
                df_h1=tfs["h1"], df_m15=tfs["m15"],
                daily_count=daily_count,
            )
            entry = result.to_dict()
            entry["confluence_score"]  = report.quality_score
            entry["confluence_detail"] = report.to_dict()
            results.append(entry)
        except Exception as e:
            results.append({"symbol": sym, "status": "ERROR", "message": str(e)})
    return JSONResponse({"watchlist": results, "daily_setups_fired": daily_count,
                         "daily_cap": MAX_DAILY_SETUPS})


# ── Status ────────────────────────────────────────────────────────────────────

@app.get("/status")
async def status():
    return JSONResponse({
        "status": "online",
        "version": "2.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "daily_setups_fired": _get_daily_count(),
        "daily_cap": MAX_DAILY_SETUPS,
        "approved_symbols": APPROVED_SYMBOLS,
    })


# ── Journal ───────────────────────────────────────────────────────────────────

@app.get("/journal")
async def get_journal(limit: int = Query(50)):
    entries = load_journal()
    return JSONResponse({"entries": entries[-limit:], "total": len(entries)})


# ── Performance ───────────────────────────────────────────────────────────────

@app.get("/performance")
async def get_performance():
    entries = load_journal()
    perf = compute_performance(entries)
    return JSONResponse(perf)


# ── Backtest ──────────────────────────────────────────────────────────────────

@app.get("/backtest")
async def backtest(
    symbol: str = Query("GBPUSD"),
    source: str = Query("auto"),
    lookback_days: int = Query(30),
):
    """
    Walk-forward backtest: replays the strategy over historical H1 candles.
    Slices the data so each 'step' uses only past data.
    """
    from ..core.data_fetcher import fetch_ohlcv
    import pandas as pd

    symbol = symbol.upper()
    df_d  = fetch_ohlcv(symbol, "1d",  source, 250)
    df_h4 = fetch_ohlcv(symbol, "4h",  source, 200)
    df_h1 = fetch_ohlcv(symbol, "1h",  source, 24 * lookback_days + 50)
    df_m  = fetch_ohlcv(symbol, "15m", source, 96 * lookback_days + 50)

    if df_h1 is None or len(df_h1) < 50:
        return JSONResponse({"error": "Insufficient data for backtest"}, status_code=422)

    wins, losses, skipped = 0, 0, 0
    trades = []

    step_size = 4  # analyze every 4 H1 candles (≈ 4h step)
    for i in range(50, len(df_h1) - step_size, step_size):
        slice_h1 = df_h1.iloc[:i]
        slice_d  = df_d[df_d.index <= slice_h1.index[-1]] if df_d is not None else df_d
        slice_h4 = df_h4[df_h4.index <= slice_h1.index[-1]] if df_h4 is not None else df_h4
        if df_m is not None:
            slice_m = df_m[df_m.index <= slice_h1.index[-1]]
        else:
            slice_m = None

        try:
            r = analyze(symbol, slice_d, slice_h4, slice_h1, slice_m,
                        daily_count=0)
        except Exception:
            continue

        if r.status != "VALID":
            skipped += 1
            continue

        # Simulate outcome: look ahead in next candles
        future = df_h1.iloc[i: i + step_size * 6]
        if r.bias == "BUY":
            hit_tp = (future["high"] >= r.tp).any()
            hit_sl = (future["low"]  <= r.sl).any()
        else:
            hit_tp = (future["low"]  <= r.tp).any()
            hit_sl = (future["high"] >= r.sl).any()

        if hit_tp and not hit_sl:
            outcome = "WIN"
            wins += 1
        elif hit_sl:
            outcome = "LOSS"
            losses += 1
        else:
            outcome = "OPEN"

        trades.append({
            "time":   str(slice_h1.index[-1]),
            "bias":   r.bias,
            "entry":  r.entry,
            "sl":     r.sl,
            "tp":     r.tp,
            "rr":     r.rr,
            "score":  r.quality_score,
            "outcome": outcome,
        })

    total = wins + losses
    win_rate = round(wins / total * 100, 1) if total > 0 else 0

    return JSONResponse({
        "symbol":      symbol,
        "lookback_days": lookback_days,
        "total_trades": total,
        "wins":        wins,
        "losses":      losses,
        "skipped_no_setup": skipped,
        "win_rate_pct": win_rate,
        "trades":      trades[-20:],  # last 20 for display
    })


# ── News ──────────────────────────────────────────────────────────────────────

@app.get("/news")
async def get_news(symbol: str = Query("GBPUSD")):
    cal = DATA_DIR / "economic_calendar.json"
    if not cal.exists():
        return JSONResponse({
            "symbol":  symbol.upper(),
            "status":  "not_configured",
            "message": "economic_calendar.json not found in trading_bot/data/",
        })
    try:
        data   = json.loads(cal.read_text())
        events = [e for e in data if symbol.upper()[:3] in e.get("currencies", [])]
        return JSONResponse({"symbol": symbol.upper(), "events": events})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

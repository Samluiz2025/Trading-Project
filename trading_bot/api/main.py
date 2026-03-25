from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from trading_bot.core.ai_engine import get_model_status, predict_signal, train_model
from trading_bot.core.data_fetcher import DataFetchError, FetchConfig, fetch_ohlc
from trading_bot.core.market_structure import detect_market_structure
from trading_bot.core.news_engine import (
    JsonEconomicCalendarProvider,
    build_news_alerts,
    fetch_market_moving_events,
    split_symbol_currencies,
)
from trading_bot.core.strategy_engine import generate_trade_setup
from trading_bot.core.supply_demand import detect_supply_demand_zones


DataSource = Literal["auto", "binance", "mock", "yfinance", "oanda", "alphavantage", "twelvedata", "stooq"]
BASE_DIR = Path(__file__).resolve().parents[2]
FRONTEND_DIR = BASE_DIR / "frontend"
DEFAULT_NEWS_CALENDAR_PATH = BASE_DIR / "trading_bot" / "data" / "economic_calendar.json"


app = FastAPI(
    title="Trading Intelligence System",
    version="0.2.0",
    description="Phase 2 backend with market bias, zones, setups, and chart visualization.",
)
app.mount("/frontend", StaticFiles(directory=FRONTEND_DIR), name="frontend")


@app.get("/")
def root() -> HTMLResponse:
    """Serve the TradingView-based dashboard from the application root."""

    return get_tradingview_page(
        symbol="FX:EURUSD",
        interval="15",
        backend_symbol="EURUSD",
        backend_interval="15m",
        source="auto",
    )


@app.get("/data")
def get_frontend_data(
    symbol: str = Query(default="EURUSD", description="Instrument symbol."),
    interval: str = Query(default="15m", description="Backend candle interval."),
    limit: int = Query(default=200, ge=20, le=1000, description="Number of candles."),
    source: DataSource = Query(default="auto", description="OHLC data source."),
) -> dict:
    """Return frontend-ready dashboard data from the existing backend engines."""

    try:
        candles = fetch_ohlc(
            FetchConfig(
                symbol=symbol,
                interval=interval,
                limit=limit,
                source=source,
            )
        )
        zones = detect_supply_demand_zones(candles, symbol=symbol, timeframe=interval)
        news_provider = _load_default_news_provider()
        current_time = datetime.now(UTC)
        news_events = fetch_market_moving_events(
            provider=news_provider,
            currencies=list(split_symbol_currencies(symbol)),
            current_time=current_time,
        )
        setup_payload = generate_trade_setup(
            candles,
            symbol=symbol,
            timeframe=interval,
            news_events=news_events,
            current_time=current_time,
            use_ai=True,
        )
        alerts = _build_frontend_alerts(
            symbol=symbol,
            setup_payload=setup_payload,
            news_events=news_events,
            current_time=current_time,
        )
    except DataFetchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    setup_list = [setup_payload["setup"]] if setup_payload["setup"] else []
    return {
        "symbol": symbol.upper(),
        "interval": interval,
        "source": source,
        "technical_bias": setup_payload["technical_bias"],
        "news_bias": setup_payload["news_bias"],
        "final_bias": setup_payload["final_bias"],
        "confidence": setup_payload["confidence"],
        "ai_prediction": setup_payload.get("ai_prediction", "NO TRADE"),
        "ai_confidence": setup_payload.get("ai_confidence", 0),
        "agreement_with_strategy": setup_payload.get("agreement_with_strategy", False),
        "latest_price": setup_payload["latest_price"],
        "setups": setup_list,
        "zones": zones,
        "alerts": alerts,
    }


@app.get("/train")
def train_ai_model(
    symbol: str = Query(default="EURUSD", description="Instrument symbol."),
    interval: str = Query(default="15m", description="Candle interval."),
    limit: int = Query(default=500, ge=100, le=5000, description="Number of candles."),
    source: DataSource = Query(default="auto", description="OHLC data source."),
) -> dict:
    """Train the lightweight AI model from historical OHLC data."""

    try:
        candles = fetch_ohlc(
            FetchConfig(
                symbol=symbol,
                interval=interval,
                limit=limit,
                source=source,
            )
        )
        news_provider = _load_default_news_provider()
        news_events = fetch_market_moving_events(
            provider=news_provider,
            currencies=list(split_symbol_currencies(symbol)),
            current_time=datetime.now(UTC),
        )
        return train_model(
            dataframe=candles,
            symbol=symbol,
            timeframe=interval,
            news_events=news_events,
        )
    except (DataFetchError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/predict")
def predict_ai_signal(
    symbol: str = Query(default="EURUSD", description="Instrument symbol."),
    interval: str = Query(default="15m", description="Candle interval."),
    limit: int = Query(default=250, ge=50, le=2000, description="Number of candles."),
    source: DataSource = Query(default="auto", description="OHLC data source."),
) -> dict:
    """Predict BUY / SELL / NO TRADE from the AI model."""

    try:
        candles = fetch_ohlc(
            FetchConfig(
                symbol=symbol,
                interval=interval,
                limit=limit,
                source=source,
            )
        )
        news_provider = _load_default_news_provider()
        current_time = datetime.now(UTC)
        news_events = fetch_market_moving_events(
            provider=news_provider,
            currencies=list(split_symbol_currencies(symbol)),
            current_time=current_time,
        )
        strategy_payload = generate_trade_setup(
            candles,
            symbol=symbol,
            timeframe=interval,
            news_events=news_events,
            current_time=current_time,
        )
        result = predict_signal(
            dataframe=candles,
            symbol=symbol,
            timeframe=interval,
            strategy_bias=strategy_payload["final_bias"],
            news_events=news_events,
        )
        return asdict(result)
    except (DataFetchError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/status")
def ai_status() -> dict:
    """Return model availability and current performance metrics."""

    return asdict(get_model_status())


@app.get("/bias")
def get_bias(
    symbol: str = Query(default="BTCUSDT", description="Instrument symbol."),
    interval: str = Query(default="1h", description="Candle interval."),
    limit: int = Query(default=200, ge=20, le=1000, description="Number of candles."),
    source: DataSource = Query(default="auto", description="OHLC data source."),
) -> dict:
    """Return market bias and recent structure information."""

    try:
        candles = fetch_ohlc(
            FetchConfig(
                symbol=symbol,
                interval=interval,
                limit=limit,
                source=source,
            )
        )
        structure = detect_market_structure(candles)
    except DataFetchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "symbol": symbol.upper(),
        "interval": interval,
        "source": source,
        "trend": structure["trend"],
        "structure": {
            "last_HH": structure["last_HH"],
            "last_HL": structure["last_HL"],
            "last_LH": structure["last_LH"],
            "last_LL": structure["last_LL"],
            "swing_count": structure["swing_count"],
        },
        "latest_candle": _serialize_candle(candles.iloc[-1].to_dict()),
        "recent_swings": structure["swings"][-8:],
    }


@app.get("/zones")
def get_zones(
    symbol: str = Query(default="BTCUSDT", description="Instrument symbol."),
    interval: str = Query(default="1h", description="Candle interval."),
    limit: int = Query(default=200, ge=20, le=1000, description="Number of candles."),
    source: DataSource = Query(default="auto", description="OHLC data source."),
) -> dict:
    """Return recent supply and demand zones."""

    try:
        candles = fetch_ohlc(
            FetchConfig(
                symbol=symbol,
                interval=interval,
                limit=limit,
                source=source,
            )
        )
        zones = detect_supply_demand_zones(candles, symbol=symbol, timeframe=interval)
    except DataFetchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "symbol": symbol.upper(),
        "interval": interval,
        "source": source,
        "zone_count": len(zones),
        "zones": zones,
    }


@app.get("/setup")
def get_setup(
    symbol: str = Query(default="BTCUSDT", description="Instrument symbol."),
    interval: str = Query(default="1h", description="Candle interval."),
    limit: int = Query(default=200, ge=20, le=1000, description="Number of candles."),
    source: DataSource = Query(default="auto", description="OHLC data source."),
) -> dict:
    """Return the current rule-based trade setup candidate."""

    try:
        candles = fetch_ohlc(
            FetchConfig(
                symbol=symbol,
                interval=interval,
                limit=limit,
                source=source,
            )
        )
        return generate_trade_setup(candles, symbol=symbol, timeframe=interval)
    except DataFetchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/chart_data")
def get_chart_data(
    symbol: str = Query(default="BTCUSDT", description="Instrument symbol."),
    interval: str = Query(default="1h", description="Candle interval."),
    limit: int = Query(default=120, ge=20, le=500, description="Number of candles."),
    source: DataSource = Query(default="auto", description="OHLC data source."),
) -> dict:
    """
    Return chart-ready OHLC data plus analysis metadata.

    This endpoint is designed for the browser chart and later alert consumers.
    """

    try:
        candles = fetch_ohlc(
            FetchConfig(
                symbol=symbol,
                interval=interval,
                limit=limit,
                source=source,
            )
        )
        structure = detect_market_structure(candles)
        zones = detect_supply_demand_zones(candles, symbol=symbol, timeframe=interval)
        setup_payload = generate_trade_setup(candles, symbol=symbol, timeframe=interval)
    except DataFetchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "symbol": symbol.upper(),
        "interval": interval,
        "source": source,
        "trend": structure["trend"],
        "candles": [_serialize_chart_candle(candle) for candle in candles.to_dict("records")],
        "swings": [_serialize_swing_for_chart(swing) for swing in structure["swings"][-20:]],
        "zones": zones,
        "setup": setup_payload["setup"],
        "latest_price": float(candles.iloc[-1]["close"]),
    }


@app.get("/chart", response_class=HTMLResponse)
def get_chart(
    symbol: str = Query(default="BTCUSDT", description="Instrument symbol."),
    interval: str = Query(default="1h", description="Candle interval."),
    limit: int = Query(default=120, ge=20, le=500, description="Number of candles."),
    source: DataSource = Query(default="auto", description="OHLC data source."),
) -> HTMLResponse:
    """Render a simple browser-based candlestick dashboard."""

    chart_payload = get_chart_data(symbol=symbol, interval=interval, limit=limit, source=source)
    chart_payload_json = json.dumps(chart_payload)

    html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Trading Intelligence Chart</title>
    <script src="https://cdn.jsdelivr.net/npm/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"></script>
    <style>
        :root {{
            --bg: #0f1720;
            --panel: #18222d;
            --panel-2: #223140;
            --text: #e7eef6;
            --muted: #9eb0c1;
            --green: #2ecc71;
            --red: #ff6b6b;
            --amber: #f5b041;
            --blue: #5dade2;
            --border: #2d3f50;
        }}
        * {{
            box-sizing: border-box;
        }}
        body {{
            margin: 0;
            font-family: "Segoe UI", Tahoma, sans-serif;
            background:
                radial-gradient(circle at top left, rgba(52, 152, 219, 0.08), transparent 30%),
                radial-gradient(circle at top right, rgba(46, 204, 113, 0.08), transparent 28%),
                var(--bg);
            color: var(--text);
        }}
        .page {{
            max-width: 1440px;
            margin: 0 auto;
            padding: 24px;
        }}
        .hero {{
            display: flex;
            justify-content: space-between;
            gap: 20px;
            flex-wrap: wrap;
            margin-bottom: 18px;
            padding: 20px;
            border: 1px solid var(--border);
            border-radius: 18px;
            background: linear-gradient(135deg, rgba(34, 49, 64, 0.95), rgba(24, 34, 45, 0.95));
        }}
        .hero h1 {{
            margin: 0 0 6px;
            font-size: 30px;
        }}
        .hero p {{
            margin: 0;
            color: var(--muted);
            max-width: 720px;
        }}
        .chips {{
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
            align-items: center;
        }}
        .chip {{
            padding: 8px 12px;
            border-radius: 999px;
            border: 1px solid var(--border);
            background: rgba(255, 255, 255, 0.03);
            font-size: 13px;
        }}
        .controls {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 12px;
            margin-bottom: 18px;
        }}
        .control {{
            display: flex;
            flex-direction: column;
            gap: 6px;
            padding: 14px;
            border-radius: 14px;
            border: 1px solid var(--border);
            background: var(--panel);
        }}
        label {{
            color: var(--muted);
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 0.06em;
        }}
        select, input, button {{
            border-radius: 10px;
            border: 1px solid var(--border);
            font-size: 14px;
        }}
        select, input {{
            padding: 10px 12px;
            background: var(--panel-2);
            color: var(--text);
        }}
        button {{
            padding: 12px 16px;
            background: linear-gradient(135deg, #2ecc71, #27ae60);
            color: #08140d;
            font-weight: 700;
            cursor: pointer;
        }}
        .layout {{
            display: grid;
            grid-template-columns: minmax(0, 2.2fr) minmax(320px, 1fr);
            gap: 18px;
        }}
        .panel {{
            border-radius: 18px;
            border: 1px solid var(--border);
            background: rgba(24, 34, 45, 0.96);
            overflow: hidden;
        }}
        .panel-header {{
            padding: 16px 18px;
            border-bottom: 1px solid var(--border);
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 10px;
        }}
        .panel-title {{
            font-size: 18px;
            font-weight: 700;
        }}
        .trend {{
            padding: 6px 10px;
            border-radius: 999px;
            font-size: 12px;
            font-weight: 700;
            text-transform: uppercase;
        }}
        .trend.bullish {{
            background: rgba(46, 204, 113, 0.18);
            color: var(--green);
        }}
        .trend.bearish {{
            background: rgba(255, 107, 107, 0.16);
            color: var(--red);
        }}
        .trend.ranging {{
            background: rgba(245, 176, 65, 0.16);
            color: var(--amber);
        }}
        #chart-container {{
            height: 620px;
        }}
        .sidebar {{
            display: grid;
            gap: 18px;
        }}
        .metrics {{
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 10px;
            padding: 16px;
        }}
        .metric {{
            padding: 14px;
            border-radius: 14px;
            background: rgba(255, 255, 255, 0.03);
            border: 1px solid rgba(255, 255, 255, 0.04);
        }}
        .metric-label {{
            color: var(--muted);
            font-size: 12px;
            margin-bottom: 6px;
        }}
        .metric-value {{
            font-size: 16px;
            font-weight: 700;
        }}
        .list {{
            padding: 16px;
            display: grid;
            gap: 10px;
            max-height: 280px;
            overflow: auto;
        }}
        .list-item {{
            padding: 12px;
            border-radius: 12px;
            background: rgba(255, 255, 255, 0.03);
            border: 1px solid rgba(255, 255, 255, 0.04);
        }}
        .list-item strong {{
            display: block;
            margin-bottom: 4px;
        }}
        .muted {{
            color: var(--muted);
        }}
        .empty {{
            padding: 16px;
            color: var(--muted);
        }}
        .hint {{
            margin-top: 6px;
            color: var(--muted);
            font-size: 12px;
            line-height: 1.4;
        }}
        @media (max-width: 980px) {{
            .layout {{
                grid-template-columns: 1fr;
            }}
            #chart-container {{
                height: 480px;
            }}
        }}
    </style>
</head>
<body>
    <div class="page">
        <section class="hero">
            <div>
                <h1>Trading Intelligence Dashboard</h1>
                <p>
                    Candlestick chart plus current bias, supply and demand zones, and the latest
                    rule-based setup from your existing backend.
                </p>
            </div>
            <div class="chips">
                <div class="chip">Supported sources: auto, binance, yfinance, stooq, oanda, alphavantage, twelvedata, mock</div>
                <div class="chip">Run command: python -m uvicorn trading_bot.api.main:app --reload</div>
            </div>
        </section>

        <section class="controls">
            <div class="control">
                <label for="symbol-input">Symbol</label>
                <input id="symbol-input" list="symbol-list" value="{symbol}" placeholder="Type any symbol">
                <datalist id="symbol-list">
                    <option value="BTCUSDT"></option>
                    <option value="ETHUSDT"></option>
                    <option value="SOLUSDT"></option>
                    <option value="BNBUSDT"></option>
                    <option value="XRPUSDT"></option>
                    <option value="ADAUSDT"></option>
                    <option value="DOGEUSDT"></option>
                    <option value="AVAXUSDT"></option>
                    <option value="EURUSD"></option>
                    <option value="GBPUSD"></option>
                    <option value="USDJPY"></option>
                    <option value="AUDUSD"></option>
                    <option value="USDCAD"></option>
                    <option value="XAUUSD"></option>
                    <option value="USOIL"></option>
                    <option value="SPX"></option>
                    <option value="NAS100"></option>
                    <option value="DJI"></option>
                    <option value="GER40"></option>
                    <option value="UK100"></option>
                    <option value="JP225"></option>
                </datalist>
                <div class="hint">Examples: BTCUSDT, EURUSD, XAUUSD, USOIL, SPX, NAS100, GER40</div>
            </div>
            <div class="control">
                <label for="interval-select">Interval</label>
                <select id="interval-select">
                    <option value="15m">15m</option>
                    <option value="1h">1h</option>
                    <option value="4h">4h</option>
                    <option value="1d">1d</option>
                </select>
            </div>
            <div class="control">
                <label for="source-select">Data Source</label>
                <select id="source-select">
                    <option value="auto">auto</option>
                    <option value="binance">binance</option>
                    <option value="yfinance">yfinance</option>
                    <option value="oanda">oanda</option>
                    <option value="alphavantage">alphavantage</option>
                    <option value="twelvedata">twelvedata</option>
                    <option value="stooq">stooq</option>
                    <option value="mock">mock</option>
                </select>
            </div>
            <div class="control">
                <label for="limit-input">Candles</label>
                <input id="limit-input" type="number" min="20" max="500" step="20" value="{limit}">
            </div>
            <div class="control">
                <label>Provider Note</label>
                <div class="muted" style="padding-top: 10px;">
                    No-key sources: binance, yfinance, stooq. OANDA, Alpha Vantage, and Twelve Data need credentials.
                </div>
            </div>
            <div class="control">
                <label>Auto Refresh</label>
                <button type="button" id="auto-refresh-button" onclick="toggleAutoRefresh()">Start Auto Refresh</button>
                <div class="hint">Refreshes the dashboard every 15 seconds.</div>
            </div>
            <div class="control">
                <label>Refresh</label>
                <button type="button" onclick="updateChart()">Update Dashboard</button>
            </div>
        </section>

        <section class="layout">
            <div class="panel">
                <div class="panel-header">
                    <div class="panel-title" id="chart-title">Chart</div>
                    <div id="trend-badge" class="trend">trend</div>
                </div>
                <div id="chart-container"></div>
            </div>

            <div class="sidebar">
                <div class="panel">
                    <div class="panel-header">
                        <div class="panel-title">Snapshot</div>
                    </div>
                    <div class="metrics">
                        <div class="metric">
                            <div class="metric-label">Symbol</div>
                            <div class="metric-value" id="metric-symbol">-</div>
                        </div>
                        <div class="metric">
                            <div class="metric-label">Interval</div>
                            <div class="metric-value" id="metric-interval">-</div>
                        </div>
                        <div class="metric">
                            <div class="metric-label">Source</div>
                            <div class="metric-value" id="metric-source">-</div>
                        </div>
                        <div class="metric">
                            <div class="metric-label">Latest Price</div>
                            <div class="metric-value" id="metric-price">-</div>
                        </div>
                    </div>
                </div>

                <div class="panel">
                    <div class="panel-header">
                        <div class="panel-title">Trade Setup</div>
                    </div>
                    <div id="setup-container" class="list"></div>
                </div>

                <div class="panel">
                    <div class="panel-header">
                        <div class="panel-title">Detected Zones</div>
                    </div>
                    <div id="zones-container" class="list"></div>
                </div>

                <div class="panel">
                    <div class="panel-header">
                        <div class="panel-title">Source Notes</div>
                    </div>
                    <div class="list">
                        <div class="list-item">
                            <strong>Binance</strong>
                            <div class="muted">Good for crypto pairs like BTCUSDT and ETHUSDT.</div>
                        </div>
                        <div class="list-item">
                            <strong>Yahoo Finance</strong>
                            <div class="muted">Useful for forex-style symbols like EURUSD and GBPUSD.</div>
                        </div>
                        <div class="list-item">
                            <strong>Stooq</strong>
                            <div class="muted">No-key public CSV source, best for daily or higher timeframe charts.</div>
                        </div>
                        <div class="list-item">
                            <strong>OANDA</strong>
                            <div class="muted">Requires an API token in your environment.</div>
                        </div>
                        <div class="list-item">
                            <strong>Alpha Vantage</strong>
                            <div class="muted">Works with forex, crypto, equities, and proxy index or commodity symbols using an API key.</div>
                        </div>
                        <div class="list-item">
                            <strong>Twelve Data</strong>
                            <div class="muted">Useful for forex, crypto, indices, and commodities with a single time-series endpoint.</div>
                        </div>
                        <div class="list-item">
                            <strong>Other brokers</strong>
                            <div class="muted">FXCM and Pepperstone are not wired yet in this backend because their access flow is SDK or account-platform specific.</div>
                        </div>
                    </div>
                </div>
            </div>
        </section>
    </div>

    <script>
        const initialPayload = {chart_payload_json};
        document.getElementById("symbol-input").value = initialPayload.symbol;
        document.getElementById("interval-select").value = initialPayload.interval;
        document.getElementById("source-select").value = initialPayload.source;
        let autoRefreshHandle = null;

        const chart = LightweightCharts.createChart(document.getElementById("chart-container"), {{
            width: document.getElementById("chart-container").clientWidth,
            height: document.getElementById("chart-container").clientHeight,
            layout: {{
                background: {{ color: "#18222d" }},
                textColor: "#e7eef6",
            }},
            grid: {{
                vertLines: {{ color: "#243646" }},
                horzLines: {{ color: "#243646" }},
            }},
            rightPriceScale: {{
                borderColor: "#2d3f50",
            }},
            timeScale: {{
                borderColor: "#2d3f50",
                timeVisible: true,
                secondsVisible: false,
            }},
        }});

        const candleSeries = chart.addCandlestickSeries({{
            upColor: "#2ecc71",
            downColor: "#ff6b6b",
            borderUpColor: "#2ecc71",
            borderDownColor: "#ff6b6b",
            wickUpColor: "#2ecc71",
            wickDownColor: "#ff6b6b",
        }});

        function formatNumber(value) {{
            return typeof value === "number" ? value.toFixed(4) : "-";
        }}

        function updateInfo(payload) {{
            const latestCandle = payload.candles[payload.candles.length - 1];
            document.getElementById("chart-title").textContent = `${{payload.symbol}} ${{payload.interval}}`;
            document.getElementById("metric-symbol").textContent = payload.symbol;
            document.getElementById("metric-interval").textContent = payload.interval;
            document.getElementById("metric-source").textContent = payload.source;
            document.getElementById("metric-price").textContent = formatNumber(payload.latest_price ?? latestCandle?.close);

            const trendBadge = document.getElementById("trend-badge");
            trendBadge.textContent = payload.trend;
            trendBadge.className = `trend ${{payload.trend}}`;

            const setupContainer = document.getElementById("setup-container");
            if (!payload.setup) {{
                setupContainer.innerHTML = '<div class="empty">No active setup at the current price.</div>';
            }} else {{
                setupContainer.innerHTML = `
                    <div class="list-item">
                        <strong>${{payload.setup.signal}} setup</strong>
                        <div class="muted">Zone type: ${{payload.setup.zone_type}}</div>
                        <div>Entry: ${{formatNumber(payload.setup.entry)}}</div>
                        <div>Stop Loss: ${{formatNumber(payload.setup.stop_loss)}}</div>
                        <div>Take Profit: ${{formatNumber(payload.setup.take_profit)}}</div>
                        <div>R:R: ${{payload.setup.risk_reward_ratio}}</div>
                    </div>
                `;
            }}

            const zonesContainer = document.getElementById("zones-container");
            if (!payload.zones.length) {{
                zonesContainer.innerHTML = '<div class="empty">No recent zones were detected.</div>';
            }} else {{
                zonesContainer.innerHTML = payload.zones
                    .slice()
                    .reverse()
                    .map(zone => `
                        <div class="list-item">
                            <strong>${{zone.type.toUpperCase()}} zone</strong>
                            <div class="muted">${{zone.timeframe}} | ${{zone.symbol}}</div>
                            <div>Start: ${{formatNumber(zone.start_price)}}</div>
                            <div>End: ${{formatNumber(zone.end_price)}}</div>
                        </div>
                    `)
                    .join("");
            }}
        }}

        function updateChartSeries(payload) {{
            candleSeries.setData(
                payload.candles.map(candle => ({{
                    time: candle.time,
                    open: candle.open,
                    high: candle.high,
                    low: candle.low,
                    close: candle.close,
                }}))
            );

            if (typeof candleSeries.setMarkers === "function") {{
                candleSeries.setMarkers(
                    payload.swings.map(swing => ({{
                        time: swing.time,
                        position: swing.type === "high" ? "aboveBar" : "belowBar",
                        color: swing.type === "high" ? "#f5b041" : "#5dade2",
                        shape: swing.type === "high" ? "arrowDown" : "arrowUp",
                        text: swing.label || swing.type,
                    }}))
                );
            }}

            chart.timeScale().fitContent();
        }}

        async function updateChart() {{
            const symbol = document.getElementById("symbol-input").value.trim();
            const interval = document.getElementById("interval-select").value;
            const source = document.getElementById("source-select").value;
            const limit = document.getElementById("limit-input").value;

            if (!symbol) {{
                alert("Please enter a symbol.");
                return;
            }}

            const response = await fetch(`/chart_data?symbol=${{symbol}}&interval=${{interval}}&limit=${{limit}}&source=${{source}}`);
            if (!response.ok) {{
                const errorPayload = await response.json();
                alert(errorPayload.detail || "Failed to load chart data.");
                return;
            }}
            const payload = await response.json();
            updateChartSeries(payload);
            updateInfo(payload);
        }}

        function debounce(fn, delay) {{
            let timeoutId = null;
            return (...args) => {{
                clearTimeout(timeoutId);
                timeoutId = setTimeout(() => fn(...args), delay);
            }};
        }}

        function toggleAutoRefresh() {{
            const button = document.getElementById("auto-refresh-button");
            if (autoRefreshHandle) {{
                clearInterval(autoRefreshHandle);
                autoRefreshHandle = null;
                button.textContent = "Start Auto Refresh";
                return;
            }}

            autoRefreshHandle = setInterval(() => {{
                updateChart().catch(error => console.error("Auto-refresh failed:", error));
            }}, 15000);
            button.textContent = "Stop Auto Refresh";
        }}

        const debouncedUpdate = debounce(() => {{
            updateChart().catch(error => console.error("Chart update failed:", error));
        }}, 350);

        document.getElementById("symbol-input").addEventListener("change", debouncedUpdate);
        document.getElementById("interval-select").addEventListener("change", debouncedUpdate);
        document.getElementById("source-select").addEventListener("change", debouncedUpdate);
        document.getElementById("limit-input").addEventListener("change", debouncedUpdate);

        updateChartSeries(initialPayload);
        updateInfo(initialPayload);

        window.addEventListener("resize", () => {{
            chart.applyOptions({{
                width: document.getElementById("chart-container").clientWidth,
                height: document.getElementById("chart-container").clientHeight,
            }});
        }});
    </script>
</body>
</html>
    """

    return HTMLResponse(content=html)


@app.get("/tradingview", response_class=HTMLResponse)
def get_tradingview_page(
    symbol: str = Query(default="FX:EURUSD", description="TradingView symbol."),
    interval: str = Query(default="15", description="TradingView interval."),
    backend_symbol: str = Query(default="EURUSD", description="Backend analysis symbol."),
    backend_interval: str = Query(default="15m", description="Backend candle interval."),
    source: DataSource = Query(default="auto", description="Backend OHLC data source."),
) -> HTMLResponse:
    """Render a TradingView widget page with your backend analysis beside it."""

    analysis_payload = get_frontend_data(
        symbol=backend_symbol,
        interval=backend_interval,
        limit=200,
        source=source,
    )
    analysis_json = json.dumps(analysis_payload)

    html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>TradingView Dashboard</title>
    <style>
        :root {{
            --bg: #0e151c;
            --panel: #16212c;
            --panel-2: #22303d;
            --text: #ebf1f6;
            --muted: #9fb1bf;
            --green: #2ecc71;
            --red: #ff6b6b;
            --amber: #f5b041;
            --border: #2b3b49;
        }}
        * {{
            box-sizing: border-box;
        }}
        body {{
            margin: 0;
            font-family: "Segoe UI", Tahoma, sans-serif;
            background:
                radial-gradient(circle at top left, rgba(52, 152, 219, 0.08), transparent 28%),
                radial-gradient(circle at bottom right, rgba(46, 204, 113, 0.08), transparent 22%),
                var(--bg);
            color: var(--text);
        }}
        .page {{
            max-width: 1480px;
            margin: 0 auto;
            padding: 24px;
        }}
        .hero {{
            display: flex;
            justify-content: space-between;
            gap: 20px;
            flex-wrap: wrap;
            margin-bottom: 18px;
            padding: 20px;
            border-radius: 18px;
            border: 1px solid var(--border);
            background: linear-gradient(135deg, rgba(34, 48, 61, 0.96), rgba(22, 33, 44, 0.96));
        }}
        .hero h1 {{
            margin: 0 0 6px;
            font-size: 30px;
        }}
        .hero p {{
            margin: 0;
            color: var(--muted);
            max-width: 760px;
        }}
        .controls {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 12px;
            margin-bottom: 18px;
        }}
        .control {{
            display: flex;
            flex-direction: column;
            gap: 6px;
            padding: 14px;
            border-radius: 14px;
            border: 1px solid var(--border);
            background: var(--panel);
        }}
        label {{
            color: var(--muted);
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }}
        input, select, button {{
            border-radius: 10px;
            border: 1px solid var(--border);
            font-size: 14px;
        }}
        input, select {{
            padding: 10px 12px;
            background: var(--panel-2);
            color: var(--text);
        }}
        button {{
            padding: 12px 16px;
            background: linear-gradient(135deg, #2ecc71, #27ae60);
            color: #08140d;
            font-weight: 700;
            cursor: pointer;
        }}
        .layout {{
            display: grid;
            grid-template-columns: minmax(0, 2.3fr) minmax(320px, 1fr);
            gap: 18px;
        }}
        .panel {{
            border-radius: 18px;
            border: 1px solid var(--border);
            background: rgba(22, 33, 44, 0.97);
            overflow: hidden;
        }}
        .panel-header {{
            padding: 16px 18px;
            border-bottom: 1px solid var(--border);
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 10px;
        }}
        .panel-title {{
            font-size: 18px;
            font-weight: 700;
        }}
        .trend {{
            padding: 6px 10px;
            border-radius: 999px;
            font-size: 12px;
            font-weight: 700;
            text-transform: uppercase;
        }}
        .trend.bullish {{
            background: rgba(46, 204, 113, 0.18);
            color: var(--green);
        }}
        .trend.bearish {{
            background: rgba(255, 107, 107, 0.16);
            color: var(--red);
        }}
        .trend.ranging {{
            background: rgba(245, 176, 65, 0.16);
            color: var(--amber);
        }}
        #tv-widget {{
            height: 760px;
        }}
        .sidebar {{
            display: grid;
            gap: 18px;
        }}
        .metrics {{
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 10px;
            padding: 16px;
        }}
        .metric {{
            padding: 14px;
            border-radius: 14px;
            background: rgba(255, 255, 255, 0.03);
            border: 1px solid rgba(255, 255, 255, 0.04);
        }}
        .metric-label {{
            color: var(--muted);
            font-size: 12px;
            margin-bottom: 6px;
        }}
        .metric-value {{
            font-size: 16px;
            font-weight: 700;
        }}
        .list {{
            padding: 16px;
            display: grid;
            gap: 10px;
            max-height: 280px;
            overflow: auto;
        }}
        .list-item {{
            padding: 12px;
            border-radius: 12px;
            background: rgba(255, 255, 255, 0.03);
            border: 1px solid rgba(255, 255, 255, 0.04);
        }}
        .list-item strong {{
            display: block;
            margin-bottom: 4px;
        }}
        .muted {{
            color: var(--muted);
        }}
        .hint {{
            margin-top: 6px;
            color: var(--muted);
            font-size: 12px;
        }}
        .empty {{
            padding: 16px;
            color: var(--muted);
        }}
        @media (max-width: 980px) {{
            .layout {{
                grid-template-columns: 1fr;
            }}
            #tv-widget {{
                height: 540px;
            }}
        }}
    </style>
</head>
<body>
    <div class="page">
        <section class="hero">
            <div>
                <h1>TradingView + Backend Intelligence</h1>
                <p>
                    TradingView handles the chart rendering while your own backend supplies
                    technical bias, news bias, final bias, confidence, setups, zones, and alerts.
                </p>
            </div>
            <div class="muted">
                Example symbols: FX:EURUSD, BINANCE:BTCUSDT, OANDA:XAUUSD, OANDA:USOIL, CAPITALCOM:US100
            </div>
        </section>

        <section class="controls">
            <div class="control">
                <label for="tv-symbol-input">TradingView Symbol</label>
                <input id="tv-symbol-input" list="tradingview-symbol-list" value="{symbol}" placeholder="BINANCE:BTCUSDT">
                <datalist id="tradingview-symbol-list">
                    <option value="BINANCE:BTCUSDT"></option>
                    <option value="BINANCE:ETHUSDT"></option>
                    <option value="BINANCE:SOLUSDT"></option>
                    <option value="BINANCE:BNBUSDT"></option>
                    <option value="BINANCE:XRPUSDT"></option>
                    <option value="BINANCE:ADAUSDT"></option>
                    <option value="BINANCE:DOGEUSDT"></option>
                    <option value="FX:EURUSD"></option>
                    <option value="FX:GBPUSD"></option>
                    <option value="FX:USDJPY"></option>
                    <option value="FX:AUDUSD"></option>
                    <option value="FX:USDCAD"></option>
                    <option value="OANDA:XAUUSD"></option>
                    <option value="OANDA:USOIL"></option>
                    <option value="CAPITALCOM:US500"></option>
                    <option value="CAPITALCOM:US100"></option>
                    <option value="CAPITALCOM:US30"></option>
                    <option value="CAPITALCOM:UK100"></option>
                    <option value="CAPITALCOM:GER40"></option>
                    <option value="CAPITALCOM:JPN225"></option>
                </datalist>
                <div class="hint">Suggestions appear while typing. Chart symbol used only by TradingView.</div>
            </div>
            <div class="control">
                <label for="tv-interval-select">TradingView Interval</label>
                <select id="tv-interval-select">
                    <option value="15">15m</option>
                    <option value="60">1h</option>
                    <option value="240">4h</option>
                    <option value="1D">1D</option>
                </select>
            </div>
            <div class="control">
                <label for="backend-symbol-input">Backend Symbol</label>
                <input id="backend-symbol-input" list="backend-symbol-list" value="{backend_symbol}" placeholder="BTCUSDT">
                <datalist id="backend-symbol-list">
                    <option value="BTCUSDT"></option>
                    <option value="ETHUSDT"></option>
                    <option value="SOLUSDT"></option>
                    <option value="BNBUSDT"></option>
                    <option value="XRPUSDT"></option>
                    <option value="ADAUSDT"></option>
                    <option value="DOGEUSDT"></option>
                    <option value="EURUSD"></option>
                    <option value="GBPUSD"></option>
                    <option value="USDJPY"></option>
                    <option value="AUDUSD"></option>
                    <option value="USDCAD"></option>
                    <option value="XAUUSD"></option>
                    <option value="USOIL"></option>
                    <option value="SPX"></option>
                    <option value="NAS100"></option>
                    <option value="DJI"></option>
                    <option value="GER40"></option>
                    <option value="UK100"></option>
                    <option value="JP225"></option>
                </datalist>
                <div class="hint">Suggestions appear while typing. Used for your own analysis endpoints.</div>
            </div>
            <div class="control">
                <label for="backend-interval-select">Backend Interval</label>
                <select id="backend-interval-select">
                    <option value="15m">15m</option>
                    <option value="1h">1h</option>
                    <option value="4h">4h</option>
                    <option value="1d">1d</option>
                </select>
            </div>
            <div class="control">
                <label for="backend-source-select">Backend Source</label>
                <select id="backend-source-select">
                    <option value="auto">auto</option>
                    <option value="binance">binance</option>
                    <option value="yfinance">yfinance</option>
                    <option value="stooq">stooq</option>
                    <option value="oanda">oanda</option>
                    <option value="alphavantage">alphavantage</option>
                    <option value="twelvedata">twelvedata</option>
                    <option value="mock">mock</option>
                </select>
            </div>
            <div class="control">
                <label>Actions</label>
                <button type="button" onclick="applyDashboard()">Update View</button>
            </div>
        </section>

        <section class="layout">
            <div class="panel">
                <div class="panel-header">
                    <div class="panel-title" id="chart-title">TradingView Chart</div>
                    <div id="trend-badge" class="trend">trend</div>
                </div>
                <div id="tv-widget"></div>
            </div>

            <div class="sidebar">
                <div class="panel">
                    <div class="panel-header">
                        <div class="panel-title">Backend Snapshot</div>
                    </div>
                    <div class="metrics">
                        <div class="metric">
                            <div class="metric-label">Symbol</div>
                            <div class="metric-value" id="metric-symbol">-</div>
                        </div>
                        <div class="metric">
                            <div class="metric-label">Interval</div>
                            <div class="metric-value" id="metric-interval">-</div>
                        </div>
                        <div class="metric">
                            <div class="metric-label">Source</div>
                            <div class="metric-value" id="metric-source">-</div>
                        </div>
                        <div class="metric">
                            <div class="metric-label">Latest Price</div>
                            <div class="metric-value" id="metric-price">-</div>
                        </div>
                        <div class="metric">
                            <div class="metric-label">Technical Bias</div>
                            <div class="metric-value" id="metric-technical-bias">-</div>
                        </div>
                        <div class="metric">
                            <div class="metric-label">News Bias</div>
                            <div class="metric-value" id="metric-news-bias">-</div>
                        </div>
                        <div class="metric">
                            <div class="metric-label">Final Bias</div>
                            <div class="metric-value" id="metric-final-bias">-</div>
                        </div>
                        <div class="metric">
                            <div class="metric-label">Confidence</div>
                            <div class="metric-value" id="metric-confidence">-</div>
                        </div>
                    </div>
                </div>

                <div class="panel">
                    <div class="panel-header">
                        <div class="panel-title">Trade Setup</div>
                    </div>
                    <div id="setup-container" class="list"></div>
                </div>

                <div class="panel">
                    <div class="panel-header">
                        <div class="panel-title">Detected Zones</div>
                    </div>
                    <div id="zones-container" class="list"></div>
                </div>

                <div class="panel">
                    <div class="panel-header">
                        <div class="panel-title">Latest Alerts</div>
                    </div>
                    <div id="alerts-container" class="list"></div>
                </div>
            </div>
        </section>
    </div>

    <script src="https://s3.tradingview.com/tv.js"></script>
    <script>
        const initialAnalysis = {analysis_json};
        document.getElementById("tv-interval-select").value = "{interval}";
        document.getElementById("backend-interval-select").value = "{backend_interval}";
        document.getElementById("backend-source-select").value = "{source}";

        let widget = null;
        let currentTvSymbol = null;
        let currentTvInterval = null;

        function formatNumber(value) {{
            return typeof value === "number" ? value.toFixed(4) : "-";
        }}

        function renderTradingViewWidget(force = false) {{
            const tvSymbol = document.getElementById("tv-symbol-input").value.trim();
            const tvInterval = document.getElementById("tv-interval-select").value;

            if (!force && widget && tvSymbol === currentTvSymbol && tvInterval === currentTvInterval) {{
                return;
            }}

            currentTvSymbol = tvSymbol;
            currentTvInterval = tvInterval;
            document.getElementById("chart-title").textContent = `TradingView: ${{tvSymbol}}`;
            document.getElementById("tv-widget").innerHTML = "";

            widget = new TradingView.widget({{
                autosize: true,
                symbol: tvSymbol,
                interval: tvInterval,
                timezone: "Etc/UTC",
                theme: "dark",
                style: "1",
                locale: "en",
                enable_publishing: false,
                hide_top_toolbar: false,
                allow_symbol_change: true,
                container_id: "tv-widget"
            }});
        }}

        function inferBackendSettingsFromTradingView(tvSymbol) {{
            const normalized = tvSymbol.trim().toUpperCase();
            if (!normalized.includes(":")) {{
                return null;
            }}

            const [provider, rawSymbol] = normalized.split(":", 2);
            const compactSymbol = rawSymbol.replace("/", "").replace("_", "");

            const providerDefaults = {{
                "BINANCE": "binance",
                "BYBIT": "binance",
                "KUCOIN": "binance",
                "COINBASE": "yfinance",
                "FX": "yfinance",
                "OANDA": "oanda",
                "FOREXCOM": "yfinance",
                "CAPITALCOM": "yfinance",
                "PEPPERSTONE": "yfinance",
                "BLACKBULL": "yfinance",
                "SAXO": "yfinance",
            }};

            const symbolMappings = {{
                "US500": "SPX",
                "SPX500": "SPX",
                "SPX": "SPX",
                "US100": "NAS100",
                "NAS100": "NAS100",
                "USTEC": "NAS100",
                "US30": "DJI",
                "DJI": "DJI",
                "GER40": "GER40",
                "DE40": "GER40",
                "DAX": "GER40",
                "UK100": "UK100",
                "JPN225": "JP225",
                "JP225": "JP225",
                "XAUUSD": "XAUUSD",
                "XAU/USD": "XAUUSD",
                "USOIL": "USOIL",
                "UKOIL": "BRENT",
                "WTI": "USOIL",
                "EURUSD": "EURUSD",
                "GBPUSD": "GBPUSD",
                "USDJPY": "USDJPY",
                "AUDUSD": "AUDUSD",
                "USDCAD": "USDCAD",
            }};

            const mappedSymbol = symbolMappings[rawSymbol] || symbolMappings[compactSymbol] || compactSymbol;
            let mappedSource = providerDefaults[provider] || "auto";

            if (compactSymbol.endsWith("USDT")) {{
                mappedSource = "binance";
            }}

            return {{
                symbol: mappedSymbol,
                source: mappedSource,
            }};
        }}

        function syncBackendInputsFromTradingView() {{
            const inferred = inferBackendSettingsFromTradingView(
                document.getElementById("tv-symbol-input").value
            );
            if (!inferred) {{
                return;
            }}

            document.getElementById("backend-symbol-input").value = inferred.symbol;
            document.getElementById("backend-source-select").value = inferred.source;
        }}

        function updateAnalysisPanel(payload) {{
            document.getElementById("metric-symbol").textContent = payload.symbol;
            document.getElementById("metric-interval").textContent = payload.interval;
            document.getElementById("metric-source").textContent = payload.source;
            document.getElementById("metric-price").textContent = formatNumber(payload.latest_price);
            document.getElementById("metric-technical-bias").textContent = (payload.technical_bias || "-").toUpperCase();
            document.getElementById("metric-news-bias").textContent = (payload.news_bias || "-").toUpperCase();
            document.getElementById("metric-final-bias").textContent = (payload.final_bias || "-").toUpperCase();
            document.getElementById("metric-confidence").textContent = `${{payload.confidence ?? 0}}%`;

            const trendBadge = document.getElementById("trend-badge");
            trendBadge.textContent = payload.final_bias || payload.trend;
            trendBadge.className = `trend ${{payload.final_bias || payload.trend || 'ranging'}}`;

            const setupContainer = document.getElementById("setup-container");
            if (!payload.setups || !payload.setups.length) {{
                setupContainer.innerHTML = '<div class="empty">No active setup at the current price.</div>';
            }} else {{
                setupContainer.innerHTML = payload.setups.map(setup => `
                    <div class="list-item">
                        <strong>${{setup.signal}} setup</strong>
                        <div class="muted">Zone type: ${{setup.zone_type}}</div>
                        <div>Entry: ${{formatNumber(setup.entry)}}</div>
                        <div>Stop Loss: ${{formatNumber(setup.stop_loss)}}</div>
                        <div>Take Profit: ${{formatNumber(setup.take_profit)}}</div>
                        <div>R:R: ${{setup.risk_reward_ratio}}</div>
                    </div>
                `).join("");
            }}

            const zonesContainer = document.getElementById("zones-container");
            if (!payload.zones.length) {{
                zonesContainer.innerHTML = '<div class="empty">No recent zones were detected.</div>';
            }} else {{
                zonesContainer.innerHTML = payload.zones
                    .slice()
                    .reverse()
                    .map(zone => `
                        <div class="list-item">
                            <strong>${{zone.type.toUpperCase()}} zone</strong>
                            <div class="muted">${{zone.timeframe}} | ${{zone.symbol}}</div>
                            <div>Start: ${{formatNumber(zone.start_price)}}</div>
                            <div>End: ${{formatNumber(zone.end_price)}}</div>
                        </div>
                    `)
                    .join("");
            }}

            const alertsContainer = document.getElementById("alerts-container");
            if (!payload.alerts || !payload.alerts.length) {{
                alertsContainer.innerHTML = '<div class="empty">No recent alerts.</div>';
            }} else {{
                alertsContainer.innerHTML = payload.alerts.map(alert => `
                    <div class="list-item">
                        <strong>${{alert.type.replace('_', ' ').toUpperCase()}}</strong>
                        <div class="muted">${{alert.message}}</div>
                    </div>
                `).join("");
            }}
        }}

        async function applyDashboard() {{
            const backendSymbol = document.getElementById("backend-symbol-input").value.trim();
            const backendInterval = document.getElementById("backend-interval-select").value;
            const backendSource = document.getElementById("backend-source-select").value;

            const response = await fetch(`/data?symbol=${{backendSymbol}}&interval=${{backendInterval}}&limit=200&source=${{backendSource}}`);
            if (!response.ok) {{
                const errorPayload = await response.json();
                alert(errorPayload.detail || "Failed to load backend analysis.");
                return;
            }}

            const payload = await response.json();
            updateAnalysisPanel(payload);
        }}

        renderTradingViewWidget(true);
        syncBackendInputsFromTradingView();
        updateAnalysisPanel(initialAnalysis);

        document.getElementById("tv-symbol-input").addEventListener("change", () => {{
            syncBackendInputsFromTradingView();
            renderTradingViewWidget(true);
            applyDashboard();
        }});

        document.getElementById("tv-interval-select").addEventListener("change", () => {{
            renderTradingViewWidget(true);
        }});

        setInterval(() => {{
            applyDashboard().catch(error => console.error("Dashboard refresh failed:", error));
        }}, 5000);
    </script>
</body>
</html>
    """

    return HTMLResponse(content=html)


@app.get("/health")
def healthcheck() -> dict:
    """Simple health endpoint."""

    return {"status": "ok"}


def _serialize_candle(candle: dict) -> dict:
    """Convert timestamps and numeric values into JSON-friendly primitives."""

    return {
        "time": candle["time"].isoformat(),
        "open": float(candle["open"]),
        "high": float(candle["high"]),
        "low": float(candle["low"]),
        "close": float(candle["close"]),
    }


def _serialize_chart_candle(candle: dict) -> dict:
    """Format candles for Lightweight Charts using Unix timestamps."""

    timestamp = candle["time"]
    return {
        "time": int(timestamp.timestamp()),
        "time_iso": timestamp.isoformat(),
        "open": float(candle["open"]),
        "high": float(candle["high"]),
        "low": float(candle["low"]),
        "close": float(candle["close"]),
    }


def _serialize_swing_for_chart(swing: dict) -> dict:
    """Convert swing timestamps to Unix seconds for the browser chart."""

    return {
        **swing,
        "time": int(pd.Timestamp(swing["time"]).timestamp()),
    }


def _load_default_news_provider() -> JsonEconomicCalendarProvider | None:
    """Load the default local economic calendar provider if the file exists."""

    if not DEFAULT_NEWS_CALENDAR_PATH.exists():
        return None
    return JsonEconomicCalendarProvider(DEFAULT_NEWS_CALENDAR_PATH)


def _build_frontend_alerts(
    symbol: str,
    setup_payload: dict,
    news_events: list,
    current_time: datetime,
) -> list[dict]:
    """Build a compact set of frontend alerts from current backend state."""

    alerts: list[dict] = []

    if setup_payload["setup"]:
        alerts.append(
            {
                "type": "setup",
                "message": f"New setup: {symbol.upper()} {setup_payload['setup']['signal']} at {setup_payload['setup']['entry']:.4f}",
            }
        )

    if setup_payload["final_bias"] != setup_payload["technical_bias"]:
        alerts.append(
            {
                "type": "bias_change",
                "message": (
                    f"Bias shift: technical {setup_payload['technical_bias'].upper()} "
                    f"vs news {setup_payload['news_bias'].upper()} -> final {setup_payload['final_bias'].upper()}"
                ),
            }
        )

    upcoming_alerts, released_alerts, sudden_alerts = build_news_alerts(
        symbol=symbol,
        events=news_events,
        current_time=current_time,
    )
    for alert in upcoming_alerts + released_alerts + sudden_alerts:
        alerts.append({"type": "news", "message": alert["message"]})

    return alerts[:10]

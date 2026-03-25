from __future__ import annotations

import json
from typing import Literal

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

from trading_bot.core.data_fetcher import DataFetchError, FetchConfig, fetch_ohlc
from trading_bot.core.market_structure import detect_market_structure
from trading_bot.core.strategy_engine import generate_trade_setup
from trading_bot.core.supply_demand import detect_supply_demand_zones


DataSource = Literal["auto", "binance", "mock", "yfinance", "oanda"]


app = FastAPI(
    title="Trading Intelligence System",
    version="0.2.0",
    description="Phase 2 backend with market bias, zones, setups, and chart visualization.",
)


@app.get("/")
def root() -> dict:
    """Simple entrypoint that confirms the API is running."""

    return {
        "message": "Trading Intelligence System API is running.",
        "endpoints": ["/bias", "/zones", "/setup", "/chart", "/chart_data", "/docs", "/health"],
    }


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
                <div class="chip">Supported sources: auto, binance, yfinance, oanda, mock</div>
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
                </datalist>
                <div class="hint">Examples: BTCUSDT, ETHUSDT, EURUSD, GBPUSD, XAUUSD</div>
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
                    OANDA needs <code>OANDA_ACCESS_TOKEN</code>. FOREX.com is not wired yet.
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
                            <strong>OANDA</strong>
                            <div class="muted">Requires an API token in your environment.</div>
                        </div>
                        <div class="list-item">
                            <strong>FOREX.com</strong>
                            <div class="muted">Official API access is account-gated, so it is not yet wired into this backend.</div>
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

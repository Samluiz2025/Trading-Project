# Trading Intelligence Platform

A rule-based trading platform for scanning, validating, journaling, and reviewing swing setups across a focused watchlist.

This project is built around a strict Smart Money Concepts workflow:

- `Daily` for directional bias
- `H1` for execution logic
- optional `M30` for refinement only
- no trade unless all required conditions are present

The current live focus is intentionally narrow:

- `ETHUSDT`
- `GBPUSD`
- `EURUSD`
- `BTCUSDT`
- `XAUUSD`
- `NAS100`
- `USDCHF`
- `USDJPY`

The platform includes:

- FastAPI backend
- TradingView-based dashboard
- real-time scanner
- Telegram alerts
- trade journal
- performance snapshot
- lightweight backtesting
- optional AI/news modules already present in the codebase

## Strategy Model

The live strategy path is centered on `SMC Continuation` inside a top-down framework:

1. `Daily bias` determines buy-only or sell-only context
2. `H1` must align with Daily
3. `H1` must confirm continuation with:
   - BOS and/or valid MSS
   - inducement
   - valid order block
   - valid FVG near the OB
4. Optional `M30` is used only to refine the same setup
5. If anything critical is missing, the system returns:

```json
{
  "status": "NO TRADE",
  "missing": ["..."],
  "message": "No valid setup available"
}
```

The current execution model uses a fixed `1:4` risk-reward target.

## Project Structure

```text
frontend/
  index.html
  style.css
  script.js

trading_bot/
  api/
    main.py
  core/
    strategy_smc.py
    confluence_engine.py
    market_monitor.py
    alert_system.py
    journal.py
    performance_tracker.py
    backtester.py
    data_fetcher.py
    news_engine.py
    ai_engine.py
  data/
    alerts.json
    trade_journal.json
    ai_dataset.csv
    ai_metrics.json
    ai_predictions.csv
    model.pkl

run_bot.py
simulate_symbol.py
requirements.txt
```

## Features

### Dashboard

The dashboard is designed to always render a useful state instead of hanging on loading.

It shows:

- TradingView chart
- watchlist sidebar for the approved pairs
- bias panel
- active setup
- setup map
- smart overlays
- alerts feed
- journal
- performance snapshot
- pair-specific news panel

Current dashboard behavior:

- loads analysis when opened
- refreshes when you change pair, source, or interval
- supports manual refresh
- does not continuously blink/reload by default

### Scanner

The scanner loops through the approved market list and sends alerts only for valid setups.

It can:

- scan your watchlist every few seconds
- log valid trades
- log rejected analyses
- update open trades when TP or SL is hit
- send alerts to console and Telegram

### Alerts

Alert delivery supports:

- console output
- dashboard feed
- Telegram

Typical setup alert payload:

```json
{
  "pair": "GBPUSD",
  "bias": "SELL",
  "entry": 1.3356,
  "sl": 1.3370,
  "tp": 1.3300,
  "confidence": "HIGH",
  "strategies": ["SMC Continuation", "M30 Refinement"],
  "confluences": [
    "Daily Bias",
    "H1 Structure",
    "BOS/MSS",
    "Inducement Confirmed",
    "Order Block",
    "FVG",
    "M30 Refinement Aligned"
  ],
  "timestamp": "2026-03-27T00:00:00+00:00"
}
```

### Journal and Performance

The system stores trade and analysis history in JSON so you can review:

- valid trades
- rejected trades
- missing conditions
- open/closed status
- RR achieved
- win rate
- profit factor

### Backtesting

The API includes a lightweight backtest route for replaying the current setup logic against historical candles.

## Requirements

Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

Current `requirements.txt`:

- `fastapi`
- `uvicorn`
- `pandas`
- `requests`
- `yfinance`
- `numpy`
- `scikit-learn`
- `joblib`

## Getting Started

### 1. Run the API / dashboard

From the project root:

```powershell
python -m uvicorn trading_bot.api.main:app --reload
```

Open:

- [http://127.0.0.1:8000/](http://127.0.0.1:8000/)

### 2. Run the scanner

In a second terminal:

```powershell
python run_bot.py --mode multi --universe all --source auto --poll-seconds 5
```

Recommended setup:

- Terminal 1: FastAPI/dashboard
- Terminal 2: scanner

## Telegram Alerts

Set your Telegram credentials in the same terminal where you run the scanner:

```powershell
$env:TELEGRAM_BOT_TOKEN="YOUR_TOKEN"
$env:TELEGRAM_CHAT_ID="YOUR_CHAT_ID"
python run_bot.py --mode multi --universe all --source auto --poll-seconds 5
```

If Telegram is configured correctly, the scanner should send:

- a startup/online message
- new setup alerts
- trade close alerts

## API Endpoints

### `GET /`

Serves the frontend dashboard.

### `GET /data`

Returns the active state for one symbol.

Example:

```text
/data?symbol=GBPUSD&interval=1h&source=yfinance
```

### `GET /status`

Simple health endpoint.

### `GET /journal`

Returns recent journal entries.

### `GET /performance`

Returns performance snapshot data.

### `GET /watchlist`

Returns current analysis summary for the approved 8-symbol watchlist.

### `GET /news`

Returns pair-specific market-moving news context.

Note:

- this route expects a local calendar file at `trading_bot/data/economic_calendar.json`
- if that file does not exist, the API returns a clean “not configured” message instead of failing

### `GET /backtest`

Runs a lightweight symbol backtest using the current strategy flow.

Example:

```text
/backtest?symbol=GBPUSD&source=yfinance
```

## Data Sources

The backend supports multiple sources, depending on asset class and local setup:

- `auto`
- `binance`
- `yfinance`
- `oanda`
- `alphavantage`
- `twelvedata`
- `stooq`
- `mock`

Practical default usage:

- `binance` for `BTCUSDT`, `ETHUSDT`
- `yfinance` for forex, gold, and indices

## Notes

- The live platform is intentionally strict. If required confluences are missing, it should return `NO TRADE`.
- The system is designed to analyze only the current approved watchlist, not every market.
- The dashboard and scanner are separate processes.
- If you close VS Code while using the integrated terminal, your running processes may stop. For longer runs, use external PowerShell or Windows Terminal windows.

## Example Commands

Run dashboard:

```powershell
python -m uvicorn trading_bot.api.main:app --reload
```

Run scanner:

```powershell
python run_bot.py --mode multi --universe all --source auto --poll-seconds 5
```

Run a quick symbol simulation:

```powershell
python simulate_symbol.py --symbol GBPUSD --mode test
```

## Roadmap Ideas

Planned/high-value next improvements:

- setup checklist panel showing satisfied vs missing conditions
- richer calendar-style journal filters
- smarter per-symbol source defaults inside the UI
- tighter setup staging for Telegram
- improved pair-specific news and macro context
- deeper backtest reporting by setup family

## Disclaimer

This software is for research, journaling, and educational use. It is not financial advice and does not guarantee profitable trading outcomes.

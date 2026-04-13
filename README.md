# Trading Intelligence Platform

A rule-based trading platform for scanning, validating, journaling, reviewing, and backtesting forex and gold setups.

The live system is built around a shared execution framework:

- `Daily` bias
- higher-timeframe location or liquidity
- lower-timeframe confirmation
- defined entry
- logical stop loss
- realistic take profit
- minimum acceptable risk-reward

## Live Setup Models

The current live scanner uses 3 setup families:

1. `Sweep Reversal`
2. `Trend Pullback Continuation`
3. `HTF Zone Reaction`

These models share the same execution standard:

- no mid-range entries
- confirmation required
- logical invalidation
- `1:3` preferred
- `1:2` minimum

## Main Features

- FastAPI backend
- TradingView-based dashboard
- live scanner
- Telegram alerts
- trade journal
- digital twin tracking
- challenge mode
- weekly outlook engine
- lightweight backtesting

## Project Structure

```text
frontend/
  index.html
  script.js
  style.css

trading_bot/
  api/
    main.py
  core/
    alert_system.py
    backtester.py
    confluence_engine.py
    data_fetcher.py
    digital_twin.py
    journal.py
    market_monitor.py
    monitor_state.py
    strategy_htf_zone.py
    strategy_pullback.py
    strategy_registry.py
    strategy_strict_liquidity.py
    weekly_outlook_engine.py
    weekly_outlook_job.py
    weekly_outlook_report.py
  utils/
    reset_monday_start.py
    run_weekly_outlook.py

run_bot.py
start_api.ps1
start_scanner.ps1
reset_monday_start.ps1
```

## Scanner Behavior

The live scanner currently focuses on:

- forex pairs
- `XAUUSD`

It evaluates all active setup families and selects the best valid result.

Telegram alert stages are now intentionally clearer:

- `VALID SETUP`
- `ENTRY ACTIVATED`
- `TRADE CLOSED`

The feed also supports:

- `WAIT FOR CONFIRMATION`

Very early zone-watch noise is suppressed from Telegram.

## Challenge Mode

Challenge mode is a stricter layer on top of the normal scanner.

It only allows:

- `A+` setups
- `1:3` RR or better
- maximum `3` trades

It also applies a final gate to reject trades that are:

- too extended
- too late in session
- too stretched after impulse

Challenge alerts are clearly labeled:

- `CHALLENGE VALID SETUP`
- `CHALLENGE ENTRY ACTIVATED`
- `CHALLENGE TRADE WIN/LOSS`

## Weekly Outlook

The project includes a weekly outlook engine that:

- reviews the previous week
- builds next-week directional bias
- marks zones and liquidity
- proposes swing and intraday plans
- saves JSON and markdown reports

## Backtesting

The API includes a lightweight backtest route for replaying the current rule set against historical candles.

## Requirements

Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

Typical packages used:

- `fastapi`
- `uvicorn`
- `pandas`
- `requests`
- `yfinance`
- `numpy`
- `scikit-learn`
- `joblib`

## Getting Started

### 1. Run the API

```powershell
powershell -ExecutionPolicy Bypass -File .\start_api.ps1
```

Then open:

- [http://127.0.0.1:8000/](http://127.0.0.1:8000/)

### 2. Run the normal scanner

In another terminal:

```powershell
$env:TELEGRAM_BOT_TOKEN="YOUR_TOKEN"
$env:TELEGRAM_CHAT_ID="YOUR_CHAT_ID"
Remove-Item Env:CHALLENGE_MODE -ErrorAction SilentlyContinue
Remove-Item Env:CHALLENGE_NAME -ErrorAction SilentlyContinue
Remove-Item Env:CHALLENGE_MAX_TRADES -ErrorAction SilentlyContinue
Remove-Item Env:CHALLENGE_RISK -ErrorAction SilentlyContinue
powershell -ExecutionPolicy Bypass -File .\start_scanner.ps1
```

### 3. Run challenge mode

In a separate terminal:

```powershell
$env:TELEGRAM_BOT_TOKEN="YOUR_TOKEN"
$env:TELEGRAM_CHAT_ID="YOUR_CHAT_ID"
$env:CHALLENGE_MODE="true"
$env:CHALLENGE_NAME="Weekly Challenge"
$env:CHALLENGE_MAX_TRADES="3"
$env:CHALLENGE_RISK="30"
powershell -ExecutionPolicy Bypass -File .\start_scanner.ps1
```

### 4. Monday reset

Before London if needed:

```powershell
Get-Process python | Stop-Process -Force
powershell -ExecutionPolicy Bypass -File .\reset_monday_start.ps1
```

## CLI Modes

Examples:

Run multi-strategy scanner:

```powershell
python run_bot.py --mode multi --universe all --source auto --poll-seconds 5
```

Run weekly outlook:

```powershell
python run_bot.py --mode weekly-outlook --source auto --timezone Europe/Vienna
```

Run validation snapshot:

```powershell
python run_bot.py --mode validation
```

Run calibration:

```powershell
python run_bot.py --mode calibrate
```

## API Endpoints

Main routes include:

- `GET /`
- `GET /data`
- `GET /status`
- `GET /journal`
- `GET /performance`
- `GET /watchlist`
- `GET /news`
- `GET /backtest`
- `GET /weekly-outlook/latest`
- `GET /weekly-outlook/run`

## Notes

- The scanner and dashboard are separate processes.
- Challenge mode is intentionally stricter than normal mode.
- Local runtime state is stored under `trading_bot/data/`.
- This repo now ignores generated runtime artifacts via `.gitignore`.

## Disclaimer

This software is for research, journaling, and educational use. It is not financial advice and does not guarantee profitable trading outcomes.

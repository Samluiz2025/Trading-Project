# Trading Intelligence Platform v2

A high-precision Smart Money Concepts trading platform with a multi-confluence scoring engine, ATR-adaptive levels, and quality-gated setup alerts.

## What Changed in v2 (Win Rate Improvements)

The previous system had a 12W/21L record (~36% win rate). The v2 engine addresses the root causes:

| Problem (v1) | Fix (v2) |
|---|---|
| Binary pass/fail – fired on minimum conditions | **Confluence scoring (0–100)** – only ≥72/100 fires |
| No H4 timeframe | **Daily → H4 → H1 → M15 cascade** (4-TF alignment) |
| Fixed-pip SL easily swept | **ATR-adaptive SL/TP** (1.5×ATR stop, 4.5×ATR target) |
| Fired during off-hours | **Session filter** – London open + NY open only |
| Fired in choppy/ranging markets | **ADX ≥ 22 gate** blocks sideways markets |
| No OB quality check | **Order Block scoring** – unmitigated OBs only |
| No FVG confluence | **FVG proximity filter** for sniper entries |
| Could re-fire same direction repeatedly | **4h cooldown** per pair+direction |
| No daily cap enforcement | **Max 5 setups/day** cap |
| Fixed 1:4 RR regardless of structure | **Minimum 1:3 RR gate** before any alert fires |

### Expected outcome
- Quality score gate (72/100) removes ~65% of low-probability setups
- 4-TF alignment requirement ensures only trend-following trades
- ATR stops prevent getting swept by normal volatility
- Session filter removes thin-market fakeouts

## Strategy Model

```
Daily bias (BULLISH/BEARISH)
  └─ H4 confirmation (EMA20/50 + structure)
       └─ H1 liquidity sweep (equal highs/lows taken)
            └─ M15 BOS after sweep
                 └─ Entry from OB or pullback
                      └─ Quality score ≥ 72/100 → ALERT
```

### Scoring breakdown (100 pts total)
| Confluence | Points |
|---|---|
| Daily bias aligned | 20 |
| H4 bias aligned | 10 |
| H1 market structure | 10 |
| Liquidity sweep confirmed | 15 |
| M15 Break of Structure | 15 |
| Unmitigated Order Block | 10 |
| FVG proximity | 5 |
| ADX ≥ 22 | 5 |
| RSI confluence | 5 |
| Valid session | 5 |

### Confidence labels
- **ELITE** (90+): All confluences present
- **HIGH** (80–89): Strong setup, minor gaps
- **MEDIUM** (72–79): Minimum threshold, valid but cautious
- **LOW** (<72): **NOT FIRED**

## Project Structure

```
frontend/
  index.html          ← Redesigned dark dashboard

trading_bot/
  api/
    main.py           ← FastAPI + new /confluence endpoint
  core/
    strategy_strict_liquidity.py  ← v2 scoring engine
    confluence_engine.py          ← Confluence map builder
    market_monitor.py             ← Scanner with daily cap
    data_fetcher.py               ← Multi-source + H4 support
    alert_system.py               ← Console + Telegram alerts
    journal.py                    ← Trade persistence
    performance_tracker.py        ← Win rate / profit factor

run_bot.py            ← Scanner entry point
```

## API Endpoints

| Endpoint | Description |
|---|---|
| `GET /` | Dashboard |
| `GET /data?symbol=GBPUSD` | Full analysis + setup for one symbol |
| `GET /confluence?symbol=GBPUSD` | Confluence map (10 factors with scores) |
| `GET /watchlist` | All 8 pairs summary |
| `GET /performance` | Win rate, profit factor, streak |
| `GET /journal` | Recent trade log |
| `GET /backtest?symbol=GBPUSD&lookback_days=30` | Walk-forward backtest |
| `GET /status` | Health check |

## Getting Started

```bash
# Install dependencies
pip install -r requirements.txt

# Run the API + dashboard
python -m uvicorn trading_bot.api.main:app --reload

# Run the scanner (separate terminal)
python run_bot.py --mode multi --source auto --poll-seconds 30

# With Telegram alerts
$env:TELEGRAM_BOT_TOKEN="YOUR_TOKEN"
$env:TELEGRAM_CHAT_ID="YOUR_CHAT_ID"
python run_bot.py --mode multi --source auto --poll-seconds 30
```

## Approved Watchlist

`ETHUSDT` `GBPUSD` `EURUSD` `BTCUSDT` `XAUUSD` `NAS100` `USDCHF` `USDJPY`

## Session Filter

Only fires during:
- **London Open**: 07:00–12:00 UTC
- **New York Open**: 12:00–20:00 UTC

Off-session setups are scored but not fired (saves 5 points, rarely reaches threshold).

## Requirements

```
fastapi
uvicorn
pandas
requests
yfinance
numpy
scikit-learn
joblib
```

## Disclaimer

For research, journaling, and educational use only. Not financial advice.

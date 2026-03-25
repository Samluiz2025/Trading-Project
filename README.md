# Trading Intelligence System

Phase 1 backend for:

- Historical OHLC data fetching
- Rule-based market structure detection
- Basic trend bias output

## Run the API

```bash
uvicorn trading_bot.api.main:app --reload
```

## Test the bias endpoint

```bash
curl "http://127.0.0.1:8000/bias?symbol=BTCUSDT&interval=1h&limit=200"
```

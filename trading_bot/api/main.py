from __future__ import annotations

from typing import Literal

from fastapi import FastAPI, Query

from trading_bot.core.data_fetcher import FetchConfig, fetch_ohlc
from trading_bot.core.market_structure import detect_market_structure
from trading_bot.core.strategy_engine import generate_trade_setup
from trading_bot.core.supply_demand import detect_supply_demand_zones


app = FastAPI(
    title="Trading Intelligence System",
    version="0.1.0",
    description="Phase 1 backend for OHLC retrieval and market bias detection.",
)


@app.get("/bias")
def get_bias(
    symbol: str = Query(default="BTCUSDT", description="Instrument symbol."),
    interval: str = Query(default="1h", description="Candle interval."),
    limit: int = Query(default=200, ge=20, le=1000, description="Number of candles."),
    source: Literal["auto", "binance", "mock"] = Query(default="auto", description="OHLC data source."),
) -> dict:
    candles = fetch_ohlc(
        FetchConfig(
            symbol=symbol,
            interval=interval,
            limit=limit,
            source=source,
        )
    )
    structure = detect_market_structure(candles)

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
    source: Literal["auto", "binance", "mock"] = Query(default="auto", description="OHLC data source."),
) -> dict:
    """Return recent supply and demand zones in a JSON-friendly structure."""

    candles = fetch_ohlc(
        FetchConfig(
            symbol=symbol,
            interval=interval,
            limit=limit,
            source=source,
        )
    )
    zones = detect_supply_demand_zones(candles, symbol=symbol, timeframe=interval)

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
    source: Literal["auto", "binance", "mock"] = Query(default="auto", description="OHLC data source."),
) -> dict:
    """
    Return a rule-based trade setup candidate.

    This endpoint is intentionally backend-focused so the same payload can feed
    alerts or automation in later phases.
    """

    candles = fetch_ohlc(
        FetchConfig(
            symbol=symbol,
            interval=interval,
            limit=limit,
            source=source,
        )
    )
    return generate_trade_setup(candles, symbol=symbol, timeframe=interval)


@app.get("/health")
def healthcheck() -> dict:
    return {"status": "ok"}


def _serialize_candle(candle: dict) -> dict:
    serialized = dict(candle)
    serialized["time"] = serialized["time"].isoformat()
    return serialized

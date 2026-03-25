from __future__ import annotations


MAJOR_FOREX_PAIRS = [
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "AUDUSD",
    "USDCAD",
    "USDCHF",
    "NZDUSD",
    "EURJPY",
    "GBPJPY",
]

MAJOR_INDICES = [
    "SPX",
    "NAS100",
    "DJI",
    "GER40",
    "UK100",
    "JP225",
]

MAJOR_CRYPTO = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
]


def get_instrument_universe(group: str = "all") -> list[str]:
    normalized = group.strip().lower()
    if normalized == "forex":
        return MAJOR_FOREX_PAIRS
    if normalized == "indices":
        return MAJOR_INDICES
    if normalized == "crypto":
        return MAJOR_CRYPTO
    return MAJOR_FOREX_PAIRS + MAJOR_INDICES + MAJOR_CRYPTO

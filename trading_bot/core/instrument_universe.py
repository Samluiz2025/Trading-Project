from __future__ import annotations


WATCHLIST_FOREX = [
    "EURUSD",
    "GBPUSD",
    "USDCHF",
    "USDJPY",
]

WATCHLIST_METALS = [
    "XAUUSD",
]

WATCHLIST_INDICES = [
    "NAS100",
]

WATCHLIST_CRYPTO = [
    "BTCUSDT",
    "ETHUSDT",
]

APPROVED_SYMBOLS = WATCHLIST_FOREX + WATCHLIST_METALS + WATCHLIST_INDICES + WATCHLIST_CRYPTO


def get_instrument_universe(group: str = "all") -> list[str]:
    normalized = group.strip().lower()
    if normalized == "forex":
        return WATCHLIST_FOREX
    if normalized == "metals":
        return WATCHLIST_METALS
    if normalized == "indices":
        return WATCHLIST_INDICES
    if normalized == "crypto":
        return WATCHLIST_CRYPTO
    return APPROVED_SYMBOLS.copy()


def is_supported_symbol(symbol: str) -> bool:
    return str(symbol or "").strip().upper() in APPROVED_SYMBOLS

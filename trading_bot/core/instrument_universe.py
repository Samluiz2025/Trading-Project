from __future__ import annotations


CORE_WATCHLIST_FOREX = [
    "EURUSD",
    "GBPUSD",
    "AUDUSD",
    "AUDCAD",
    "NZDUSD",
    "EURGBP",
    "EURNZD",
    "GBPAUD",
    "USDCHF",
    "USDJPY",
    "AUDJPY",
    "CHFJPY",
    "NZDJPY",
    "CADJPY",
    "GBPJPY",
    "GBPCHF",
]

ALL_FOREX_PAIRS = [
    "EURUSD",
    "GBPUSD",
    "AUDUSD",
    "NZDUSD",
    "USDCAD",
    "USDCHF",
    "USDJPY",
    "EURGBP",
    "EURJPY",
    "EURCHF",
    "EURAUD",
    "EURNZD",
    "EURCAD",
    "GBPJPY",
    "GBPCHF",
    "GBPAUD",
    "GBPNZD",
    "GBPCAD",
    "AUDJPY",
    "AUDNZD",
    "AUDCAD",
    "AUDCHF",
    "NZDJPY",
    "NZDCAD",
    "NZDCHF",
    "CADJPY",
    "CADCHF",
    "CHFJPY",
]

WATCHLIST_METALS = [
    "XAUUSD",
]

WATCHLIST_INDICES = [
    "NAS100",
    "SP500",
    "US30",
    "GER40",
    "UK100",
    "JP225",
]

WATCHLIST_CRYPTO = [
    "BTCUSDT",
    "ETHUSDT",
]

APPROVED_SYMBOLS = ALL_FOREX_PAIRS + WATCHLIST_METALS + WATCHLIST_INDICES + WATCHLIST_CRYPTO


def get_instrument_universe(group: str = "all") -> list[str]:
    normalized = group.strip().lower()
    if normalized == "watchlist":
        return CORE_WATCHLIST_FOREX + WATCHLIST_METALS + WATCHLIST_INDICES + WATCHLIST_CRYPTO
    if normalized == "forex":
        return ALL_FOREX_PAIRS
    if normalized == "metals":
        return WATCHLIST_METALS
    if normalized == "indices":
        return WATCHLIST_INDICES
    if normalized == "crypto":
        return WATCHLIST_CRYPTO
    return APPROVED_SYMBOLS.copy()


def is_supported_symbol(symbol: str) -> bool:
    return str(symbol or "").strip().upper() in APPROVED_SYMBOLS

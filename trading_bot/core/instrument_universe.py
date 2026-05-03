from __future__ import annotations


SYMBOL_ALIASES = {
    "JP225": "JPN225",
}


def normalize_instrument_symbol(symbol: str) -> str:
    normalized = str(symbol or "").strip().upper()
    return SYMBOL_ALIASES.get(normalized, normalized)


CORE_WATCHLIST_FOREX = [
    "EURUSD",
    "GBPUSD",
    "AUDUSD",
    "AUDCAD",
    "NZDUSD",
    "EURGBP",
    "GBPAUD",
    "GBPNZD",
    "USDCHF",
    "USDJPY",
    "AUDJPY",
    "CHFJPY",
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
    "EURCHF",
    "EURAUD",
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
    "CADJPY",
    "CADCHF",
    "CHFJPY",
]

WATCHLIST_METALS = [
    "XAUUSD",
    "USOIL",
]

WATCHLIST_INDICES = [
    "NAS100",
    "SP500",
    "US30",
    "GER40",
    "UK100",
    "JPN225",
]

WATCHLIST_CRYPTO = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
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
    return normalize_instrument_symbol(symbol) in APPROVED_SYMBOLS

from __future__ import annotations

PRIMARY_STRATEGY = "Sweep Reversal"
PULLBACK_STRATEGY = "Trend Pullback Continuation"
HTF_ZONE_STRATEGY = "HTF Zone Reaction"
ALL_LIVE_STRATEGIES = "All Live Strategies"
LIVE_STRATEGIES = (PRIMARY_STRATEGY, PULLBACK_STRATEGY, HTF_ZONE_STRATEGY)
STRATEGY_RESULT_KEYS = {
    PRIMARY_STRATEGY: "strict_liquidity",
    PULLBACK_STRATEGY: "pullback",
    HTF_ZONE_STRATEGY: "htf_zone",
}
LIVE_SPECIAL_SYMBOLS = {
    "XAUUSD",
    "USOIL",
    "NAS100",
    "SP500",
    "US30",
    "GER40",
    "UK100",
    "JPN225",
    "JP225",
}


def supports_live_symbol(symbol: object) -> bool:
    normalized = str(symbol or "").strip().upper().replace("/", "").replace("_", "")
    if normalized in LIVE_SPECIAL_SYMBOLS:
        return True
    if normalized.endswith("USDT") and len(normalized) >= 7 and normalized.isalpha():
        return True
    return len(normalized) == 6 and normalized.isalpha()


def normalize_strategy_scope(value: object) -> str:
    normalized = str(value or "").strip()
    if normalized in {ALL_LIVE_STRATEGIES, *LIVE_STRATEGIES}:
        return normalized
    return ALL_LIVE_STRATEGIES


def resolve_strategy_scope(value: object) -> tuple[str, ...]:
    normalized = normalize_strategy_scope(value)
    if normalized == ALL_LIVE_STRATEGIES:
        return LIVE_STRATEGIES
    return (normalized,)


def is_live_strategy(strategy_name: object) -> bool:
    return strategy_matches_scope(strategy_name, ALL_LIVE_STRATEGIES)


def strategy_matches_scope(strategy_name: object, scope: object) -> bool:
    strategy_value = str(strategy_name or "").strip()
    if not strategy_value:
        return False

    allowed = set(resolve_strategy_scope(scope))
    if strategy_value in allowed:
        return True

    parts = {part.strip() for part in strategy_value.split("+") if part.strip()}
    return bool(parts & allowed)


def strategy_result_key(strategy_name: object) -> str | None:
    return STRATEGY_RESULT_KEYS.get(str(strategy_name or "").strip())

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol


BiasLabel = str


@dataclass(frozen=True)
class EconomicEvent:
    """Normalized economic calendar event used by the news engine."""

    event_name: str
    currency: str
    impact: str
    time: datetime
    category: str = "calendar"
    is_scheduled: bool = True
    market_moving: bool = False
    sentiment_hint: str | None = None
    impact_score: int = 0
    forecast: float | None = None
    previous: float | None = None
    actual: float | None = None


@dataclass(frozen=True)
class CurrencyBiasSignal:
    """Bias derived from one or more high-impact events for a currency."""

    currency: str
    bias: BiasLabel
    strength: int
    driver: str
    event_time: str


@dataclass(frozen=True)
class BiasDecision:
    """Final bias decision after comparing technical and news biases."""

    technical_bias: BiasLabel
    news_bias: BiasLabel
    final_bias: BiasLabel
    confidence: int


class EconomicCalendarProvider(Protocol):
    """Provider interface for economic calendar sources."""

    def fetch_events(
        self,
        currencies: list[str],
        start_time: datetime,
        end_time: datetime,
    ) -> list[EconomicEvent]:
        """Return economic calendar events within the requested time window."""


class JsonEconomicCalendarProvider:
    """
    File-backed provider for local development and future feed replacement.

    The JSON file should contain a list of objects with:
    event_name, currency, impact, time, forecast, previous, actual
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def fetch_events(
        self,
        currencies: list[str],
        start_time: datetime,
        end_time: datetime,
    ) -> list[EconomicEvent]:
        if not self.path.exists():
            return []

        payload = json.loads(self.path.read_text(encoding="utf-8"))
        currency_filter = {currency.upper() for currency in currencies}

        events: list[EconomicEvent] = []
        for item in payload:
            event = EconomicEvent(
                event_name=item["event_name"],
                currency=item["currency"].upper(),
                impact=item["impact"].lower(),
                time=_parse_datetime(item["time"]),
                category=item.get("category", "calendar"),
                is_scheduled=bool(item.get("is_scheduled", True)),
                market_moving=bool(item.get("market_moving", False)),
                sentiment_hint=item.get("sentiment_hint"),
                impact_score=int(item.get("impact_score", _default_impact_score(item.get("impact", "low")))),
                forecast=_coerce_float(item.get("forecast")),
                previous=_coerce_float(item.get("previous")),
                actual=_coerce_float(item.get("actual")),
            )
            if event.currency not in currency_filter:
                continue
            if not (start_time <= event.time <= end_time):
                continue
            events.append(event)

        events.sort(key=lambda event: event.time)
        return events


def fetch_market_moving_events(
    provider: EconomicCalendarProvider | None,
    currencies: list[str],
    current_time: datetime | None = None,
    lookback_minutes: int = 60,
    lookahead_hours: int = 24,
) -> list[EconomicEvent]:
    """
    Fetch events that are likely to move the market.

    This includes:
    - high-impact scheduled events
    - events explicitly marked as market-moving
    - sudden/unscheduled headlines with enough impact score
    """

    if provider is None:
        return []

    active_time = current_time or datetime.now(UTC)
    start_time = active_time - timedelta(minutes=lookback_minutes)
    end_time = active_time + timedelta(hours=lookahead_hours)

    events = provider.fetch_events(
        currencies=currencies,
        start_time=start_time,
        end_time=end_time,
    )
    return [event for event in events if is_market_moving_event(event)]


def fetch_high_impact_events(
    provider: EconomicCalendarProvider | None,
    currencies: list[str],
    current_time: datetime | None = None,
    lookback_minutes: int = 60,
    lookahead_hours: int = 24,
) -> list[EconomicEvent]:
    """Backward-compatible alias for the older high-impact-only entrypoint."""

    return fetch_market_moving_events(
        provider=provider,
        currencies=currencies,
        current_time=current_time,
        lookback_minutes=lookback_minutes,
        lookahead_hours=lookahead_hours,
    )


def derive_news_bias(
    currencies: list[str],
    events: list[EconomicEvent],
    current_time: datetime | None = None,
) -> dict[str, CurrencyBiasSignal]:
    """
    Derive a bias per currency from scheduled events and sudden news.

    Released events take priority over pre-news expectations because actual data
    is stronger than forecast-based positioning.
    """

    active_time = current_time or datetime.now(UTC)
    bias_by_currency: dict[str, CurrencyBiasSignal] = {}

    for currency in currencies:
        relevant_events = [event for event in events if event.currency == currency.upper()]
        if not relevant_events:
            continue

        released_events = [event for event in relevant_events if event.actual is not None and event.time <= active_time]
        sudden_events = [
            event for event in relevant_events
            if not event.is_scheduled and event.time <= active_time
        ]
        upcoming_events = [event for event in relevant_events if event.actual is None and event.time >= active_time]

        signal: CurrencyBiasSignal | None = None
        if sudden_events:
            signal = _derive_sudden_news_bias_signal(max(sudden_events, key=lambda event: event.time))
        elif released_events:
            signal = _derive_post_news_bias_signal(max(released_events, key=lambda event: event.time))
        elif upcoming_events:
            signal = _derive_pre_news_bias_signal(min(upcoming_events, key=lambda event: event.time))

        if signal is not None:
            bias_by_currency[currency.upper()] = signal

    return bias_by_currency


def get_pair_news_bias(
    symbol: str,
    bias_by_currency: dict[str, CurrencyBiasSignal],
) -> BiasLabel:
    """Translate currency-level signals into a pair-level directional bias."""

    base_currency, quote_currency = split_symbol_currencies(symbol)
    base_signal = bias_by_currency.get(base_currency)
    quote_signal = bias_by_currency.get(quote_currency)

    if base_signal and quote_signal:
        base_score = _bias_to_score(base_signal.bias)
        quote_score = _bias_to_score(quote_signal.bias)
        return _score_to_bias(base_score - quote_score)

    if base_signal:
        return _score_to_bias(_bias_to_score(base_signal.bias))

    if quote_signal:
        return _score_to_bias(-_bias_to_score(quote_signal.bias))

    return "neutral"


def combine_biases(
    technical_bias: BiasLabel,
    news_bias: BiasLabel,
) -> BiasDecision:
    """
    Merge technical and news biases into a final decision and confidence score.

    Rules:
    - If news conflicts with technical bias, news overrides.
    - If they align, confidence increases.
    - Neutral news leaves technical bias unchanged.
    """

    technical_direction = _directional_bias(technical_bias)
    news_direction = _directional_bias(news_bias)

    confidence = 55
    final_bias = technical_bias

    if technical_direction == "neutral" and news_direction != "neutral":
        final_bias = news_bias
        confidence = 65 if _is_strong_bias(news_bias) else 60
    elif news_direction == "neutral":
        final_bias = technical_bias
        confidence = 60 if technical_direction != "neutral" else 50
    elif technical_direction == news_direction:
        final_bias = technical_bias if not _is_strong_bias(news_bias) else news_bias
        confidence = 85 if _is_strong_bias(news_bias) else 75
    else:
        final_bias = news_bias
        confidence = 80 if _is_strong_bias(news_bias) else 70

    return BiasDecision(
        technical_bias=technical_bias,
        news_bias=news_bias,
        final_bias=final_bias,
        confidence=max(0, min(100, confidence)),
    )


def build_news_alerts(
    symbol: str,
    events: list[EconomicEvent],
    current_time: datetime | None = None,
    pre_alert_window_minutes: int = 15,
) -> tuple[list[dict], list[dict]]:
    """
    Create normalized pre-news, release, and sudden-news alert candidates for one symbol.

    Returns three lists:
    - upcoming alerts
    - released alerts
    - sudden-news alerts
    """

    active_time = current_time or datetime.now(UTC)
    relevant_currencies = set(split_symbol_currencies(symbol))

    upcoming_alerts: list[dict] = []
    released_alerts: list[dict] = []
    sudden_alerts: list[dict] = []

    for event in events:
        if event.currency not in relevant_currencies:
            continue

        minutes_to_event = (event.time - active_time).total_seconds() / 60
        if event.is_scheduled and 0 <= minutes_to_event <= pre_alert_window_minutes:
            upcoming_alerts.append(
                {
                    "type": "news_approaching",
                    "key": _build_event_key(event),
                    "message": (
                        f"[ALERT] {symbol.upper()} Market-moving news approaching in "
                        f"{int(round(minutes_to_event))} min: {event.currency} {event.event_name}"
                    ),
                }
            )

        if event.actual is not None and abs((active_time - event.time).total_seconds()) <= 15 * 60:
            released_alerts.append(
                {
                    "type": "news_released",
                    "key": _build_event_key(event),
                    "message": (
                        f"[ALERT] {symbol.upper()} News released: {event.currency} {event.event_name} "
                        f"(actual={_format_value(event.actual)}, forecast={_format_value(event.forecast)})"
                    ),
                }
            )

        if not event.is_scheduled and abs((active_time - event.time).total_seconds()) <= 15 * 60:
            sudden_alerts.append(
                {
                    "type": "sudden_news",
                    "key": _build_event_key(event),
                    "message": (
                        f"[ALERT] {symbol.upper()} Sudden market-moving news: "
                        f"{event.currency} {event.event_name}"
                    ),
                }
            )

    return upcoming_alerts, released_alerts, sudden_alerts


def split_symbol_currencies(symbol: str) -> tuple[str, str]:
    """Infer base and quote currencies from common FX and crypto symbol formats."""

    cleaned = symbol.strip().upper().replace("/", "").replace("_", "").replace("-", "")
    special_mappings = {
        "XAUUSD": ("XAU", "USD"),
        "XAGUSD": ("XAG", "USD"),
        "USOIL": ("USOIL", "USD"),
        "UKOIL": ("UKOIL", "USD"),
        "BRENT": ("BRENT", "USD"),
        "SPX": ("SPX", "USD"),
        "NAS100": ("NAS100", "USD"),
        "DJI": ("DJI", "USD"),
        "GER40": ("GER40", "EUR"),
        "UK100": ("UK100", "GBP"),
        "JP225": ("JP225", "JPY"),
    }
    if cleaned in special_mappings:
        return special_mappings[cleaned]

    quote_candidates = ("USDT", "USDC", "USD", "JPY", "EUR", "GBP", "AUD", "CAD", "CHF", "NZD")
    for quote_currency in quote_candidates:
        if cleaned.endswith(quote_currency) and len(cleaned) > len(quote_currency):
            return cleaned[: -len(quote_currency)], quote_currency

    if len(cleaned) >= 6:
        return cleaned[:3], cleaned[3:6]

    return cleaned, "USD"


def _derive_pre_news_bias_signal(event: EconomicEvent) -> CurrencyBiasSignal | None:
    if not event.is_scheduled and event.sentiment_hint:
        return CurrencyBiasSignal(
            currency=event.currency,
            bias=_normalize_bias_label(event.sentiment_hint),
            strength=2 if _is_strong_bias(event.sentiment_hint) else 1,
            driver=event.event_name,
            event_time=event.time.isoformat(),
        )

    if event.forecast is None or event.previous is None:
        return None

    if event.forecast > event.previous:
        bias = "bullish"
        strength = 1
    elif event.forecast < event.previous:
        bias = "bearish"
        strength = 1
    else:
        bias = "neutral"
        strength = 0

    return CurrencyBiasSignal(
        currency=event.currency,
        bias=bias,
        strength=strength,
        driver=event.event_name,
        event_time=event.time.isoformat(),
    )


def _derive_post_news_bias_signal(event: EconomicEvent) -> CurrencyBiasSignal | None:
    if event.actual is None or event.forecast is None:
        return None

    delta = event.actual - event.forecast
    threshold = _reaction_threshold(event.forecast)

    if delta >= threshold * 2:
        bias = "strong bullish"
        strength = 2
    elif delta >= threshold:
        bias = "bullish"
        strength = 1
    elif delta <= -(threshold * 2):
        bias = "strong bearish"
        strength = 2
    elif delta <= -threshold:
        bias = "bearish"
        strength = 1
    else:
        bias = "neutral"
        strength = 0

    return CurrencyBiasSignal(
        currency=event.currency,
        bias=bias,
        strength=strength,
        driver=event.event_name,
        event_time=event.time.isoformat(),
    )


def _derive_sudden_news_bias_signal(event: EconomicEvent) -> CurrencyBiasSignal | None:
    """Use sentiment hints or impact metadata for unscheduled news."""

    if event.sentiment_hint:
        normalized_bias = _normalize_bias_label(event.sentiment_hint)
        strength = 2 if _is_strong_bias(normalized_bias) or event.impact_score >= 4 else 1
        return CurrencyBiasSignal(
            currency=event.currency,
            bias=normalized_bias,
            strength=strength,
            driver=event.event_name,
            event_time=event.time.isoformat(),
        )

    if event.impact_score >= 4:
        return CurrencyBiasSignal(
            currency=event.currency,
            bias="strong bullish" if event.market_moving else "neutral",
            strength=2 if event.market_moving else 0,
            driver=event.event_name,
            event_time=event.time.isoformat(),
        )

    return None


def _reaction_threshold(reference_value: float) -> float:
    magnitude = abs(reference_value) if reference_value is not None else 0
    return max(magnitude * 0.05, 0.01)


def _bias_to_score(bias: BiasLabel) -> int:
    mapping = {
        "strong bullish": 2,
        "bullish": 1,
        "neutral": 0,
        "fakeout": 0,
        "bearish": -1,
        "strong bearish": -2,
        "ranging": 0,
    }
    return mapping.get(bias, 0)


def _score_to_bias(score: int) -> BiasLabel:
    if score >= 2:
        return "strong bullish"
    if score == 1:
        return "bullish"
    if score <= -2:
        return "strong bearish"
    if score == -1:
        return "bearish"
    return "neutral"


def _directional_bias(bias: BiasLabel) -> BiasLabel:
    if "bullish" in bias:
        return "bullish"
    if "bearish" in bias:
        return "bearish"
    return "neutral"


def _is_strong_bias(bias: BiasLabel) -> bool:
    return bias.startswith("strong ")


def is_market_moving_event(event: EconomicEvent) -> bool:
    """Return True when the event is important enough to affect bias/alerts."""

    if event.market_moving:
        return True
    if event.impact == "high":
        return True
    if not event.is_scheduled and event.impact_score >= 3:
        return True
    if event.impact_score >= 4:
        return True
    return False


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(UTC)


def _coerce_float(value: object) -> float | None:
    if value in {None, ""}:
        return None
    return float(value)


def _build_event_key(event: EconomicEvent) -> str:
    return "|".join([event.currency, event.event_name, event.time.isoformat()])


def _format_value(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.4f}"


def _default_impact_score(impact: str) -> int:
    mapping = {
        "low": 1,
        "medium": 2,
        "med": 2,
        "high": 4,
    }
    return mapping.get(str(impact).lower(), 0)


def _normalize_bias_label(value: str) -> str:
    normalized = value.strip().lower()
    aliases = {
        "bullish": "bullish",
        "strong bullish": "strong bullish",
        "very bullish": "strong bullish",
        "bearish": "bearish",
        "strong bearish": "strong bearish",
        "very bearish": "strong bearish",
        "neutral": "neutral",
        "fakeout": "fakeout",
    }
    return aliases.get(normalized, "neutral")

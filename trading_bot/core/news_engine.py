from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol
from xml.etree import ElementTree

import requests


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

        payload = json.loads(self.path.read_text(encoding="utf-8-sig"))
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


class TradingEconomicsCalendarProvider:
    """
    Live economic calendar provider backed by Trading Economics.

    Credentials can be supplied as:
    - TRADING_ECONOMICS_API_KEY
    - or TRADING_ECONOMICS_CLIENT + TRADING_ECONOMICS_SECRET
    """

    def __init__(self, api_key: str | None = None, client: str | None = None, secret: str | None = None) -> None:
        self.api_key = api_key or os.getenv("TRADING_ECONOMICS_API_KEY")
        self.client = client or os.getenv("TRADING_ECONOMICS_CLIENT")
        self.secret = secret or os.getenv("TRADING_ECONOMICS_SECRET")

    def is_configured(self) -> bool:
        return bool(self.api_key or (self.client and self.secret))

    def fetch_events(
        self,
        currencies: list[str],
        start_time: datetime,
        end_time: datetime,
    ) -> list[EconomicEvent]:
        if not self.is_configured():
            return []

        auth_value = self.api_key if self.api_key else f"{self.client}:{self.secret}"
        try:
            response = requests.get(
                "https://api.tradingeconomics.com/calendar",
                params={"c": auth_value, "f": "json"},
                timeout=12,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException:
            return []

        currency_filter = {currency.upper() for currency in currencies}
        events: list[EconomicEvent] = []
        for item in payload:
            raw_currency = str(item.get("Currency") or item.get("currency") or "").upper()
            if raw_currency not in currency_filter:
                continue

            raw_date = item.get("Date") or item.get("date")
            if not raw_date:
                continue
            try:
                event_time = _parse_provider_datetime(str(raw_date))
            except ValueError:
                continue
            if not (start_time <= event_time <= end_time):
                continue

            impact_value = str(item.get("Importance") or item.get("importance") or item.get("Impact") or "").lower()
            impact = "high" if "3" in impact_value or "high" in impact_value else "medium" if "2" in impact_value or "medium" in impact_value else "low"
            events.append(
                EconomicEvent(
                    event_name=str(item.get("Event") or item.get("event") or item.get("Category") or "Economic event"),
                    currency=raw_currency,
                    impact=impact,
                    time=event_time,
                    category=str(item.get("Category") or item.get("category") or "calendar"),
                    is_scheduled=True,
                    market_moving=impact in {"high", "medium"},
                    impact_score=_default_impact_score(impact),
                    forecast=_coerce_float(item.get("Forecast") or item.get("forecast")),
                    previous=_coerce_float(item.get("Previous") or item.get("previous")),
                    actual=_coerce_float(item.get("Actual") or item.get("actual")),
                )
            )

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


def rank_events_for_symbol(symbol: str, events: list[EconomicEvent]) -> list[dict]:
    relevant_currencies = set(split_symbol_currencies(symbol))
    ranked: list[dict] = []
    for event in events:
        relevance = event.impact_score
        if event.currency in relevant_currencies:
            relevance += 5
        if event.market_moving:
            relevance += 4
        if not event.is_scheduled:
            relevance += 3
        ranked.append(
            {
                "event_name": event.event_name,
                "currency": event.currency,
                "impact": event.impact,
                "time": event.time.isoformat(),
                "forecast": event.forecast,
                "previous": event.previous,
                "actual": event.actual,
                "market_moving": event.market_moving,
                "is_scheduled": event.is_scheduled,
                "impact_score": event.impact_score,
                "relevance_score": relevance,
            }
        )
    ranked.sort(key=lambda item: (-item["relevance_score"], item["time"]))
    return ranked


def get_news_lock(symbol: str, events: list[EconomicEvent], current_time: datetime | None = None, window_minutes: int = 30) -> dict:
    active_time = current_time or datetime.now(UTC)
    relevant_currencies = set(split_symbol_currencies(symbol))
    locked_by: list[dict] = []
    for event in events:
        if event.currency not in relevant_currencies:
            continue
        if event.impact not in {"high", "medium"} and not event.market_moving:
            continue
        minutes_to_event = abs((event.time - active_time).total_seconds()) / 60
        if minutes_to_event <= window_minutes:
            locked_by.append(
                {
                    "event_name": event.event_name,
                    "currency": event.currency,
                    "time": event.time.isoformat(),
                    "minutes_from_now": round(minutes_to_event, 1),
                }
            )
    return {
        "locked": bool(locked_by),
        "window_minutes": window_minutes,
        "events": locked_by,
    }


def load_symbol_news_context(
    symbol: str,
    *,
    calendar_path: str | Path | None = None,
    current_time: datetime | None = None,
) -> dict:
    live_provider = TradingEconomicsCalendarProvider()
    default_path = Path(__file__).resolve().parents[1] / "data" / "economic_calendar.json"
    active_path = Path(calendar_path) if calendar_path is not None else default_path
    if live_provider.is_configured():
        provider: EconomicCalendarProvider | None = live_provider
    elif active_path.exists():
        provider = JsonEconomicCalendarProvider(active_path)
    else:
        provider = None

    if provider is None:
        return {
            "configured": False,
            "events": [],
            "ranked_events": [],
            "pair_news_bias": "neutral",
            "news_lock": {"locked": False, "events": [], "window_minutes": 30},
            "headlines": fetch_live_headlines(symbol),
            "provider": "headlines_only",
        }

    currencies = list(split_symbol_currencies(symbol))
    events = fetch_market_moving_events(provider=provider, currencies=currencies, current_time=current_time)
    bias_by_currency = derive_news_bias(currencies=currencies, events=events, current_time=current_time)
    return {
        "configured": True,
        "events": events,
        "ranked_events": rank_events_for_symbol(symbol, events),
        "pair_news_bias": get_pair_news_bias(symbol, bias_by_currency),
        "news_lock": get_news_lock(symbol, events, current_time=current_time),
        "headlines": fetch_live_headlines(symbol),
        "provider": "tradingeconomics" if isinstance(provider, TradingEconomicsCalendarProvider) else "local_json",
    }


def fetch_live_headlines(symbol: str, limit: int = 8) -> list[dict]:
    query = _headline_query(symbol)
    url = f"https://news.google.com/rss/search?q={query}"
    try:
        response = requests.get(url, timeout=8)
        response.raise_for_status()
    except requests.RequestException:
        return []

    try:
        root = ElementTree.fromstring(response.text)
    except ElementTree.ParseError:
        return []

    items: list[dict] = []
    for item in root.findall(".//item")[:limit]:
        items.append(
            {
                "title": _safe_xml_text(item.find("title")),
                "link": _safe_xml_text(item.find("link")),
                "published": _safe_xml_text(item.find("pubDate")),
                "source": _safe_xml_text(item.find("source")),
                "relevance_score": _headline_relevance(symbol, _safe_xml_text(item.find("title"))),
            }
        )
    items.sort(key=lambda headline: -headline["relevance_score"])
    return items


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


def _parse_provider_datetime(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        parsed = datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
        parsed = parsed.replace(tzinfo=UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
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


def _headline_query(symbol: str) -> str:
    base, quote = split_symbol_currencies(symbol)
    query_map = {
        "XAUUSD": "gold OR XAUUSD OR Federal Reserve OR inflation",
        "NAS100": "Nasdaq 100 OR US tech stocks OR Federal Reserve",
        "BTCUSDT": "Bitcoin OR BTC OR crypto market",
        "ETHUSDT": "Ethereum OR ETH OR crypto market",
        "GBPUSD": "British pound OR sterling OR Bank of England OR US dollar",
        "EURUSD": "euro OR ECB OR US dollar OR Federal Reserve",
        "USDJPY": "US dollar OR Japanese yen OR Bank of Japan",
        "USDCHF": "US dollar OR Swiss franc OR Swiss National Bank",
        "AUDUSD": "Australian dollar OR RBA OR US dollar",
        "NZDUSD": "New Zealand dollar OR RBNZ OR US dollar",
        "AUDJPY": "Australian dollar OR Japanese yen OR RBA OR Bank of Japan",
        "GBPJPY": "British pound OR Japanese yen OR Bank of England OR Bank of Japan",
    }
    if symbol.upper() in query_map:
        return query_map[symbol.upper()]
    return f"{base} {quote} forex market"


def _headline_relevance(symbol: str, title: str) -> int:
    normalized_title = str(title or "").lower()
    base, quote = split_symbol_currencies(symbol)
    score = 0
    for token in {symbol.lower(), base.lower(), quote.lower()}:
        if token and token in normalized_title:
            score += 4
    keyword_map = {
        "xauusd": ["gold", "fed", "inflation", "treasury"],
        "nas100": ["nasdaq", "tech", "stocks", "fed"],
        "btcusdt": ["bitcoin", "btc", "crypto", "etf"],
        "ethusdt": ["ethereum", "eth", "crypto", "defi"],
    }
    for keyword in keyword_map.get(symbol.lower(), ["forex", "central bank", "inflation"]):
        if keyword in normalized_title:
            score += 2
    return score


def _safe_xml_text(node) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.strip()

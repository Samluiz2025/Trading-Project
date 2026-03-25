from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

import requests

from trading_bot.core.alerts_store import append_alert, load_alerts
from trading_bot.core.data_fetcher import FetchConfig, fetch_ohlc
from trading_bot.core.instrument_universe import get_instrument_universe
from trading_bot.core.journal import update_open_trade_outcomes
from trading_bot.core.market_structure import detect_market_structure
from trading_bot.core.news_engine import (
    EconomicCalendarProvider,
    build_news_alerts,
    fetch_market_moving_events,
)
from trading_bot.core.strategy_execution_engine import (
    ExecutionConfig,
    determine_alert_stage,
    evaluate_strict_execution_setup,
    format_high_setup_alert,
    generate_alert_with_tf_info,
)
from trading_bot.core.strategy_engine import generate_trade_setup
from trading_bot.core.supply_demand import detect_supply_demand_zones


@dataclass(frozen=True)
class MonitorConfig:
    """Configuration for a single monitored instrument."""

    symbol: str
    interval: str = "1h"
    limit: int = 200
    source: str = "auto"


@dataclass
class AlertState:
    """Track previous monitoring state so duplicate alerts are avoided."""

    last_trend: str | None = None
    last_setup_signature: str | None = None
    last_final_bias: str | None = None
    active_zone_keys: set[str] = field(default_factory=set)
    alerted_upcoming_news_keys: set[str] = field(default_factory=set)
    alerted_released_news_keys: set[str] = field(default_factory=set)
    alerted_sudden_news_keys: set[str] = field(default_factory=set)
    alerted_strict_setup_keys: set[str] = field(default_factory=set)
    alerted_strict_stage_keys: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class TelegramConfig:
    """Configuration required to send Telegram bot alerts."""

    bot_token: str
    chat_id: str


def monitor_symbol(
    config: MonitorConfig,
    state: AlertState | None = None,
    news_provider: EconomicCalendarProvider | None = None,
    current_time: datetime | None = None,
) -> tuple[list[str], AlertState]:
    """
    Evaluate one symbol and return any newly triggered alerts.

    The alert engine reuses the existing market structure, zone, and strategy
    logic so the monitoring layer stays thin and ready for future Telegram
    integration.
    """

    active_state = state or AlertState()
    active_time = current_time or datetime.now(UTC)
    candles = fetch_ohlc(
        FetchConfig(
            symbol=config.symbol,
            interval=config.interval,
            limit=config.limit,
            source=config.source,  # type: ignore[arg-type]
        )
    )
    structure = detect_market_structure(candles)
    news_events = fetch_market_moving_events(
        provider=news_provider,
        currencies=list(_get_symbol_currencies(config.symbol)),
        current_time=active_time,
    )
    setup_payload = generate_trade_setup(
        candles,
        symbol=config.symbol,
        timeframe=config.interval,
        news_events=news_events,
        current_time=active_time,
    )
    zones = detect_supply_demand_zones(candles, symbol=config.symbol, timeframe=config.interval)
    current_price = float(candles.iloc[-1]["close"])

    alerts: list[str] = []

    trend_alert = _detect_bias_change_alert(
        symbol=config.symbol,
        new_trend=structure["trend"],
        state=active_state,
    )
    if trend_alert is not None:
        alerts.append(trend_alert)

    setup_alert = _detect_setup_alert(
        symbol=config.symbol,
        setup=setup_payload["setup"],
        state=active_state,
    )
    if setup_alert is not None:
        alerts.append(setup_alert)

    zone_alerts = _detect_zone_entry_alerts(
        symbol=config.symbol,
        zones=zones,
        current_price=current_price,
        state=active_state,
    )
    alerts.extend(zone_alerts)

    news_alerts = _detect_news_alerts(
        symbol=config.symbol,
        news_events=news_events,
        current_time=active_time,
        state=active_state,
    )
    alerts.extend(news_alerts)

    final_bias_alert = _detect_final_bias_change_alert(
        symbol=config.symbol,
        final_bias=setup_payload["final_bias"],
        state=active_state,
    )
    if final_bias_alert is not None:
        alerts.append(final_bias_alert)

    active_state.last_trend = structure["trend"]
    active_state.last_setup_signature = _build_setup_signature(setup_payload["setup"])
    active_state.last_final_bias = setup_payload["final_bias"]

    return alerts, active_state


def run_monitoring_loop(
    monitor_configs: list[MonitorConfig],
    poll_interval_seconds: int = 60,
    news_provider: EconomicCalendarProvider | None = None,
) -> None:
    """Continuously monitor configured symbols and print new alerts."""

    state_by_symbol = {config.symbol.upper(): AlertState() for config in monitor_configs}
    telegram_config = load_telegram_config()
    print("[INFO] Trading bot monitor started.")
    print(f"[INFO] Polling every {poll_interval_seconds} seconds.")
    if telegram_config is not None:
        print("[INFO] Telegram alerts are enabled.")
    else:
        print("[INFO] Telegram alerts are disabled. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to enable them.")

    while True:
        for config in monitor_configs:
            symbol_key = config.symbol.upper()
            try:
                alerts, next_state = monitor_symbol(
                    config,
                    state_by_symbol[symbol_key],
                    news_provider=news_provider,
                )
                state_by_symbol[symbol_key] = next_state
                for alert in alerts:
                    print(alert)
                    send_telegram_alert(alert, telegram_config)
            except Exception as exc:
                print(f"[ERROR] {symbol_key} monitor failed: {exc}")

        time.sleep(poll_interval_seconds)


def monitor_strict_symbol(
    symbol: str,
    source: str = "auto",
    state: AlertState | None = None,
) -> tuple[list[dict], AlertState]:
    """Monitor the strict SMC/ICT engine and emit alerts only for full setups."""

    active_state = state or AlertState()
    result = evaluate_strict_execution_setup(
        ExecutionConfig(
            symbol=symbol,
            source=source,
        )
    )
    stage, stage_payload = determine_alert_stage(result)
    if stage is None:
        return [], active_state

    signature = _build_strict_setup_signature(result, stage=stage)
    if signature in active_state.alerted_strict_stage_keys:
        return [], active_state

    if stage == "HIGH_SETUP":
        message = format_high_setup_alert(result)
        alert_payload = {
            "type": stage.lower(),
            "signature": signature,
            **generate_alert_with_tf_info(result),
            "message": message,
        }
    elif stage == "ZONE_WATCH":
        message = (
            "[ZONE WATCH]\n"
            f"Pair: {stage_payload['pair']}\n"
            f"Bias: {stage_payload['bias']}\n"
            f"Entry: {stage_payload['entry']:.4f}\n"
            f"SL: {stage_payload['stop_loss']:.4f}\n"
            f"TP: {stage_payload['take_profit']:.4f}\n"
            f"Confidence: {stage_payload['confidence']}%\n"
            "Confluences:\n"
            + "\n".join(f"- {_format_confluence_for_alert(item)}" for item in stage_payload["confluences"])
        )
        alert_payload = {
            "type": stage.lower(),
            "signature": signature,
            "pair": stage_payload["pair"],
            "bias": stage_payload["bias"],
            "entry": stage_payload["entry"],
            "stop_loss": stage_payload["stop_loss"],
            "take_profit": stage_payload["take_profit"],
            "confluences": stage_payload["confluences"],
            "confidence": stage_payload["confidence"],
            "timestamp": stage_payload["timestamp"],
            "message": message,
        }
    else:
        missing_lines = ""
        if stage_payload.get("missing_confluences"):
            missing_lines = "\nMissing:\n" + "\n".join(
                f"- {item}" for item in stage_payload["missing_confluences"]
            )
        message = (
            "[SETUP FORMING]\n"
            f"Pair: {stage_payload['pair']}\n"
            f"Bias: {stage_payload['bias']}\n"
            f"Potential Entry: {stage_payload['entry']:.4f}\n"
            f"Confidence: {stage_payload['confidence']}%\n"
            "Confluences:\n"
            + "\n".join(f"- {_format_confluence_for_alert(item)}" for item in stage_payload["confluences"])
            + missing_lines
        )
        alert_payload = {
            "type": stage.lower(),
            "signature": signature,
            "pair": stage_payload["pair"],
            "bias": stage_payload["bias"],
            "entry": stage_payload["entry"],
            "stop_loss": stage_payload["stop_loss"],
            "take_profit": stage_payload["take_profit"],
            "confluences": stage_payload["confluences"],
            "missing_confluences": stage_payload.get("missing_confluences", []),
            "confidence": stage_payload["confidence"],
            "timestamp": stage_payload["timestamp"],
            "message": message,
        }
    append_alert(alert_payload)
    active_state.alerted_strict_stage_keys.add(signature)
    if stage == "HIGH_SETUP":
        active_state.alerted_strict_setup_keys.add(signature)
    return [alert_payload], active_state


def run_strict_market_scanner(
    group: str = "all",
    source: str = "auto",
    poll_interval_seconds: int = 5,
) -> None:
    """Continuously scan the configured universe for full high-probability setups."""

    symbols = get_instrument_universe(group)
    state_by_symbol = {symbol.upper(): AlertState() for symbol in symbols}
    telegram_config = load_telegram_config()

    print(f"[INFO] Strict market scanner started for {group} universe.")
    print(f"[INFO] Scanning {len(symbols)} symbols every {poll_interval_seconds} seconds.")
    if telegram_config is not None:
        send_telegram_alert(
            f"[INFO] Strict scanner is online. Universe={group.upper()} Symbols={len(symbols)} Poll={poll_interval_seconds}s",
            telegram_config,
        )

    while True:
        for symbol in symbols:
            try:
                alerts, next_state = monitor_strict_symbol(
                    symbol=symbol,
                    source=source,
                    state=state_by_symbol[symbol.upper()],
                )
                state_by_symbol[symbol.upper()] = next_state
                for alert in alerts:
                    if alert["message"]:
                        print(alert["message"])
                        send_telegram_alert(alert["message"], telegram_config)
            except Exception as exc:
                print(f"[ERROR] Strict scanner failed for {symbol.upper()}: {exc}")

        try:
            closed_trades = update_open_trade_outcomes(default_source=source)
            for trade in closed_trades:
                outcome_message = _format_trade_outcome_alert(trade)
                outcome_alert = {
                    "type": "trade_closed",
                    "signature": f"trade_closed|{trade.get('signature')}|{trade.get('status')}",
                    "pair": trade.get("symbol"),
                    "bias": _infer_trade_bias_from_entry(trade),
                    "entry": trade.get("entry"),
                    "stop_loss": trade.get("stop_loss"),
                    "take_profit": trade.get("take_profit"),
                    "confidence": trade.get("confidence"),
                    "timestamp": trade.get("closed_at"),
                    "message": outcome_message,
                }
                append_alert(outcome_alert)
                print(outcome_message)
                send_telegram_alert(outcome_message, telegram_config)
        except Exception as exc:
            print(f"[ERROR] Open trade outcome monitor failed: {exc}")
        time.sleep(poll_interval_seconds)


def get_recent_alerts(limit: int = 50) -> list[dict]:
    """Return persisted alerts for API and dashboard use."""

    return list(reversed(load_alerts(limit=limit)))


def load_telegram_config() -> TelegramConfig | None:
    """Load Telegram settings from environment variables."""

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        return None

    return TelegramConfig(bot_token=bot_token, chat_id=chat_id)


def send_telegram_alert(message: str, telegram_config: TelegramConfig | None) -> None:
    """Send an alert message to Telegram when configuration is available."""

    if telegram_config is None:
        return

    url = f"https://api.telegram.org/bot{telegram_config.bot_token}/sendMessage"
    payload = {
        "chat_id": telegram_config.chat_id,
        "text": message,
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"[ERROR] Telegram alert failed: {exc}")


def _detect_bias_change_alert(symbol: str, new_trend: str, state: AlertState) -> str | None:
    """Alert only when the directional bias changes between bullish and bearish."""

    previous_trend = state.last_trend
    if previous_trend is None:
        return None

    directional_trends = {"bullish", "bearish"}
    if previous_trend in directional_trends and new_trend in directional_trends and previous_trend != new_trend:
        return f"[ALERT] {symbol.upper()} Bias changed to {new_trend.upper()}"

    return None


def _detect_setup_alert(symbol: str, setup: dict | None, state: AlertState) -> str | None:
    """Alert when a new trade setup appears or materially changes."""

    new_signature = _build_setup_signature(setup)
    if new_signature is None:
        return None

    if new_signature != state.last_setup_signature:
        return f"[ALERT] {symbol.upper()} {setup['signal']} setup detected at {setup['entry']:.4f}"

    return None


def _detect_final_bias_change_alert(symbol: str, final_bias: str, state: AlertState) -> str | None:
    """Alert when news changes the effective trading bias."""

    if state.last_final_bias is None:
        return None
    if final_bias != state.last_final_bias:
        return f"[ALERT] {symbol.upper()} Final bias changed to {final_bias.upper()} due to news/technical shift"
    return None


def _detect_zone_entry_alerts(
    symbol: str,
    zones: list[dict],
    current_price: float,
    state: AlertState,
) -> list[str]:
    """Alert when price newly enters a supply or demand zone."""

    current_zone_keys = {
        _build_zone_key(zone)
        for zone in zones
        if _price_is_inside_zone(current_price=current_price, zone=zone)
    }

    new_zone_keys = current_zone_keys.difference(state.active_zone_keys)
    alerts: list[str] = []
    for zone in zones:
        zone_key = _build_zone_key(zone)
        if zone_key in new_zone_keys:
            alerts.append(
                f"[ALERT] {symbol.upper()} Price entered {zone['type'].upper()} zone "
                f"({zone['start_price']:.4f} - {zone['end_price']:.4f})"
            )

    state.active_zone_keys = current_zone_keys
    return alerts


def _detect_news_alerts(
    symbol: str,
    news_events: list,
    current_time: datetime,
    state: AlertState,
) -> list[str]:
    """Alert for approaching, released, and sudden market-moving news."""

    upcoming_alerts, released_alerts, sudden_alerts = build_news_alerts(
        symbol=symbol,
        events=news_events,
        current_time=current_time,
    )

    alerts: list[str] = []
    for alert in upcoming_alerts:
        if alert["key"] not in state.alerted_upcoming_news_keys:
            alerts.append(alert["message"])
            state.alerted_upcoming_news_keys.add(alert["key"])

    for alert in released_alerts:
        if alert["key"] not in state.alerted_released_news_keys:
            alerts.append(alert["message"])
            state.alerted_released_news_keys.add(alert["key"])

    for alert in sudden_alerts:
        if alert["key"] not in state.alerted_sudden_news_keys:
            alerts.append(alert["message"])
            state.alerted_sudden_news_keys.add(alert["key"])

    return alerts


def _price_is_inside_zone(current_price: float, zone: dict) -> bool:
    """Return True when price is inside the zone bounds."""

    lower_bound = min(zone["start_price"], zone["end_price"])
    upper_bound = max(zone["start_price"], zone["end_price"])
    return lower_bound <= current_price <= upper_bound


def _build_setup_signature(setup: dict | None) -> str | None:
    """Build a stable signature so repeated identical setups do not re-alert."""

    if setup is None:
        return None

    zone = setup["zone"]
    return "|".join(
        [
            setup["signal"],
            f"{setup['entry']:.4f}",
            f"{setup['stop_loss']:.4f}",
            f"{setup['take_profit']:.4f}",
            zone["type"],
            f"{zone['start_price']:.4f}",
            f"{zone['end_price']:.4f}",
            zone["formed_at"],
        ]
    )


def _build_zone_key(zone: dict) -> str:
    """Build a stable identifier for zone-entry deduplication."""

    return "|".join(
        [
            zone["type"],
            f"{zone['start_price']:.4f}",
            f"{zone['end_price']:.4f}",
            zone["formed_at"],
        ]
    )


def _get_symbol_currencies(symbol: str) -> tuple[str, str]:
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


def _build_strict_setup_signature(result: dict, stage: str = "HIGH_SETUP") -> str:
    return "|".join(
        [
            stage,
            result["pair"],
            result["bias"],
            _format_signature_number(result.get("entry")),
            _format_signature_number(result.get("sl")),
            _format_signature_number(result.get("tp")),
        ]
    )


def _format_signature_number(value: float | None) -> str:
    if value is None:
        return "none"
    return f"{value:.4f}"


def _format_confluence_for_alert(item) -> str:
    if isinstance(item, dict):
        confluence_type = item.get("type", "Confluence")
        confluence_tf = item.get("tf")
        return f"{confluence_type} ({confluence_tf})" if confluence_tf else str(confluence_type)
    return str(item)


def _format_trade_outcome_alert(trade: dict) -> str:
    return (
        f"[TRADE {trade.get('status', 'CLOSED')}]\n"
        f"Pair: {trade.get('symbol')}\n"
        f"Strategy: {trade.get('strategy')}\n"
        f"Entry: {float(trade.get('entry') or 0):.4f}\n"
        f"SL: {float(trade.get('stop_loss') or 0):.4f}\n"
        f"TP: {float(trade.get('take_profit') or 0):.4f}\n"
        f"Outcome: {trade.get('status')}\n"
        f"RR: {trade.get('rr_achieved')}"
    )


def _infer_trade_bias_from_entry(trade: dict) -> str:
    entry = float(trade.get("entry") or 0)
    target = float(trade.get("take_profit") or 0)
    return "BUY" if target >= entry else "SELL"

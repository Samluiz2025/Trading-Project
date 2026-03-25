from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

import requests

from trading_bot.core.data_fetcher import FetchConfig, fetch_ohlc
from trading_bot.core.market_structure import detect_market_structure
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
    active_zone_keys: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class TelegramConfig:
    """Configuration required to send Telegram bot alerts."""

    bot_token: str
    chat_id: str


def monitor_symbol(config: MonitorConfig, state: AlertState | None = None) -> tuple[list[str], AlertState]:
    """
    Evaluate one symbol and return any newly triggered alerts.

    The alert engine reuses the existing market structure, zone, and strategy
    logic so the monitoring layer stays thin and ready for future Telegram
    integration.
    """

    active_state = state or AlertState()
    candles = fetch_ohlc(
        FetchConfig(
            symbol=config.symbol,
            interval=config.interval,
            limit=config.limit,
            source=config.source,  # type: ignore[arg-type]
        )
    )
    structure = detect_market_structure(candles)
    setup_payload = generate_trade_setup(candles, symbol=config.symbol, timeframe=config.interval)
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

    active_state.last_trend = structure["trend"]
    active_state.last_setup_signature = _build_setup_signature(setup_payload["setup"])

    return alerts, active_state


def run_monitoring_loop(
    monitor_configs: list[MonitorConfig],
    poll_interval_seconds: int = 60,
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
                alerts, next_state = monitor_symbol(config, state_by_symbol[symbol_key])
                state_by_symbol[symbol_key] = next_state
                for alert in alerts:
                    print(alert)
                    send_telegram_alert(alert, telegram_config)
            except Exception as exc:
                print(f"[ERROR] {symbol_key} monitor failed: {exc}")

        time.sleep(poll_interval_seconds)


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

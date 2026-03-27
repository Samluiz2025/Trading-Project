from __future__ import annotations

import os
from dataclasses import dataclass

import requests

from trading_bot.core.alerts_store import append_alert


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    chat_id: str


def load_telegram_config() -> TelegramConfig | None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return None
    return TelegramConfig(bot_token=token, chat_id=chat_id)


def send_alert(alert_payload: dict, telegram_config: TelegramConfig | None = None) -> dict:
    stored = append_alert(alert_payload)
    message = _format_alert_message(stored)
    print(message)
    if telegram_config is not None:
        _send_telegram_message(message, telegram_config)
    return stored


def _format_alert_message(alert_payload: dict) -> str:
    if alert_payload.get("status") == "NO TRADE":
        return (
            f"[NO TRADE]\n"
            f"Pair: {alert_payload.get('pair')}\n"
            f"Message: {alert_payload.get('message')}\n"
            f"Missing: {', '.join(alert_payload.get('missing', []))}"
        )

    return (
        f"[VALID SETUP]\n"
        f"Pair: {alert_payload.get('pair')}\n"
        f"Bias: {alert_payload.get('bias')}\n"
        f"Entry: {float(alert_payload.get('entry') or 0):.4f}\n"
        f"SL: {float(alert_payload.get('sl') or 0):.4f}\n"
        f"TP: {float(alert_payload.get('tp') or 0):.4f}\n"
        f"Confidence: {alert_payload.get('confidence')}\n"
        f"Strategies: {', '.join(alert_payload.get('strategies', []))}\n"
        f"Confluences: {', '.join(alert_payload.get('confluences', []))}"
    )


def _send_telegram_message(message: str, telegram_config: TelegramConfig) -> None:
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{telegram_config.bot_token}/sendMessage",
            json={"chat_id": telegram_config.chat_id, "text": message},
            timeout=10,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"[ERROR] Telegram alert failed: {exc}")

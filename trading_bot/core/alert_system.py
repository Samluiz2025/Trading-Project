"""
alert_system.py – Console + Telegram alerts for valid setups
"""
from __future__ import annotations
import logging
import os
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ALERTS_FILE = DATA_DIR / "alerts.json"

REFIRE_COOLDOWN_H = 2
_last_fired: dict[str, datetime] = {}


def _cooldown_ok(sym: str, bias: str) -> bool:
    key = f"{sym}_{bias}"
    last = _last_fired.get(key)
    return last is None or datetime.now(timezone.utc) - last > timedelta(hours=REFIRE_COOLDOWN_H)


def _mark_fired(sym: str, bias: str):
    _last_fired[f"{sym}_{bias}"] = datetime.now(timezone.utc)


def _save_alert(payload: dict):
    DATA_DIR.mkdir(exist_ok=True)
    alerts = []
    if ALERTS_FILE.exists():
        try:
            alerts = json.loads(ALERTS_FILE.read_text())
        except Exception:
            pass
    alerts.append(payload)
    ALERTS_FILE.write_text(json.dumps(alerts[-200:], indent=2))  # keep last 200


def _send_telegram(message: str):
    token   = os.getenv("TELEGRAM_VALID_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_VALID_CHAT_ID")   or os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    try:
        import requests
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        requests.post(url, json={
            "chat_id": chat_id, "text": message,
            "parse_mode": "Markdown",
        }, timeout=8)
    except Exception as e:
        logger.warning("Telegram send failed: %s", e)


def send_setup_alert(result) -> None:
    """Dispatch alert for a VALID SetupResult. Enforces per-symbol cooldown."""
    if result.status not in ("VALID", "VALID_TRADE"):
        return
    if not _cooldown_ok(result.symbol, result.bias):
        logger.debug("Cooldown active for %s %s — skipping Telegram", result.symbol, result.bias)
        return
    _mark_fired(result.symbol, result.bias)

    emoji = "🟢" if result.bias == "BUY" else "🔴"
    conf_icons = {"ELITE": "⭐⭐⭐", "HIGH": "⭐⭐", "MEDIUM": "⭐", "LOW": ""}

    msg = (
        f"{emoji} *{result.symbol}* – {result.bias}\n"
        f"Score: *{result.quality_score}/100* {conf_icons.get(result.confidence,'')}\n"
        f"Entry: `{result.entry}` | SL: `{result.sl}` | TP: `{result.tp}`\n"
        f"RR: 1:{result.rr}\n"
        f"✅ {chr(10).join(result.confluences[:6])}"
    )

    logger.info("\n" + "="*50 + "\n" + msg.replace("*","").replace("`","") + "\n" + "="*50)
    _send_telegram(msg)
    _save_alert({
        "pair":       result.symbol,
        "bias":       result.bias,
        "entry":      result.entry,
        "sl":         result.sl,
        "tp":         result.tp,
        "rr":         result.rr,
        "confidence": result.confidence,
        "score":      result.quality_score,
        "confluences": result.confluences,
        "timestamp":  result.timestamp,
    })

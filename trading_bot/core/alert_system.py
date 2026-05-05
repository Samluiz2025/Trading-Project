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

REFIRE_COOLDOWN_H  = 1   # min gap between same-direction alerts
JOURNAL_BLOCK_H    = 8   # suppress re-alert if OPEN or recent WIN exists within this window
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
    ALERTS_FILE.write_text(json.dumps(alerts[-500:], indent=2))  # keep last 500


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


def _journal_block_check(symbol: str, bias: str) -> tuple[bool, str]:
    """
    Return (is_blocked, reason).
    Blocked when the journal already has within the last JOURNAL_BLOCK_H hours:
      - an OPEN entry for same symbol+bias (trade still live), OR
      - a WIN entry (TP hit — don't re-enter the same move immediately)
    Old OPEN entries beyond the window are ignored — they're unresolved stale logs.
    """
    try:
        from .journal import load_journal
        now     = datetime.now(timezone.utc)
        cutoff  = now - timedelta(hours=JOURNAL_BLOCK_H)
        entries = load_journal()
        for e in entries:
            if e.get("symbol", "").upper() != symbol.upper():
                continue
            if str(e.get("bias", "")).upper() != bias.upper():
                continue
            outcome = str(e.get("outcome", "")).upper()
            # Parse entry timestamp (when the trade was logged)
            ts_raw = e.get("timestamp") or e.get("logged_at") or ""
            try:
                ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except Exception:
                continue  # can't parse timestamp — skip
            if ts < cutoff:
                continue  # entry is older than our window — ignore
            # Block if trade is still OPEN and was logged recently
            if outcome == "OPEN":
                age_h = (now - ts).total_seconds() / 3600
                return True, f"trade OPEN in journal ({age_h:.1f}h ago)"
            # Block if WIN was logged recently (TP hit)
            if outcome == "WIN":
                closed_raw = e.get("closed_at") or ts_raw
                try:
                    closed_ts = datetime.fromisoformat(str(closed_raw).replace("Z", "+00:00"))
                    if closed_ts.tzinfo is None:
                        closed_ts = closed_ts.replace(tzinfo=timezone.utc)
                    if closed_ts > cutoff:
                        age_h = (now - closed_ts).total_seconds() / 3600
                        return True, f"TP already hit {age_h:.1f}h ago"
                except Exception:
                    pass
    except Exception as e:
        logger.debug("Journal block check failed for %s: %s", symbol, e)
    return False, ""


def _news_lock_check(symbol: str) -> tuple[bool, str]:
    """Return (is_locked, reason). Fast path — no HTTP calls."""
    try:
        from .news_engine import (
            load_symbol_news_context, split_symbol_currencies,
            fetch_market_moving_events, get_news_lock, BuiltInCalendarProvider,
            JsonEconomicCalendarProvider, _CompositeProvider,
        )
        from datetime import UTC, datetime as _dt
        from pathlib import Path as _P

        # Resolve provider (same logic as load_symbol_news_context, no headline fetch)
        cal_path = _P(__file__).resolve().parents[1] / "data" / "economic_calendar.json"
        if cal_path.exists():
            provider = _CompositeProvider([JsonEconomicCalendarProvider(cal_path), BuiltInCalendarProvider()])
        else:
            provider = BuiltInCalendarProvider()

        now = _dt.now(UTC)
        currencies = list(split_symbol_currencies(symbol))
        events = fetch_market_moving_events(provider=provider, currencies=currencies, current_time=now)
        lock = get_news_lock(symbol, events, current_time=now)
        if lock.get("locked"):
            ev   = lock["events"][0]
            mins = ev["minutes_from_now"]
            direction = "in" if mins > 0 else "released"
            return True, f"{ev['event_name']} ({ev['currency']}) {direction} {abs(mins):.0f}min"
    except Exception as e:
        logger.debug("News lock check failed for %s: %s", symbol, e)
    return False, ""


def send_setup_alert(result) -> None:
    """Dispatch alert for a VALID SetupResult. Enforces cooldown + news lock."""
    if result.status not in ("VALID", "VALID_TRADE"):
        return
    if not _cooldown_ok(result.symbol, result.bias):
        logger.debug("Cooldown active for %s %s — skipping Telegram", result.symbol, result.bias)
        return

    blocked, j_reason = _journal_block_check(result.symbol, result.bias)
    if blocked:
        logger.info("JOURNAL BLOCK – %s %s suppressed: %s", result.symbol, result.bias, j_reason)
        return

    locked, reason = _news_lock_check(result.symbol)
    if locked:
        logger.info("NEWS LOCK – %s %s suppressed: %s", result.symbol, result.bias, reason)
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

"""
alert_system.py – Console + Telegram alerts for valid setups

Alert logic:
  - Fires when the setup STATE changes: bias flip, new signal, score shift ≥10pts,
    entry price moves ≥0.1%, or setup becomes invalid (sends a cancellation).
  - A minimum 5-minute guard prevents duplicate sends on the exact same unchanged setup.
  - No journal blocking. No cooldown beyond 5 min on unchanged setups.
  - News events are flagged in the message (⚠️) but never block the alert.
"""
from __future__ import annotations
import logging
import os
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)
DATA_DIR    = Path(__file__).resolve().parent.parent / "data"
ALERTS_FILE = DATA_DIR / "alerts.json"

# Minimum seconds between identical (unchanged) alerts for the same symbol
MIN_REPEAT_SECONDS = 300   # 5 minutes — prevents hammering same setup every 5s

# Last alerted state per symbol — used to detect meaningful changes
_last_state: dict[str, dict] = {}


# ── State-change detection ────────────────────────────────────────────────────

def _state_changed(symbol: str, result) -> tuple[bool, str]:
    """
    Return (changed, reason).
    'Changed' means something meaningful is different from the last alert.
    """
    prev = _last_state.get(symbol)

    if prev is None:
        return True, "new signal"

    # Bias flipped (BUY → SELL or vice versa)
    if prev.get("bias") != result.bias:
        return True, f"bias flipped {prev.get('bias')} -> {result.bias}"

    # Setup just became valid (was NO_TRADE before)
    if prev.get("status") != "VALID_TRADE" and result.status == "VALID_TRADE":
        return True, "setup became valid"

    # Score moved ≥ 10 points (meaningful change in confluence)
    score_diff = abs((prev.get("score") or 0) - (result.quality_score or 0))
    if score_diff >= 10:
        return True, f"score changed by {score_diff:+d} pts"

    # Entry price moved ≥ 0.1% (new level)
    prev_entry = prev.get("entry") or 0
    curr_entry = result.entry or 0
    if prev_entry and curr_entry:
        pct = abs(curr_entry - prev_entry) / prev_entry
        if pct >= 0.001:
            return True, f"entry moved {pct*100:.2f}%"

    # Minimum repeat guard — same setup, not enough time passed
    last_sent = prev.get("sent_at")
    if last_sent:
        elapsed = (datetime.now(timezone.utc) - last_sent).total_seconds()
        if elapsed < MIN_REPEAT_SECONDS:
            return False, f"unchanged setup ({int(elapsed)}s ago)"

    # Same setup but enough time passed — resend as confirmation
    return True, "setup confirmed (periodic update)"


def _save_state(symbol: str, result):
    _last_state[symbol] = {
        "status":  result.status,
        "bias":    result.bias,
        "score":   result.quality_score,
        "entry":   result.entry,
        "sent_at": datetime.now(timezone.utc),
    }


# ── News event annotation (never blocks) ─────────────────────────────────────

def _news_note(symbol: str) -> str:
    """Return a ⚠️ warning string if a high-impact event is imminent, else ''."""
    try:
        from .news_engine import (
            split_symbol_currencies, fetch_market_moving_events,
            get_news_lock, BuiltInCalendarProvider,
            JsonEconomicCalendarProvider, _CompositeProvider,
        )
        from datetime import UTC, datetime as _dt
        from pathlib import Path as _P

        cal_path = _P(__file__).resolve().parents[1] / "data" / "economic_calendar.json"
        provider = (
            _CompositeProvider([JsonEconomicCalendarProvider(cal_path), BuiltInCalendarProvider()])
            if cal_path.exists() else BuiltInCalendarProvider()
        )
        now = _dt.now(UTC)
        currencies = list(split_symbol_currencies(symbol))
        events = fetch_market_moving_events(provider=provider, currencies=currencies, current_time=now)
        lock = get_news_lock(symbol, events, current_time=now)
        if lock.get("locked"):
            ev = lock["events"][0]
            mins = ev["minutes_from_now"]
            direction = "in" if mins > 0 else "just released"
            return f"\n⚠️ {ev['event_name']} ({ev['currency']}) {direction} {abs(mins):.0f}min"
    except Exception:
        pass
    return ""


# ── Persistence ───────────────────────────────────────────────────────────────

def _save_alert(payload: dict):
    DATA_DIR.mkdir(exist_ok=True)
    alerts = []
    if ALERTS_FILE.exists():
        try:
            alerts = json.loads(ALERTS_FILE.read_text())
        except Exception:
            pass
    alerts.append(payload)
    ALERTS_FILE.write_text(json.dumps(alerts[-500:], indent=2))


def _send_telegram(message: str):
    token   = os.getenv("TELEGRAM_VALID_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_VALID_CHAT_ID")   or os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    try:
        import requests
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        requests.post(url, json={
            "chat_id": chat_id, "text": message, "parse_mode": "Markdown",
        }, timeout=8)
    except Exception as e:
        logger.warning("Telegram send failed: %s", e)


# ── Public API ────────────────────────────────────────────────────────────────

def send_setup_alert(result) -> None:
    """
    Fire a Telegram alert when the setup state changes meaningfully.
    Never blocks on journal history or news — news events are annotated instead.
    """
    if result.status not in ("VALID", "VALID_TRADE"):
        return

    changed, reason = _state_changed(result.symbol, result)
    if not changed:
        logger.debug("No change – %s %s (%s)", result.symbol, result.bias, reason)
        return

    _save_state(result.symbol, result)

    emoji      = "🟢" if result.bias == "BUY" else "🔴"
    conf_icons = {"ELITE": "⭐⭐⭐", "HIGH": "⭐⭐", "MEDIUM": "⭐", "LOW": ""}
    news       = _news_note(result.symbol)

    msg = (
        f"{emoji} *{result.symbol}* – {result.bias}\n"
        f"Score: *{result.quality_score}/100* {conf_icons.get(result.confidence, '')}\n"
        f"Entry: `{result.entry}` | SL: `{result.sl}` | TP: `{result.tp}`\n"
        f"RR: 1:{result.rr}\n"
        f"✅ {chr(10).join(result.confluences[:6])}"
        f"{news}"
    )

    logger.info(
        "\n" + "="*50 + "\n[%s] " + msg.replace("*","").replace("`","") + "\n" + "="*50,
        reason,
    )
    _send_telegram(msg)
    _save_alert({
        "pair":        result.symbol,
        "bias":        result.bias,
        "entry":       result.entry,
        "sl":          result.sl,
        "tp":          result.tp,
        "rr":          result.rr,
        "confidence":  result.confidence,
        "score":       result.quality_score,
        "confluences": result.confluences,
        "timestamp":   result.timestamp,
        "change":      reason,
    })


def send_ltf_alert(ltf_result) -> None:
    """
    Fire a separate Telegram alert for an LTF precision entry.
    Only fires when RR >= 5 and trigger is genuinely new.
    """
    if not ltf_result.found:
        return

    key = f"LTF_{ltf_result.symbol}_{ltf_result.bias}"
    prev = _last_state.get(key)
    now  = datetime.now(timezone.utc)

    if prev:
        elapsed = (now - prev.get("sent_at", now)).total_seconds()
        # Don't resend same LTF setup within 10 minutes
        if elapsed < 600:
            return
        # Don't resend if entry hasn't moved ≥ 0.05%
        if prev.get("entry") and ltf_result.ltf_entry:
            pct = abs(ltf_result.ltf_entry - prev["entry"]) / prev["entry"]
            if pct < 0.0005:
                return

    _last_state[key] = {"entry": ltf_result.ltf_entry, "sent_at": now}

    emoji = "🟢" if ltf_result.bias == "BUY" else "🔴"
    # Build top confluences line (skip HTF TP prefix line, show SMC hits)
    conf_lines = ltf_result.confluences[1:-1] if len(ltf_result.confluences) > 2 else ltf_result.confluences
    msg = (
        f"⚡ *{ltf_result.symbol}* – {ltf_result.bias} \\[LTF PRECISION\\]\n"
        f"Score: *{ltf_result.score}/100* ({ltf_result.confidence})\n"
        f"Entry: `{ltf_result.ltf_entry}` | SL: `{ltf_result.ltf_sl}` | TP: `{ltf_result.ltf_tp}`\n"
        f"RR: *1:{ltf_result.ltf_rr}* {emoji}  _(HTF was 1:{ltf_result.htf_rr})_\n"
        f"✅ {chr(10).join(conf_lines)}"
    )

    logger.info(
        "\n" + "="*50
        + f"\n[LTF] {ltf_result.symbol} {ltf_result.bias}"
        + f" | score={ltf_result.score} | RR=1:{ltf_result.ltf_rr}\n" + "="*50
    )
    _send_telegram(msg)
    _save_alert({
        "type":        "LTF",
        "pair":        ltf_result.symbol,
        "bias":        ltf_result.bias,
        "entry":       ltf_result.ltf_entry,
        "sl":          ltf_result.ltf_sl,
        "tp":          ltf_result.ltf_tp,
        "rr":          ltf_result.ltf_rr,
        "htf_rr":      ltf_result.htf_rr,
        "score":       ltf_result.score,
        "confidence":  ltf_result.confidence,
        "confluences": ltf_result.confluences,
        "timestamp":   now.isoformat(),
    })


def send_invalidation_alert(symbol: str, prev_bias: str, reason: str = "") -> None:
    """
    Notify Telegram when a previously valid setup is no longer valid.
    Called by market_monitor when a symbol drops from VALID_TRADE to NO_TRADE.
    """
    prev = _last_state.get(symbol)
    if not prev or prev.get("status") != "VALID_TRADE":
        return  # wasn't valid before, nothing to cancel

    _last_state[symbol] = {"status": "NO_TRADE", "bias": None, "score": 0,
                            "entry": None, "sent_at": datetime.now(timezone.utc)}

    msg = (
        f"❌ *{symbol}* – {prev_bias} setup *INVALIDATED*\n"
        f"Previous entry: `{prev.get('entry')}` | Score was: {prev.get('score')}\n"
        + (f"Reason: {reason}" if reason else "Setup conditions no longer met")
    )
    logger.info("INVALIDATED – %s %s: %s", symbol, prev_bias, reason)
    _send_telegram(msg)

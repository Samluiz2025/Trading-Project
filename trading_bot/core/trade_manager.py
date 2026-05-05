"""
trade_manager.py
─────────────────────────────────────────────────────────────────────────────
Monitors open journal trades and sends trade management alerts:

  1:1 RR reached  → "🔔 Move SL to breakeven" alert
  1:2 RR reached  → "💰 Consider partial close (50%)" alert
  1:3 RR reached  → "🎯 Final target approaching" alert

Called every scan cycle by MarketMonitor.
Tracks which milestones have already been alerted (in-memory + JSON file)
to avoid repeating the same alert.
─────────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DATA_DIR       = Path(__file__).resolve().parent.parent / "data"
MGMT_FILE      = DATA_DIR / "trade_management.json"

# In-memory cache: {trade_key: set_of_milestones_sent}
_sent: dict[str, set] = {}


def _load_sent():
    """Load previously sent milestones from disk so they survive restarts."""
    global _sent
    if MGMT_FILE.exists():
        try:
            raw = json.loads(MGMT_FILE.read_text())
            _sent = {k: set(v) for k, v in raw.items()}
        except Exception:
            _sent = {}


def _save_sent():
    DATA_DIR.mkdir(exist_ok=True)
    try:
        MGMT_FILE.write_text(json.dumps({k: list(v) for k, v in _sent.items()}, indent=2))
    except Exception:
        pass


def _trade_key(entry: dict) -> str:
    return f"{entry.get('symbol')}_{entry.get('bias')}_{entry.get('timestamp', '')[:16]}"


def _current_price(symbol: str, source: str) -> Optional[float]:
    try:
        from .data_fetcher import fetch_ohlcv
        df = fetch_ohlcv(symbol, "15m", source, limit=5)
        if df is not None and not df.empty:
            return float(df["close"].iloc[-1])
    except Exception:
        pass
    return None


def _send_mgmt_alert(symbol: str, bias: str, milestone: str,
                     current: float, entry: float, sl: float, tp: float,
                     rr: float, level: float):
    """Fire a trade management Telegram alert."""
    from .alert_system import _send_telegram, _save_alert

    emoji   = "🟢" if bias == "BUY" else "🔴"
    icons   = {"BE": "🔔", "PARTIAL": "💰", "FINAL": "🎯"}
    labels  = {
        "BE":      "Move SL to Breakeven",
        "PARTIAL": "Partial Close — take 50% off",
        "FINAL":   "Final target approaching — trail SL",
    }
    icon  = icons.get(milestone, "📌")
    label = labels.get(milestone, milestone)

    msg = (
        f"{icon} *{symbol}* – {bias} | *{label}*\n"
        f"Price: `{round(current, 5)}` | Level: `{round(level, 5)}`\n"
        f"Entry: `{entry}` | SL: `{sl}` | TP: `{tp}` | RR: 1:{rr}\n"
        f"{emoji} Trade is running — manage your position."
    )

    logger.info("[MGMT] %s %s %s @ %.5f", symbol, bias, milestone, current)
    _send_telegram(msg)
    _save_alert({
        "type":      "MANAGEMENT",
        "pair":      symbol,
        "bias":      bias,
        "milestone": milestone,
        "price":     current,
        "level":     level,
        "entry":     entry,
        "sl":        sl,
        "tp":        tp,
        "rr":        rr,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


def check_open_trades(source: str = "auto") -> int:
    """
    Check all OPEN journal entries for milestone hits.
    Returns the number of new management alerts sent.
    """
    _load_sent()

    from .journal import load_journal
    entries = load_journal()
    open_entries = [e for e in entries if e.get("outcome") == "OPEN"]
    if not open_entries:
        return 0

    alerts_sent = 0

    for e in open_entries:
        try:
            symbol  = e.get("symbol", "")
            bias    = str(e.get("bias", "")).upper()
            entry_p = float(e.get("entry") or 0)
            sl      = float(e.get("sl") or 0)
            tp      = float(e.get("tp") or 0)
            rr      = float(e.get("rr") or 0)

            if not all([symbol, bias in ("BUY", "SELL"), entry_p, sl, tp]):
                continue

            risk = abs(entry_p - sl)
            if risk == 0:
                continue

            current = _current_price(symbol, source)
            if current is None:
                continue

            key       = _trade_key(e)
            sent_set  = _sent.setdefault(key, set())

            # Calculate milestone levels
            if bias == "BUY":
                be_level      = round(entry_p + risk, 5)        # 1:1
                partial_level = round(entry_p + 2 * risk, 5)    # 1:2
                final_level   = round(entry_p + 3 * risk, 5)    # 1:3
                at_be      = current >= be_level
                at_partial = current >= partial_level
                at_final   = current >= final_level
            else:
                be_level      = round(entry_p - risk, 5)
                partial_level = round(entry_p - 2 * risk, 5)
                final_level   = round(entry_p - 3 * risk, 5)
                at_be      = current <= be_level
                at_partial = current <= partial_level
                at_final   = current <= final_level

            # Fire milestones in order (only the first new one per cycle)
            if at_final and "FINAL" not in sent_set:
                _send_mgmt_alert(symbol, bias, "FINAL",
                                 current, entry_p, sl, tp, rr, final_level)
                sent_set.update({"BE", "PARTIAL", "FINAL"})
                alerts_sent += 1

            elif at_partial and "PARTIAL" not in sent_set:
                _send_mgmt_alert(symbol, bias, "PARTIAL",
                                 current, entry_p, sl, tp, rr, partial_level)
                sent_set.update({"BE", "PARTIAL"})
                alerts_sent += 1

            elif at_be and "BE" not in sent_set:
                _send_mgmt_alert(symbol, bias, "BE",
                                 current, entry_p, sl, tp, rr, be_level)
                sent_set.add("BE")
                alerts_sent += 1

        except Exception as ex:
            logger.debug("Trade mgmt error for %s: %s", e.get("symbol"), ex)

    if alerts_sent:
        _save_sent()

    return alerts_sent

"""
market_monitor.py
─────────────────────────────────────────────────────────────────────────────
Scans all approved symbols, filters by quality score, dispatches alerts,
and periodically resolves open journal entries to WIN/LOSS.

Alert pipeline per scan cycle:
  1. scan_symbol()  → strategy analysis + state tracking (no callbacks here)
  2. scan_all()     → collect all ready-to-alert results
  3. _dedup()       → correlation dedup: max 2 USD-majors, max 1 index/crypto
                       per bias direction; all crosses kept
  4. _session_ok()  → suppress score 70-79 setups outside London/NY session
  5. Fire callbacks + LTF for the final clean set (typically 3-6 per cycle)
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations
import logging
import time
from datetime import datetime, timezone, date
from typing import Callable, Optional

from .strategy_strict_liquidity import (
    analyze, APPROVED_SYMBOLS, SetupResult, MAX_DAILY_SETUPS
)
from .data_fetcher import fetch_all_timeframes
from .alert_system import send_invalidation_alert, send_ltf_alert

logger = logging.getLogger(__name__)

RESOLVE_EVERY_N_CYCLES = 10

# ── Correlation groups ────────────────────────────────────────────────────────
# These pairs all express the same macro USD direction — cap at 2 per side
_USD_MAJORS = {
    "EURUSD", "GBPUSD", "AUDUSD", "NZDUSD",
    "USDCAD", "USDCHF", "USDJPY", "XAUUSD",
}
# Indices, crypto, commodities — cap at 1 per bias direction
_INDICES_CRYPTO = {
    "NAS100", "US100", "SP500", "US30", "GER40", "UK100", "JPN225", "JP225",
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "USOIL", "XAGUSD",
}


def _dedup_correlated(results: list[SetupResult]) -> list[SetupResult]:
    """
    Reduce correlated setups: keep only the strongest per macro group.
      - USD majors:       max 2 BUY + max 2 SELL (highest score wins)
      - Indices/crypto:   max 1 BUY + max 1 SELL
      - Cross pairs:      all kept (they express unique cross-currency dynamics)
    Returns list sorted by score descending.
    """
    def top(lst, n):
        return sorted(lst, key=lambda r: r.quality_score, reverse=True)[:n]

    usd_buy  = [r for r in results if r.symbol in _USD_MAJORS and r.bias == "BUY"]
    usd_sell = [r for r in results if r.symbol in _USD_MAJORS and r.bias == "SELL"]
    idx_buy  = [r for r in results if r.symbol in _INDICES_CRYPTO and r.bias == "BUY"]
    idx_sell = [r for r in results if r.symbol in _INDICES_CRYPTO and r.bias == "SELL"]
    crosses  = [r for r in results
                if r.symbol not in _USD_MAJORS and r.symbol not in _INDICES_CRYPTO]

    deduped = top(usd_buy, 2) + top(usd_sell, 2) + top(idx_buy, 1) + top(idx_sell, 1) + crosses
    return sorted(deduped, key=lambda r: r.quality_score, reverse=True)


def _in_active_session() -> bool:
    """True during London (07–12 UTC) and New York (12–20 UTC) sessions."""
    h = datetime.now(timezone.utc).hour
    return 7 <= h < 20


# ── Monitor ───────────────────────────────────────────────────────────────────

class MarketMonitor:
    def __init__(self, source: str = "auto", poll_seconds: int = 30):
        self.source        = source
        self.poll_seconds  = poll_seconds
        self._daily_count  = 0
        self._daily_date   = date.today()
        self._callbacks: list[Callable[[SetupResult], None]] = []
        self._running      = False
        self._cycle_count  = 0
        self._prev_status:     dict[str, str] = {}
        self._prev_bias:       dict[str, str] = {}
        # Confirmation: require 2 consecutive valid/invalid before acting
        self._consec_valid:    dict[str, int] = {}
        self._consec_no_trade: dict[str, int] = {}

    def _reset_if_new_day(self):
        today = date.today()
        if today != self._daily_date:
            logger.info("New trading day – resetting daily setup counter.")
            self._daily_count = 0
            self._daily_date  = today

    def on_valid_setup(self, cb: Callable[[SetupResult], None]):
        self._callbacks.append(cb)

    # ── scan_symbol: analysis + state only, NO callbacks ─────────────────────

    def scan_symbol(self, symbol: str) -> tuple[SetupResult, bool]:
        """
        Returns (result, alert_ready).
        alert_ready = True when the result is confirmed valid and should
        be considered for alerting. Callbacks are fired by scan_all() after
        correlation dedup and session filtering.
        """
        self._reset_if_new_day()
        tfs = fetch_all_timeframes(symbol, self.source)

        is_mock = any(
            getattr(tfs.get(tf), "attrs", {}).get("is_mock", False)
            for tf in ("daily", "h1")
        )

        result = analyze(
            symbol      = symbol,
            df_daily    = tfs["daily"],
            df_h4       = tfs["h4"],
            df_h1       = tfs["h1"],
            df_m15      = tfs["m15"],
            daily_count = self._daily_count,
        )

        prev_status = self._prev_status.get(symbol, "")
        prev_bias   = self._prev_bias.get(symbol, "")
        alert_ready = False

        if result.status == "VALID_TRADE":
            self._consec_valid[symbol]    = self._consec_valid.get(symbol, 0) + 1
            self._consec_no_trade[symbol] = 0

            if is_mock:
                logger.warning("MOCK DATA – %s suppressed", symbol)
            elif self._consec_valid[symbol] < 2:
                logger.debug("%s valid %d/2 — awaiting confirmation",
                             symbol, self._consec_valid[symbol])
            else:
                alert_ready = True   # confirmed — hand off to scan_all

        else:
            self._consec_valid[symbol]    = 0
            self._consec_no_trade[symbol] = self._consec_no_trade.get(symbol, 0) + 1

            # Invalidation: only after 2 consecutive NO_TRADE
            if (self._consec_no_trade[symbol] >= 2
                    and prev_status == "VALID_TRADE"
                    and not is_mock):
                send_invalidation_alert(
                    symbol, prev_bias,
                    reason=result.message or "conditions no longer met",
                )

        self._prev_status[symbol] = result.status
        self._prev_bias[symbol]   = result.bias or prev_bias
        return result, alert_ready

    # ── _fire: callbacks + LTF for one result ────────────────────────────────

    def _fire(self, result: SetupResult):
        self._daily_count += 1
        for cb in self._callbacks:
            try:
                cb(result)
            except Exception as e:
                logger.error("Callback error: %s", e)

        try:
            from .ltf_engine import find_ltf_entry
            ltf = find_ltf_entry(
                symbol = result.symbol,
                bias   = result.bias,
                htf_tp = result.tp,
                htf_rr = result.rr,
                source = self.source,
            )
            if ltf.found:
                send_ltf_alert(ltf)
        except Exception as e:
            logger.debug("LTF scan error for %s: %s", result.symbol, e)

    # ── scan_all: scan → dedup → session filter → fire ───────────────────────

    def scan_all(self) -> list[SetupResult]:
        all_results   = []
        ready_results = []

        for symbol in APPROVED_SYMBOLS:
            try:
                result, alert_ready = self.scan_symbol(symbol)
                all_results.append(result)
                if alert_ready:
                    ready_results.append(result)
                logger.info("%s → %s | Score: %d | Conf: %s",
                            symbol, result.status,
                            result.quality_score, result.confidence)
            except Exception as e:
                logger.error("Error scanning %s: %s", symbol, e)

        if not ready_results:
            return all_results

        # ── Correlation dedup ──────────────────────────────────────────────
        deduped = _dedup_correlated(ready_results)

        # ── Session filter ─────────────────────────────────────────────────
        # Score 70-79: London/NY session only
        # Score 80+:   always alert (elite setup, don't miss it)
        in_session = _in_active_session()
        for result in deduped:
            if result.quality_score >= 80 or in_session:
                self._fire(result)
            else:
                logger.info(
                    "Off-session suppressed: %s %s score=%d (need 80+ or active session)",
                    result.symbol, result.bias, result.quality_score,
                )

        return all_results

    def _run_outcome_resolver(self):
        try:
            from .outcome_resolver import resolve_open_outcomes
            resolved = resolve_open_outcomes(self.source)
            if resolved:
                logger.info("Outcome resolver: %d trade(s) closed (WIN/LOSS)", resolved)
        except Exception as e:
            logger.warning("Outcome resolver error: %s", e)

    def run(self):
        self._running = True
        logger.info("MarketMonitor started. Poll: %ds | Source: %s",
                    self.poll_seconds, self.source)
        self._run_outcome_resolver()

        while self._running:
            try:
                self.scan_all()
            except Exception as e:
                logger.error("Scan cycle error: %s", e)

            self._cycle_count += 1
            if self._cycle_count % RESOLVE_EVERY_N_CYCLES == 0:
                self._run_outcome_resolver()

            time.sleep(self.poll_seconds)

    def stop(self):
        self._running = False

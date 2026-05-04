"""
market_monitor.py
─────────────────────────────────────────────────────────────────────────────
Scans all approved symbols, enforces daily setup cap (5),
filters by quality score, and dispatches alerts.
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

logger = logging.getLogger(__name__)


class MarketMonitor:
    def __init__(self, source: str = "auto", poll_seconds: int = 30):
        self.source        = source
        self.poll_seconds  = poll_seconds
        self._daily_count  = 0
        self._daily_date   = date.today()
        self._callbacks: list[Callable[[SetupResult], None]] = []
        self._running      = False

    # ── Daily count management ────────────────────────────────────────────────

    def _reset_if_new_day(self):
        today = date.today()
        if today != self._daily_date:
            logger.info("New trading day – resetting daily setup counter.")
            self._daily_count = 0
            self._daily_date  = today

    def _at_cap(self) -> bool:
        self._reset_if_new_day()
        return self._daily_count >= MAX_DAILY_SETUPS

    # ── Callback registration ─────────────────────────────────────────────────

    def on_valid_setup(self, cb: Callable[[SetupResult], None]):
        """Register a callback that fires for every VALID setup."""
        self._callbacks.append(cb)

    # ── Core scan ─────────────────────────────────────────────────────────────

    def scan_symbol(self, symbol: str) -> SetupResult:
        self._reset_if_new_day()
        tfs = fetch_all_timeframes(symbol, self.source)
        result = analyze(
            symbol      = symbol,
            df_daily    = tfs["daily"],
            df_h4       = tfs["h4"],
            df_h1       = tfs["h1"],
            df_m15      = tfs["m15"],
            daily_count = self._daily_count,
        )
        if result.status == "VALID_TRADE":
            self._daily_count += 1
            for cb in self._callbacks:
                try:
                    cb(result)
                except Exception as e:
                    logger.error("Callback error: %s", e)
        return result

    def scan_all(self) -> list[SetupResult]:
        """Scan all approved symbols. Returns list of all results."""
        results = []
        for symbol in APPROVED_SYMBOLS:
            if self._at_cap():
                logger.info("Daily cap reached – skipping remaining symbols.")
                break
            try:
                result = self.scan_symbol(symbol)
                results.append(result)
                logger.info(
                    "%s → %s | Score: %d | Conf: %s",
                    symbol, result.status,
                    result.quality_score, result.confidence
                )
            except Exception as e:
                logger.error("Error scanning %s: %s", symbol, e)
        return results

    def run(self):
        """Blocking poll loop."""
        self._running = True
        logger.info("MarketMonitor started. Poll: %ds | Source: %s",
                    self.poll_seconds, self.source)
        while self._running:
            try:
                self.scan_all()
            except Exception as e:
                logger.error("Scan cycle error: %s", e)
            time.sleep(self.poll_seconds)

    def stop(self):
        self._running = False

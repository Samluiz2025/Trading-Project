"""
market_monitor.py
─────────────────────────────────────────────────────────────────────────────
Scans all approved symbols, filters by quality score, dispatches alerts,
and periodically resolves open journal entries to WIN/LOSS.
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

# Resolve open outcomes every N scan cycles
RESOLVE_EVERY_N_CYCLES = 10


class MarketMonitor:
    def __init__(self, source: str = "auto", poll_seconds: int = 30):
        self.source        = source
        self.poll_seconds  = poll_seconds
        self._daily_count  = 0
        self._daily_date   = date.today()
        self._callbacks: list[Callable[[SetupResult], None]] = []
        self._running      = False
        self._cycle_count  = 0

    def _reset_if_new_day(self):
        today = date.today()
        if today != self._daily_date:
            logger.info("New trading day – resetting daily setup counter.")
            self._daily_count = 0
            self._daily_date  = today

    def on_valid_setup(self, cb: Callable[[SetupResult], None]):
        self._callbacks.append(cb)

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
        results = []
        for symbol in APPROVED_SYMBOLS:
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
        # Resolve any stale open outcomes on startup
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

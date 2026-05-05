"""
ltf_engine.py – Lower Time Frame (M5) precision entry engine
─────────────────────────────────────────────────────────────────────────────
Called AFTER an HTF setup is confirmed (VALID_TRADE on Daily/H4/H1/M15).

Concept:
  - HTF gives bias + macro TP target
  - M5 gives a precise entry with a tight SL (just beyond the M5 wick)
  - Same TP, much smaller SL  →  1:10–20 RR instead of 1:2–3

LTF Trigger Patterns (in direction of HTF bias):
  1. M5 Liquidity Sweep + Rejection  ← highest quality
       BUY: latest M5 bar's low swept below recent swing low, closed back above
       SELL: latest M5 bar's high swept above recent swing high, closed back below

  2. M5 Break of Structure (BOS)
       BUY: M5 bar closes above the high of the previous 5 bars (bullish BOS)
       SELL: M5 bar closes below the low of the previous 5 bars (bearish BOS)

  3. M5 Fair Value Gap (FVG) + Rejection
       BUY: 3-candle bullish FVG pattern — price returned to gap and rejected
       SELL: 3-candle bearish FVG pattern — price returned to gap and rejected

  4. M5 Order Block Touch
       BUY: price returned to the last bearish OB before the bullish move
       SELL: price returned to the last bullish OB before the bearish move

Requirements for a valid LTF entry:
  - HTF status must be VALID_TRADE
  - LTF trigger must be within 0.3% of the HTF entry price
  - Minimum RR of 5:1 (no point taking LTF entries for small RR)
  - Latest M5 bar must be closed (not the live forming bar)
"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import Optional
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

MIN_LTF_RR = 5.0          # minimum RR to bother with LTF entry
ENTRY_ZONE_PCT = 0.003     # LTF trigger must be within 0.3% of HTF entry
SL_BUFFER_MULT = 1.5       # SL placed this many pips beyond the wick
LOOKBACK_BARS  = 20        # M5 bars to scan for triggers
BOS_LOOKBACK   = 5         # bars to define the BOS swing


@dataclass
class LTFResult:
    found:        bool
    symbol:       str
    bias:         str
    trigger:      str         # e.g. "M5 Liquidity Sweep + Rejection"
    ltf_entry:    Optional[float] = None
    ltf_sl:       Optional[float] = None
    ltf_tp:       Optional[float] = None
    ltf_rr:       Optional[float] = None
    htf_rr:       Optional[float] = None
    confluences:  list = None


# ── Pip size per symbol ───────────────────────────────────────────────────────

def _pip(symbol: str, price: float) -> float:
    sym = symbol.upper()
    if "JPY" in sym:
        return 0.01
    if sym in ("XAUUSD",):
        return 0.10
    if sym in ("XAGUSD",):
        return 0.005
    if sym in ("USOIL",):
        return 0.01
    if sym in ("BTCUSDT",):
        return 1.0
    if sym in ("ETHUSDT", "SOLUSDT"):
        return 0.05
    if sym in ("BNBUSDT", "XRPUSDT"):
        return 0.001
    if sym in ("NAS100", "US100", "SP500", "GER40", "UK100"):
        return 0.5
    if sym in ("US30",):
        return 1.0
    if sym in ("JPN225", "JP225"):
        return 5.0
    return 0.0001   # default forex


# ── Pattern detectors ─────────────────────────────────────────────────────────

def _detect_sweep_rejection(df: pd.DataFrame, bias: str) -> Optional[dict]:
    """
    Highest-quality pattern: wick sweeps liquidity then price closes back.
    BUY: low swept below swing low → closed above.
    SELL: high swept above swing high → closed below.
    """
    if len(df) < BOS_LOOKBACK + 1:
        return None

    latest    = df.iloc[-1]
    reference = df.iloc[-(BOS_LOOKBACK + 1):-1]

    if bias == "BUY":
        swing_low = reference["low"].min()
        swept     = latest["low"] < swing_low
        rejected  = latest["close"] > swing_low
        if swept and rejected:
            return {
                "trigger":  "M5 Liquidity Sweep + Rejection",
                "entry":    float(latest["close"]),
                "sl_wick":  float(latest["low"]),
            }
    else:  # SELL
        swing_high = reference["high"].max()
        swept      = latest["high"] > swing_high
        rejected   = latest["close"] < swing_high
        if swept and rejected:
            return {
                "trigger":  "M5 Liquidity Sweep + Rejection",
                "entry":    float(latest["close"]),
                "sl_wick":  float(latest["high"]),
            }
    return None


def _detect_bos(df: pd.DataFrame, bias: str) -> Optional[dict]:
    """
    Break of Structure: close breaks the last BOS_LOOKBACK-bar high/low.
    """
    if len(df) < BOS_LOOKBACK + 1:
        return None

    latest    = df.iloc[-1]
    reference = df.iloc[-(BOS_LOOKBACK + 1):-1]

    if bias == "BUY":
        swing_high = reference["high"].max()
        if latest["close"] > swing_high and latest["close"] > latest["open"]:
            swing_low = reference["low"].min()
            return {
                "trigger":  "M5 Break of Structure",
                "entry":    float(latest["close"]),
                "sl_wick":  float(min(latest["low"], swing_low)),
            }
    else:
        swing_low = reference["low"].min()
        if latest["close"] < swing_low and latest["close"] < latest["open"]:
            swing_high = reference["high"].max()
            return {
                "trigger":  "M5 Break of Structure",
                "entry":    float(latest["close"]),
                "sl_wick":  float(max(latest["high"], swing_high)),
            }
    return None


def _detect_fvg(df: pd.DataFrame, bias: str) -> Optional[dict]:
    """
    Fair Value Gap: 3-candle imbalance; price returned to gap and rejected.
    FVG forms at bars -3, -2, -1. Latest bar is at the gap and rejecting.
    """
    if len(df) < 4:
        return None

    c1, c2, c3, latest = (df.iloc[-4], df.iloc[-3], df.iloc[-2], df.iloc[-1])

    if bias == "BUY":
        # Bullish FVG: c1.low — gap — c3.high; price came back and closed above gap
        if c3["high"] < c1["low"]:  # gap exists
            gap_top = float(c1["low"])
            gap_bot = float(c3["high"])
            in_gap  = latest["low"] <= gap_top and latest["close"] >= gap_bot
            if in_gap and latest["close"] > latest["open"]:
                return {
                    "trigger":  "M5 FVG Rejection",
                    "entry":    float(latest["close"]),
                    "sl_wick":  float(min(latest["low"], gap_bot)),
                }
    else:
        # Bearish FVG: c3.low — gap — c1.high
        if c3["low"] > c1["high"]:
            gap_bot = float(c1["high"])
            gap_top = float(c3["low"])
            in_gap  = latest["high"] >= gap_bot and latest["close"] <= gap_top
            if in_gap and latest["close"] < latest["open"]:
                return {
                    "trigger":  "M5 FVG Rejection",
                    "entry":    float(latest["close"]),
                    "sl_wick":  float(max(latest["high"], gap_top)),
                }
    return None


def _detect_ob(df: pd.DataFrame, bias: str) -> Optional[dict]:
    """
    Order Block: last opposing candle before a strong impulse.
    Price returned to OB; confirmed if latest bar closes back in direction.
    """
    if len(df) < 6:
        return None

    body = df.iloc[-6:-1]
    latest = df.iloc[-1]

    if bias == "BUY":
        # Find last bearish candle in the reference window
        bearish = body[body["close"] < body["open"]]
        if bearish.empty:
            return None
        ob = bearish.iloc[-1]
        ob_low, ob_high = float(ob["low"]), float(ob["high"])
        # Price returned to OB and bullish close
        touched_ob = latest["low"] <= ob_high and latest["high"] >= ob_low
        if touched_ob and latest["close"] > latest["open"]:
            return {
                "trigger":  "M5 Order Block Touch",
                "entry":    float(latest["close"]),
                "sl_wick":  float(min(latest["low"], ob_low)),
            }
    else:
        bullish = body[body["close"] > body["open"]]
        if bullish.empty:
            return None
        ob = bullish.iloc[-1]
        ob_low, ob_high = float(ob["low"]), float(ob["high"])
        touched_ob = latest["high"] >= ob_low and latest["low"] <= ob_high
        if touched_ob and latest["close"] < latest["open"]:
            return {
                "trigger":  "M5 Order Block Touch",
                "entry":    float(latest["close"]),
                "sl_wick":  float(max(latest["high"], ob_high)),
            }
    return None


# ── Main entry point ──────────────────────────────────────────────────────────

def find_ltf_entry(
    symbol:     str,
    bias:       str,
    htf_entry:  float,
    htf_tp:     float,
    htf_rr:     float,
    source:     str = "auto",
) -> LTFResult:
    """
    Fetch M5 data for `symbol` and look for a precision LTF entry trigger
    aligned with the HTF bias. Returns an LTFResult.
    """
    no_signal = LTFResult(found=False, symbol=symbol, bias=bias,
                          trigger="no trigger", confluences=[])
    try:
        from .data_fetcher import fetch_ohlcv
        df = fetch_ohlcv(symbol, "5m", source, limit=60)
        if df is None or len(df) < 10:
            return no_signal
        if df.attrs.get("is_mock"):
            return no_signal

        # Use the most recent closed bars (drop the live forming bar)
        df = df.iloc[:-1].tail(LOOKBACK_BARS)
        if len(df) < 6:
            return no_signal

        pip_size = _pip(symbol, float(df.iloc[-1]["close"]))

        # Run pattern detectors in priority order
        signal = (
            _detect_sweep_rejection(df, bias)
            or _detect_fvg(df, bias)
            or _detect_ob(df, bias)
            or _detect_bos(df, bias)
        )

        if signal is None:
            return no_signal

        entry    = signal["entry"]
        sl_wick  = signal["sl_wick"]
        trigger  = signal["trigger"]

        # Validate trigger is within ENTRY_ZONE_PCT of HTF entry
        if htf_entry and abs(entry - htf_entry) / htf_entry > ENTRY_ZONE_PCT:
            logger.debug(
                "LTF trigger %.5f too far from HTF entry %.5f for %s",
                entry, htf_entry, symbol,
            )
            return no_signal

        # Calculate SL with buffer
        buf = pip_size * SL_BUFFER_MULT
        if bias == "BUY":
            ltf_sl = round(sl_wick - buf, 6)
            risk   = entry - ltf_sl
            reward = htf_tp - entry
        else:
            ltf_sl = round(sl_wick + buf, 6)
            risk   = ltf_sl - entry
            reward = entry - htf_tp

        if risk <= 0 or reward <= 0:
            return no_signal

        rr = round(reward / risk, 1)
        if rr < MIN_LTF_RR:
            logger.debug("LTF RR %.1f below minimum %.1f for %s", rr, MIN_LTF_RR, symbol)
            return no_signal

        confluences = [
            trigger,
            f"HTF bias aligned ({bias})",
            f"LTF SL: {ltf_sl} ({round(risk / pip_size, 1)} pips risk)",
            f"HTF TP target: {htf_tp}",
        ]

        logger.info(
            "LTF ENTRY FOUND | %s %s | %s | Entry=%.5f SL=%.5f TP=%.5f RR=1:%.1f",
            symbol, bias, trigger, entry, ltf_sl, htf_tp, rr,
        )

        return LTFResult(
            found       = True,
            symbol      = symbol,
            bias        = bias,
            trigger     = trigger,
            ltf_entry   = round(entry, 6),
            ltf_sl      = ltf_sl,
            ltf_tp      = htf_tp,
            ltf_rr      = rr,
            htf_rr      = htf_rr,
            confluences = confluences,
        )

    except Exception as e:
        logger.error("LTF engine error for %s: %s", symbol, e)
        return no_signal

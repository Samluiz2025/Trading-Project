"""
ltf_engine.py – Lower Time Frame precision entry engine (M30 / M15)
─────────────────────────────────────────────────────────────────────────────
Called AFTER the HTF analysis confirms a VALID_TRADE on Daily/H4/H1/M15.

Concept
-------
HTF analysis establishes the macro bias and TP target.
LTF analysis zooms into M30/M15 to find a valid SMC setup at that same level
with a much tighter SL based on LTF structure instead of H1 ATR.

Same TP, smaller SL → much higher RR (typically 1:8 – 1:20).

LTF Entry Requirements (all scored, minimum 55/100 required)
─────────────────────────────────────────────────────────────
  1. M30 bias aligned with HTF direction          (+20)
  2. M15 market structure aligned                 (+15)
  3. M15 liquidity sweep confirmed                (+20)
  4. M15 BOS (break of structure) confirmed       (+20)
  5. M15 order block identified (entry level)     (+15)
  6. M15 FVG present                              (+10)

Entry  : M15 order block level (ob_high for SELL, ob_low for BUY)
         Falls back to current M15 close if no OB found but score ≥ 55
SL     : Beyond M15 OB (+ 0.3 × M15 ATR buffer) — much tighter than H1
TP     : HTF macro target (unchanged from main analysis)
Min RR : 5.0 (below this, no point — just take the swing entry)
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd

logger = logging.getLogger(__name__)

LTF_MIN_SCORE  = 70
LTF_MIN_RR     = 5.0
ATR_SL_BUFFER  = 0.3   # multiplier on M15 ATR for SL buffer beyond OB


@dataclass
class LTFResult:
    found:       bool
    symbol:      str
    bias:        str
    score:       int               = 0
    confidence:  str               = "LOW"
    ltf_entry:   Optional[float]   = None
    ltf_sl:      Optional[float]   = None
    ltf_tp:      Optional[float]   = None
    ltf_rr:      Optional[float]   = None
    htf_rr:      Optional[float]   = None
    confluences: list              = field(default_factory=list)
    missing:     list              = field(default_factory=list)
    timeframe:   str               = "M15"


# ── Shared SMC helpers (mirrors strategy_strict_liquidity.py) ─────────────────

def _safe_float(v) -> float:
    try:
        return float(v)
    except Exception:
        return 0.0


def _atr(df: pd.DataFrame, period: int = 14) -> float:
    try:
        h, l, c = df["high"], df["low"], df["close"]
        tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
        return _safe_float(tr.ewm(span=period, adjust=False).mean().iloc[-1])
    except Exception:
        return 0.0


def _ema(df: pd.DataFrame, period: int) -> float:
    try:
        return _safe_float(df["close"].ewm(span=period, adjust=False).mean().iloc[-1])
    except Exception:
        return 0.0


def _structure(df: pd.DataFrame) -> str:
    try:
        h = df["high"].iloc[-30:].values
        l = df["low"].iloc[-30:].values
        sh = [h[i] for i in range(1, len(h)-1) if h[i] > h[i-1] and h[i] > h[i+1]]
        sl = [l[i] for i in range(1, len(l)-1) if l[i] < l[i-1] and l[i] < l[i+1]]
        if len(sh) >= 2 and len(sl) >= 2:
            if sh[-1] > sh[-2] and sl[-1] > sl[-2]: return "BULLISH"
            if sh[-1] < sh[-2] and sl[-1] < sl[-2]: return "BEARISH"
        return "RANGING"
    except Exception:
        return "RANGING"


def _m30_bias(df: pd.DataFrame) -> str:
    """M30 bias using EMA20/50 + structure — same logic as _h4_bias."""
    try:
        if len(df) < 20:
            return "NEUTRAL"
        e20 = _ema(df, 20)
        e50 = _ema(df, min(50, len(df) - 1))
        ms  = _structure(df)
        if e20 > e50 and ms in ("BULLISH", "RANGING"): return "BULLISH"
        if e20 < e50 and ms in ("BEARISH", "RANGING"): return "BEARISH"
        return "NEUTRAL"
    except Exception:
        return "NEUTRAL"


def _liq_swept(df: pd.DataFrame, bias: str) -> bool:
    try:
        tol = 0.0005
        h = df["high"].iloc[-40:].values
        l = df["low"].iloc[-40:].values
        if bias == "SELL":
            for i in range(1, len(h) - 3):
                if abs(h[i] - h[i-1]) / (h[i] + 1e-9) < tol:
                    if any(x > max(h[i], h[i-1]) for x in h[i+1:]):
                        return True
        else:
            for i in range(1, len(l) - 3):
                if abs(l[i] - l[i-1]) / (l[i] + 1e-9) < tol:
                    if any(x < min(l[i], l[i-1]) for x in l[i+1:]):
                        return True
    except Exception:
        pass
    return False


def _bos(df: pd.DataFrame, bias: str) -> bool:
    try:
        h = df["high"].iloc[-25:].values
        l = df["low"].iloc[-25:].values
        c = df["close"].iloc[-25:].values
        if bias == "BUY":
            sh = [h[i] for i in range(1, len(h)-1) if h[i] > h[i-1] and h[i] > h[i+1]]
            return bool(sh and c[-1] > sh[-1])
        else:
            sl = [l[i] for i in range(1, len(l)-1) if l[i] < l[i-1] and l[i] < l[i+1]]
            return bool(sl and c[-1] < sl[-1])
    except Exception:
        return False


def _find_ob(df: pd.DataFrame, bias: str):
    """Find the most recent valid order block."""
    try:
        candles = df.iloc[-50:]
        for i in range(len(candles) - 3, 0, -1):
            c  = candles.iloc[i]
            cn = candles.iloc[i + 1]
            atr = _atr(candles.iloc[:i + 1])
            if bias == "BUY":
                if c["close"] < c["open"] and cn["close"] > cn["open"]:
                    if (cn["close"] - cn["open"]) > atr * 0.5:
                        ob_l, ob_h = float(c["low"]), float(c["high"])
                        if candles.iloc[i+1:]["low"].min() > ob_l:
                            return ob_l, ob_h
            else:
                if c["close"] > c["open"] and cn["close"] < cn["open"]:
                    if (cn["open"] - cn["close"]) > atr * 0.5:
                        ob_l, ob_h = float(c["low"]), float(c["high"])
                        if candles.iloc[i+1:]["high"].max() < ob_h:
                            return ob_l, ob_h
    except Exception:
        pass
    return None, None


def _fvg(df: pd.DataFrame, bias: str) -> bool:
    try:
        for i in range(len(df) - 3, max(len(df) - 25, 1), -1):
            c1, c3 = df.iloc[i - 1], df.iloc[i + 1]
            if bias == "BUY"  and c3["low"]  > c1["high"]: return True
            if bias == "SELL" and c3["high"] < c1["low"]:  return True
    except Exception:
        pass
    return False


def _label(score: int) -> str:
    if score >= 90: return "ELITE"
    if score >= 75: return "HIGH"
    if score >= 55: return "MEDIUM"
    return "LOW"


# ── LTF scoring ───────────────────────────────────────────────────────────────

def _ltf_score(m30_bias_ok: bool, m15_struct_ok: bool, liq: bool,
               bos: bool, ob_ok: bool, fvg: bool) -> tuple[int, list, list]:
    score = 0
    conf  = []
    miss  = []

    if m30_bias_ok:
        score += 20; conf.append("M30 Bias Aligned")
    else:
        miss.append("M30 Bias")

    if m15_struct_ok:
        score += 15; conf.append("M15 Structure Aligned")
    else:
        miss.append("M15 Structure")

    if liq:
        score += 20; conf.append("M15 Liquidity Sweep")
    else:
        miss.append("M15 Liquidity Sweep")

    if bos:
        score += 20; conf.append("M15 BOS Confirmed")
    else:
        miss.append("M15 BOS")

    if ob_ok:
        score += 15; conf.append("M15 Order Block")
    else:
        miss.append("M15 Order Block")

    if fvg:
        score += 10; conf.append("M15 FVG Present")

    return score, conf, miss


# ── Main entry point ──────────────────────────────────────────────────────────

def find_ltf_entry(
    symbol:    str,
    bias:      str,          # HTF bias: "BUY" or "SELL"
    htf_tp:    float,        # HTF macro TP target (unchanged)
    htf_rr:    float,        # HTF RR (for comparison in alert)
    source:    str = "auto",
) -> LTFResult:
    """
    Fetch M30 and M15 data, run full SMC confluence check on LTF,
    and return a precision entry with tight M15-based SL and HTF TP.
    """
    no_signal = LTFResult(found=False, symbol=symbol, bias=bias, htf_rr=htf_rr)

    try:
        from .data_fetcher import fetch_ohlcv

        df_m30 = fetch_ohlcv(symbol, "30m", source, limit=100)
        df_m15 = fetch_ohlcv(symbol, "15m", source, limit=100)

        if df_m30 is None or len(df_m30) < 20:
            return no_signal
        if df_m15 is None or len(df_m15) < 25:
            return no_signal
        if df_m30.attrs.get("is_mock") or df_m15.attrs.get("is_mock"):
            return no_signal

        direction = "BULLISH" if bias == "BUY" else "BEARISH"

        # ── M30 bias (structural context for LTF) ─────────────────────────────
        m30b       = _m30_bias(df_m30)
        m30_ok     = m30b == direction

        # ── M15 structure, liquidity, BOS, OB, FVG ────────────────────────────
        m15_struct = _structure(df_m15)
        m15_ok     = m15_struct == direction

        liq        = _liq_swept(df_m15, bias)
        bos        = _bos(df_m15, bias)
        ob_l, ob_h = _find_ob(df_m15, bias)
        ob_ok      = ob_l is not None
        fvg        = _fvg(df_m15, bias)

        # ── Score ──────────────────────────────────────────────────────────────
        score, confluences, missing = _ltf_score(
            m30_ok, m15_ok, liq, bos, ob_ok, fvg
        )

        if score < LTF_MIN_SCORE:
            logger.debug(
                "LTF score %d < %d for %s %s — no precision entry",
                score, LTF_MIN_SCORE, symbol, bias
            )
            return no_signal

        # ── Entry / SL from M15 OB ────────────────────────────────────────────
        atr_m15 = _atr(df_m15)
        price   = _safe_float(df_m15["close"].iloc[-1])

        if bias == "BUY":
            entry  = round(ob_h, 6) if ob_ok else round(price, 6)
            sl     = round((ob_l - ATR_SL_BUFFER * atr_m15) if ob_ok
                           else (price - ATR_SL_BUFFER * atr_m15), 6)
        else:
            entry  = round(ob_l, 6) if ob_ok else round(price, 6)
            sl     = round((ob_h + ATR_SL_BUFFER * atr_m15) if ob_ok
                           else (price + ATR_SL_BUFFER * atr_m15), 6)

        tp = htf_tp

        risk   = abs(entry - sl)
        reward = abs(tp - entry)
        rr     = round(reward / risk, 1) if risk > 0 else 0.0

        if rr < LTF_MIN_RR:
            logger.debug(
                "LTF RR 1:%.1f below minimum 1:%.1f for %s", rr, LTF_MIN_RR, symbol
            )
            return no_signal

        # ── Annotate confluences ───────────────────────────────────────────────
        confluences.insert(0, f"HTF TP target: {tp}")
        confluences.append(f"M15 SL distance: {round(risk / (atr_m15 + 1e-9), 1)}× M15 ATR")

        logger.info(
            "LTF ENTRY | %s %s | score=%d | entry=%.5f sl=%.5f tp=%.5f rr=1:%.1f (HTF was 1:%.1f)",
            symbol, bias, score, entry, sl, tp, rr, htf_rr,
        )

        return LTFResult(
            found       = True,
            symbol      = symbol,
            bias        = bias,
            score       = score,
            confidence  = _label(score),
            ltf_entry   = entry,
            ltf_sl      = sl,
            ltf_tp      = tp,
            ltf_rr      = rr,
            htf_rr      = htf_rr,
            confluences = confluences,
            missing     = missing,
            timeframe   = "M15",
        )

    except Exception as e:
        logger.error("LTF engine error for %s: %s", symbol, e)
        return no_signal

"""
strategy_strict_liquidity.py
SMC Multi-Timeframe Strategy Engine with confluence scoring.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

MIN_QUALITY_SCORE = 70   # raised — only alert high-confidence setups
MAX_DAILY_SETUPS  = 10
MIN_ADX           = 18
MIN_RR            = 2.0
ATR_SL_MULT       = 1.5
ATR_TP_MULT       = 3.5   # fallback only — real TP comes from swing structure
REFIRE_COOLDOWN_H = 2

LONDON_OPEN  = 7
LONDON_CLOSE = 12
NY_OPEN      = 12
NY_CLOSE     = 20

from .instrument_universe import APPROVED_SYMBOLS  # noqa: E402 — used by market_monitor & main


@dataclass
class SetupResult:
    status:        str
    symbol:        str
    bias:          Optional[str] = None
    entry:         Optional[float] = None
    sl:            Optional[float] = None
    tp:            Optional[float] = None
    rr:            Optional[float] = None
    quality_score: int = 0
    confidence:    str = "LOW"
    confluences:   list = field(default_factory=list)
    missing:       list = field(default_factory=list)
    message:       str = ""
    daily_bias:    str = "-"
    h4_bias:       str = "-"
    h1_bias:       str = "-"
    latest_price:  Optional[float] = None
    session:       str = "-"
    adx:           float = 0.0
    rsi:           float = 50.0
    atr:           float = 0.0
    timestamp:     str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


# ── Indicators ────────────────────────────────────────────────────────────────

def _safe_float(val) -> float:
    try:
        return float(val)
    except Exception:
        return 0.0


def _atr(df: pd.DataFrame, period: int = 14) -> float:
    try:
        h, l, c = df["high"], df["low"], df["close"]
        tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
        return _safe_float(tr.ewm(span=period, adjust=False).mean().iloc[-1])
    except Exception:
        return 0.001


def _adx(df: pd.DataFrame, period: int = 14) -> float:
    try:
        h, l, c = df["high"], df["low"], df["close"]
        up   = h.diff()
        down = -l.diff()
        pdm  = np.where((up > down) & (up > 0), up, 0.0)
        mdm  = np.where((down > up) & (down > 0), down, 0.0)
        tr_s = pd.Series(pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1).values)
        atr_s   = tr_s.ewm(span=period, adjust=False).mean()
        pdi = 100 * pd.Series(pdm).ewm(span=period, adjust=False).mean() / (atr_s + 1e-9)
        mdi = 100 * pd.Series(mdm).ewm(span=period, adjust=False).mean() / (atr_s + 1e-9)
        dx  = 100 * (pdi - mdi).abs() / (pdi + mdi + 1e-9)
        return _safe_float(dx.ewm(span=period, adjust=False).mean().iloc[-1])
    except Exception:
        return 0.0


def _ema(df: pd.DataFrame, period: int) -> float:
    try:
        return _safe_float(df["close"].ewm(span=period, adjust=False).mean().iloc[-1])
    except Exception:
        return 0.0


def _rsi(df: pd.DataFrame, period: int = 14) -> float:
    try:
        delta = df["close"].diff()
        gain  = delta.clip(lower=0).ewm(span=period, adjust=False).mean()
        loss  = (-delta.clip(upper=0)).ewm(span=period, adjust=False).mean()
        rs    = gain / (loss + 1e-9)
        return _safe_float(100 - 100 / (1 + rs.iloc[-1]))
    except Exception:
        return 50.0


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


def _find_ob(df: pd.DataFrame, bias: str):
    try:
        candles = df.iloc[-50:]
        for i in range(len(candles)-3, 0, -1):
            c = candles.iloc[i]; cn = candles.iloc[i+1]
            atr = _atr(candles.iloc[:i+1])
            if bias == "BUY":
                if c["close"] < c["open"] and cn["close"] > cn["open"]:
                    if (cn["close"] - cn["open"]) > atr * 0.8:
                        ob_l, ob_h = c["low"], c["high"]
                        if candles.iloc[i+1:]["low"].min() > ob_l:
                            return ob_l, ob_h
            else:
                if c["close"] > c["open"] and cn["close"] < cn["open"]:
                    if (cn["open"] - cn["close"]) > atr * 0.8:
                        ob_l, ob_h = c["low"], c["high"]
                        if candles.iloc[i+1:]["high"].max() < ob_h:
                            return ob_l, ob_h
    except Exception:
        pass
    return None, None


def _fvg(df: pd.DataFrame, bias: str) -> bool:
    try:
        for i in range(len(df)-3, max(len(df)-25, 1), -1):
            c1, c3 = df.iloc[i-1], df.iloc[i+1]
            if bias == "BUY" and c3["low"] > c1["high"]:  return True
            if bias == "SELL" and c3["high"] < c1["low"]: return True
    except Exception:
        pass
    return False


def _liq_swept(df: pd.DataFrame, bias: str) -> bool:
    try:
        tol = 0.0005
        h = df["high"].iloc[-40:].values
        l = df["low"].iloc[-40:].values
        if bias == "SELL":
            for i in range(1, len(h)-3):
                if abs(h[i]-h[i-1])/(h[i]+1e-9) < tol:
                    if any(x > max(h[i],h[i-1]) for x in h[i+1:]):
                        return True
        else:
            for i in range(1, len(l)-3):
                if abs(l[i]-l[i-1])/(l[i]+1e-9) < tol:
                    if any(x < min(l[i],l[i-1]) for x in l[i+1:]):
                        return True
    except Exception:
        pass
    return False


def _bos(df_m15: pd.DataFrame, bias: str) -> bool:
    try:
        h = df_m15["high"].iloc[-25:].values
        l = df_m15["low"].iloc[-25:].values
        c = df_m15["close"].iloc[-25:].values
        if bias == "BUY":
            sh = [h[i] for i in range(1,len(h)-1) if h[i]>h[i-1] and h[i]>h[i+1]]
            return bool(sh and c[-1] > sh[-1])
        else:
            sl = [l[i] for i in range(1,len(l)-1) if l[i]<l[i-1] and l[i]<l[i+1]]
            return bool(sl and c[-1] < sl[-1])
    except Exception:
        return False


def _daily_bias(df: pd.DataFrame) -> str:
    try:
        if len(df) < 30:
            return "NEUTRAL"
        e20 = _ema(df, 20); e50 = _ema(df, min(50, len(df)-1))
        ms  = _structure(df)
        bull = (1 if e20 > e50 else 0) + (1 if ms == "BULLISH" else 0)
        bear = (1 if e20 < e50 else 0) + (1 if ms == "BEARISH" else 0)
        if bull >= 2: return "BULLISH"
        if bear >= 2: return "BEARISH"
        # tiebreak: last close vs ema20
        price = _safe_float(df["close"].iloc[-1])
        return "BULLISH" if price > e20 else "BEARISH"
    except Exception:
        return "NEUTRAL"


def _h4_bias(df: pd.DataFrame) -> str:
    try:
        if len(df) < 20:
            return "NEUTRAL"
        e20 = _ema(df, 20); e50 = _ema(df, min(50, len(df)-1))
        ms  = _structure(df)
        if e20 > e50 and ms in ("BULLISH","RANGING"): return "BULLISH"
        if e20 < e50 and ms in ("BEARISH","RANGING"): return "BEARISH"
        return "NEUTRAL"
    except Exception:
        return "NEUTRAL"


def _session(now: Optional[datetime] = None) -> tuple[bool, str]:
    t = (now or datetime.now(timezone.utc))
    h = t.hour
    if LONDON_OPEN <= h < LONDON_CLOSE: return True,  "London"
    if NY_OPEN     <= h < NY_CLOSE:     return True,  "New York"
    if 5 <= h < 7:                      return True,  "Pre-London"
    return False, "Off-session"


# ── Swing-based TP ───────────────────────────────────────────────────────────

def _swing_tp(df_daily: pd.DataFrame, df_h4: pd.DataFrame,
              bias: str, entry: float, sl: float, atr_h1: float) -> float:
    """
    Find TP at the nearest significant daily or H4 swing level beyond entry
    that satisfies MIN_RR. Falls back to ATR×3.5 if no structural level found.
    """
    risk = abs(entry - sl)
    if risk == 0:
        risk = atr_h1 * ATR_SL_MULT

    def _nearest_swing(df) -> Optional[float]:
        if df is None or len(df) < 10:
            return None
        h = df["high"].values
        l  = df["low"].values
        if bias == "BUY":
            targets = sorted(
                float(h[i]) for i in range(1, len(h) - 1)
                if h[i] > h[i-1] and h[i] > h[i+1] and h[i] > entry
            )
            for t in targets:
                if (t - entry) / risk >= MIN_RR:
                    return t
        else:
            targets = sorted(
                (float(l[i]) for i in range(1, len(l) - 1)
                 if l[i] < l[i-1] and l[i] < l[i+1] and l[i] < entry),
                reverse=True,
            )
            for t in targets:
                if (entry - t) / risk >= MIN_RR:
                    return t
        return None

    tp = _nearest_swing(df_daily) or _nearest_swing(df_h4)
    if tp is not None:
        return round(tp, 6)

    # Fallback: ATR-based
    mult = ATR_TP_MULT * atr_h1
    return round(entry + mult if bias == "BUY" else entry - mult, 6)


# ── Scoring ───────────────────────────────────────────────────────────────────

def _score(daily_b, h4_b, h1_struct, adx, rsi, bias,
           liq, bos_ok, ob_ok, fvg_ok, sess_ok, rr) -> tuple[int,list,list]:
    score = 0; conf = []; miss = []
    d = "BULLISH" if bias == "BUY" else "BEARISH"

    if daily_b == d:   score += 20; conf.append("Daily Bias Aligned")
    else:               miss.append("Daily Bias")

    if h4_b == d:      score += 10; conf.append("H4 Bias Aligned")
    else:               miss.append("H4 Alignment")

    if h1_struct == d: score += 10; conf.append("H1 Structure Aligned")
    else:               miss.append("H1 Structure")

    if liq:            score += 15; conf.append("Liquidity Sweep Confirmed")
    else:               miss.append("Liquidity Sweep")

    if bos_ok:         score += 15; conf.append("M15 BOS Confirmed")
    else:               miss.append("M15 BOS")

    if ob_ok:          score += 10; conf.append("Order Block Present")
    else:               miss.append("Order Block")

    if fvg_ok:         score +=  5; conf.append("FVG Proximity")

    if adx >= MIN_ADX: score +=  5; conf.append(f"ADX {adx:.1f}")
    else:               miss.append(f"ADX low ({adx:.1f})")

    if (bias=="BUY" and rsi < 60) or (bias=="SELL" and rsi > 40):
        score += 5; conf.append(f"RSI confluent ({rsi:.1f})")
    else:
        miss.append(f"RSI ({rsi:.1f})")

    if sess_ok:        score +=  5; conf.append("Valid Session")
    else:               miss.append("Off-session")

    return score, conf, miss


def _label(score: int) -> str:
    if score >= 90: return "ELITE"
    if score >= 75: return "HIGH"
    if score >= 55: return "MEDIUM"
    return "LOW"


# ── Cooldown tracker ──────────────────────────────────────────────────────────
_last_fired: dict[str, datetime] = {}

def _cooldown_ok(sym: str, bias: str) -> bool:
    key = f"{sym}_{bias}"
    last = _last_fired.get(key)
    return last is None or datetime.now(timezone.utc) - last > timedelta(hours=REFIRE_COOLDOWN_H)

def _mark_fired(sym: str, bias: str):
    _last_fired[f"{sym}_{bias}"] = datetime.now(timezone.utc)


# ── Main entry ────────────────────────────────────────────────────────────────

def analyze(
    symbol:      str,
    df_daily:    pd.DataFrame,
    df_h4:       pd.DataFrame,
    df_h1:       pd.DataFrame,
    df_m15:      pd.DataFrame,
    daily_count: int = 0,
    now_utc:     Optional[datetime] = None,
) -> SetupResult:

    def no_trade(missing, msg, db="-", hb="-", h1b="-", price=None, sess="-"):
        return SetupResult(
            status="NO TRADE", symbol=symbol,
            missing=missing, message=msg,
            daily_bias=db, h4_bias=hb, h1_bias=h1b,
            latest_price=price, session=sess,
        )

    # Data check — be lenient, mock fills gaps anyway
    for name, df, mn in [("Daily",df_daily,30),("H4",df_h4,20),("H1",df_h1,25),("M15",df_m15,25)]:
        if df is None or len(df) < mn:
            return no_trade([f"Insufficient {name} data"], f"Need ≥{mn} {name} candles.")

    sess_ok, session_name = _session(now_utc)

    db   = _daily_bias(df_daily)
    h4b  = _h4_bias(df_h4)
    h1s  = _structure(df_h1)
    adx  = _adx(df_h1)
    rsi  = _rsi(df_h1)
    atr  = _atr(df_h1)
    price = _safe_float(df_h1["close"].iloc[-1])

    if db == "NEUTRAL":
        return no_trade(["Daily Bias"], "Daily bias is NEUTRAL.", db, h4b, h1s, price, session_name)

    bias = "BUY" if db == "BULLISH" else "SELL"

    liq  = _liq_swept(df_h1, bias)
    bos  = _bos(df_m15, bias)
    ob_l, ob_h = _find_ob(df_h1, bias)
    ob_ok  = ob_l is not None
    fvg_ok = _fvg(df_m15, bias)

    # Entry / SL
    if bias == "BUY":
        entry = round(ob_h, 5) if ob_ok else round(price, 5)
        sl    = round((ob_l - 0.3*atr) if ob_ok else (price - ATR_SL_MULT*atr), 5)
    else:
        entry = round(ob_l, 5) if ob_ok else round(price, 5)
        sl    = round((ob_h + 0.3*atr) if ob_ok else (price + ATR_SL_MULT*atr), 5)

    # Stale check — if price already blew through the OB in trade direction, skip
    if bias == "BUY" and price > entry + 1.5 * atr:
        return no_trade(
            ["Entry already passed"],
            f"Price {price:.5f} already {((price-entry)/atr):.1f}×ATR above OB — setup stale.",
            db, h4b, h1s, price, session_name,
        )
    if bias == "SELL" and price < entry - 1.5 * atr:
        return no_trade(
            ["Entry already passed"],
            f"Price {price:.5f} already {((entry-price)/atr):.1f}×ATR below OB — setup stale.",
            db, h4b, h1s, price, session_name,
        )

    # TP from nearest daily/H4 swing structure (real target, not ATR formula)
    tp = _swing_tp(df_daily, df_h4, bias, entry, sl, atr)

    risk   = abs(entry - sl)
    reward = abs(tp - entry)
    rr     = round(reward / risk, 2) if risk > 0 else 0.0

    if rr < MIN_RR:
        return no_trade(
            [f"RR too low ({rr})"],
            f"RR {rr:.1f} < minimum {MIN_RR}.",
            db, h4b, h1s, price, session_name
        )

    score, confluences, missing = _score(
        db, h4b, h1s, adx, rsi, bias,
        liq, bos, ob_ok, fvg_ok, sess_ok, rr
    )

    if score < MIN_QUALITY_SCORE:
        return SetupResult(
            status="NO TRADE", symbol=symbol,
            missing=missing, message=f"Score {score}/100 below {MIN_QUALITY_SCORE}. Missing: {', '.join(missing)}",
            daily_bias=db, h4_bias=h4b, h1_bias=h1s,
            latest_price=price, session=session_name,
            quality_score=score, adx=adx, rsi=rsi, atr=atr,
        )

    confluences.append(f"RR 1:{rr}")
    if session_name != "Off-session":
        confluences.append(f"Session: {session_name}")

    logger.info("VALID | %s %s | score=%d | entry=%.5f sl=%.5f tp=%.5f rr=1:%.1f",
                symbol, bias, score, entry, sl, tp, rr)

    return SetupResult(
        status="VALID_TRADE", symbol=symbol,
        bias=bias, entry=entry, sl=sl, tp=tp, rr=rr,
        quality_score=score, confidence=_label(score),
        confluences=confluences, missing=[],
        message=f"Valid setup. Score: {score}/100.",
        daily_bias=db, h4_bias=h4b, h1_bias=h1s,
        latest_price=price, session=session_name,
        adx=adx, rsi=rsi, atr=atr,
    )
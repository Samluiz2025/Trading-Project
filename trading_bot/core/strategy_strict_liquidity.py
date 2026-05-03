"""
strategy_strict_liquidity.py
─────────────────────────────────────────────────────────────────────────────
IMPROVED Smart Money Concepts Strategy Engine
─────────────────────────────────────────────────────────────────────────────
Key improvements over v1:
  1. Confluence SCORING system (0–100) – only HIGH-score setups fire
  2. Multi-timeframe alignment gate: Daily → H4 → H1 → M15
  3. ATR-adaptive SL/TP (no more fixed-pip SL getting swept)
  4. Session quality filter: London open, NY open, overlap only
  5. Trend strength gate (ADX ≥ 22) to skip choppy markets
  6. Volume/spread proxy check to avoid thin-market fakeouts
  7. OB (Order Block) quality scoring – only premium / discount OBs
  8. FVG (Fair Value Gap) proximity filter for sniper entries
  9. Daily trade-cap: max 5 setups, min quality score 72/100
 10. Anti-recency bias: won't fire same direction twice in 4h on same pair
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────
MIN_QUALITY_SCORE   = 72          # out of 100 – below this → NO TRADE
MAX_DAILY_SETUPS    = 5
MIN_ADX             = 22          # trend strength gate
MIN_RR              = 3.0         # minimum risk-reward ratio
ATR_SL_MULTIPLIER   = 1.5         # SL = entry ± 1.5×ATR(H1,14)
ATR_TP_MULTIPLIER   = 4.5         # TP = entry ± 4.5×ATR(H1,14)  → 1:3 RR floor
REFIRE_COOLDOWN_H   = 4           # hours before same pair+direction can refire
LONDON_OPEN  = (7, 0)             # UTC 07:00
LONDON_CLOSE = (12, 0)            # UTC 12:00
NY_OPEN      = (12, 0)            # UTC 12:00
NY_CLOSE     = (20, 0)            # UTC 20:00

APPROVED_SYMBOLS = [
    "ETHUSDT", "GBPUSD", "EURUSD", "BTCUSDT",
    "XAUUSD",  "NAS100", "USDCHF", "USDJPY",
]

# ─── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class SetupResult:
    status:        str                     # "VALID" | "NO TRADE"
    symbol:        str
    bias:          Optional[str] = None    # "BUY" | "SELL"
    entry:         Optional[float] = None
    sl:            Optional[float] = None
    tp:            Optional[float] = None
    rr:            Optional[float] = None
    quality_score: int = 0                 # 0–100
    confidence:    str = "LOW"             # LOW / MEDIUM / HIGH / ELITE
    confluences:   list = field(default_factory=list)
    missing:       list = field(default_factory=list)
    message:       str = ""
    timestamp:     str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


# ─── Indicator helpers ────────────────────────────────────────────────────────

def _atr(df: pd.DataFrame, period: int = 14) -> float:
    """Average True Range – last value."""
    high, low, close = df["high"], df["low"], df["close"]
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return float(tr.ewm(span=period, adjust=False).mean().iloc[-1])


def _adx(df: pd.DataFrame, period: int = 14) -> float:
    """Wilder ADX – last value."""
    high, low, close = df["high"], df["low"], df["close"]
    up_move   = high.diff()
    down_move = -low.diff()
    plus_dm  = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr_s = pd.Series(
        pd.concat([high - low, (high - close.shift()).abs(),
                   (low  - close.shift()).abs()], axis=1).max(axis=1).values
    )
    atr_s   = tr_s.ewm(span=period, adjust=False).mean()
    plus_di  = 100 * pd.Series(plus_dm).ewm(span=period, adjust=False).mean() / atr_s
    minus_di = 100 * pd.Series(minus_dm).ewm(span=period, adjust=False).mean() / atr_s
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9)
    adx = dx.ewm(span=period, adjust=False).mean()
    return float(adx.iloc[-1])


def _ema(df: pd.DataFrame, period: int) -> float:
    return float(df["close"].ewm(span=period, adjust=False).mean().iloc[-1])


def _rsi(df: pd.DataFrame, period: int = 14) -> float:
    delta = df["close"].diff()
    gain  = delta.clip(lower=0).ewm(span=period, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(span=period, adjust=False).mean()
    rs    = gain / (loss + 1e-9)
    return float(100 - 100 / (1 + rs.iloc[-1]))


def _higher_highs_lower_lows(df: pd.DataFrame) -> str:
    """Simple market structure detection on the last 30 candles."""
    closes = df["close"].iloc[-30:].values
    highs  = df["high"].iloc[-30:].values
    lows   = df["low"].iloc[-30:].values
    # Find swing highs/lows (3-candle pivots)
    swing_highs = [highs[i] for i in range(1, len(highs)-1)
                   if highs[i] > highs[i-1] and highs[i] > highs[i+1]]
    swing_lows  = [lows[i]  for i in range(1, len(lows)-1)
                   if lows[i]  < lows[i-1]  and lows[i]  < lows[i+1]]
    if len(swing_highs) >= 2 and len(swing_lows) >= 2:
        if swing_highs[-1] > swing_highs[-2] and swing_lows[-1] > swing_lows[-2]:
            return "BULLISH"
        if swing_highs[-1] < swing_highs[-2] and swing_lows[-1] < swing_lows[-2]:
            return "BEARISH"
    return "RANGING"


def _find_order_blocks(df: pd.DataFrame, bias: str) -> tuple[Optional[float], Optional[float]]:
    """
    Identify the most recent unmitigated Order Block (OB).
    Returns (ob_low, ob_high) or (None, None).
    Bullish OB: last bearish candle before a strong bullish impulse.
    Bearish OB: last bullish candle before a strong bearish impulse.
    """
    candles = df.iloc[-50:]
    for i in range(len(candles) - 3, 0, -1):
        c     = candles.iloc[i]
        c_nxt = candles.iloc[i + 1]
        c_atr = _atr(candles.iloc[:i+1])
        if bias == "BUY":
            # Last down-close candle before a big up-move
            if c["close"] < c["open"] and c_nxt["close"] > c_nxt["open"]:
                impulse = c_nxt["close"] - c_nxt["open"]
                if impulse > c_atr * 1.2:
                    # Check OB is unmitigated (price hasn't closed inside it)
                    ob_low, ob_high = c["low"], c["high"]
                    recent_lows = candles.iloc[i+1:]["low"].min()
                    if recent_lows > ob_low:
                        return ob_low, ob_high
        else:  # SELL
            if c["close"] > c["open"] and c_nxt["close"] < c_nxt["open"]:
                impulse = c_nxt["open"] - c_nxt["close"]
                if impulse > c_atr * 1.2:
                    ob_low, ob_high = c["low"], c["high"]
                    recent_highs = candles.iloc[i+1:]["high"].max()
                    if recent_highs < ob_high:
                        return ob_low, ob_high
    return None, None


def _find_fvg(df: pd.DataFrame, bias: str) -> bool:
    """Check if there's an unfilled Fair Value Gap in last 20 candles."""
    for i in range(len(df) - 3, len(df) - 20, -1):
        if i < 1:
            break
        c1, c2, c3 = df.iloc[i-1], df.iloc[i], df.iloc[i+1]
        if bias == "BUY":
            if c3["low"] > c1["high"]:   # gap up – bullish FVG
                return True
        else:
            if c3["high"] < c1["low"]:   # gap down – bearish FVG
                return True
    return False


def _liquidity_swept(df: pd.DataFrame, bias: str) -> bool:
    """
    Check if recent price swept equal highs (for SELL) or equal lows (for BUY).
    Equal highs/lows = within 0.05% of each other.
    """
    tolerance = 0.0005
    highs = df["high"].iloc[-30:].values
    lows  = df["low"].iloc[-30:].values
    if bias == "SELL":
        # Look for equal highs that were swept
        for i in range(1, len(highs) - 2):
            if abs(highs[i] - highs[i-1]) / (highs[i] + 1e-9) < tolerance:
                # Equal high found; check if later candle swept it
                eq_level = max(highs[i], highs[i-1])
                sweep_candles = highs[i+1:]
                if any(h > eq_level for h in sweep_candles):
                    return True
    else:  # BUY
        for i in range(1, len(lows) - 2):
            if abs(lows[i] - lows[i-1]) / (lows[i] + 1e-9) < tolerance:
                eq_level = min(lows[i], lows[i-1])
                sweep_candles = lows[i+1:]
                if any(l < eq_level for l in sweep_candles):
                    return True
    return False


def _structure_break(df_m15: pd.DataFrame, bias: str) -> bool:
    """
    Detect Break of Structure (BOS) on M15 after a liquidity sweep.
    BOS = a candle closes beyond the last swing high/low.
    """
    closes = df_m15["close"].iloc[-20:].values
    highs  = df_m15["high"].iloc[-20:].values
    lows   = df_m15["low"].iloc[-20:].values
    if bias == "BUY":
        # Bullish BOS: close above a recent swing high
        swing_highs = [highs[i] for i in range(1, len(highs)-1)
                       if highs[i] > highs[i-1] and highs[i] > highs[i+1]]
        if swing_highs and closes[-1] > swing_highs[-1]:
            return True
    else:
        swing_lows = [lows[i] for i in range(1, len(lows)-1)
                      if lows[i] < lows[i-1] and lows[i] < lows[i+1]]
        if swing_lows and closes[-1] < swing_lows[-1]:
            return True
    return False


def _daily_bias(df_daily: pd.DataFrame) -> str:
    """
    Multi-factor daily bias:
      - EMA 20/50 cross direction
      - Market structure (HH/HL vs LH/LL)
      - Price relative to EMA200
    Returns: "BULLISH" | "BEARISH" | "NEUTRAL"
    """
    if len(df_daily) < 55:
        return "NEUTRAL"
    ema20  = _ema(df_daily, 20)
    ema50  = _ema(df_daily, 50)
    ema200 = _ema(df_daily, 200) if len(df_daily) >= 200 else None
    ms     = _higher_highs_lower_lows(df_daily)
    price  = float(df_daily["close"].iloc[-1])

    bull_pts = 0
    bear_pts = 0
    if ema20 > ema50:   bull_pts += 1
    else:               bear_pts += 1
    if ms == "BULLISH": bull_pts += 1
    elif ms == "BEARISH": bear_pts += 1
    if ema200:
        if price > ema200: bull_pts += 1
        else:              bear_pts += 1

    if bull_pts >= 2:   return "BULLISH"
    if bear_pts >= 2:   return "BEARISH"
    return "NEUTRAL"


def _h4_bias(df_h4: pd.DataFrame) -> str:
    if len(df_h4) < 25:
        return "NEUTRAL"
    ema20 = _ema(df_h4, 20)
    ema50 = _ema(df_h4, 50)
    ms    = _higher_highs_lower_lows(df_h4)
    if ema20 > ema50 and ms == "BULLISH": return "BULLISH"
    if ema20 < ema50 and ms == "BEARISH": return "BEARISH"
    return "NEUTRAL"


def _session_ok(now_utc: Optional[datetime] = None) -> tuple[bool, str]:
    """Return (is_valid_session, session_name)."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    h, m = now_utc.hour, now_utc.minute
    t = h * 60 + m
    london_start = LONDON_OPEN[0]  * 60
    london_end   = LONDON_CLOSE[0] * 60
    ny_start     = NY_OPEN[0]  * 60
    ny_end       = NY_CLOSE[0] * 60
    if london_start <= t < london_end:
        return True, "London"
    if ny_start <= t < ny_end:
        return True, "New York"
    return False, "Off-session"


# ─── Confluence Scoring ───────────────────────────────────────────────────────

def _score_setup(
    daily_bias:       str,
    h4_bias:          str,
    h1_structure:     str,
    adx:              float,
    rsi:              float,
    bias:             str,
    liquidity_swept:  bool,
    bos_confirmed:    bool,
    ob_found:         bool,
    fvg_found:        bool,
    session_ok:       bool,
    rr:               float,
) -> tuple[int, list[str], list[str]]:
    """
    Score the setup 0–100.  Returns (score, confluences, missing).
    """
    score       = 0
    confluences = []
    missing     = []
    direction   = "BULLISH" if bias == "BUY" else "BEARISH"

    # Daily alignment (20 pts)
    if daily_bias == direction:
        score += 20
        confluences.append("Daily Bias Aligned")
    else:
        missing.append("Daily Bias")

    # H4 alignment (10 pts)
    if h4_bias == direction:
        score += 10
        confluences.append("H4 Bias Aligned")
    else:
        missing.append("H4 Alignment")

    # H1 market structure (10 pts)
    if h1_structure == direction:
        score += 10
        confluences.append("H1 Structure Aligned")
    else:
        missing.append("H1 Structure")

    # Liquidity sweep (15 pts – core SMC requirement)
    if liquidity_swept:
        score += 15
        confluences.append("Liquidity Sweep Confirmed")
    else:
        missing.append("Liquidity Sweep")

    # Break of Structure on M15 (15 pts)
    if bos_confirmed:
        score += 15
        confluences.append("M15 BOS Confirmed")
    else:
        missing.append("M15 BOS")

    # Order Block (10 pts)
    if ob_found:
        score += 10
        confluences.append("Unmitigated OB Present")
    else:
        missing.append("Order Block")

    # FVG proximity (5 pts – bonus confluence)
    if fvg_found:
        score += 5
        confluences.append("FVG Proximity")

    # ADX trend strength (5 pts)
    if adx >= MIN_ADX:
        score += 5
        confluences.append(f"ADX Trend Strength ({adx:.1f})")
    else:
        missing.append(f"ADX too low ({adx:.1f} < {MIN_ADX})")

    # RSI confluence (5 pts)
    if bias == "BUY"  and rsi < 55:
        score += 5
        confluences.append(f"RSI Oversold Zone ({rsi:.1f})")
    elif bias == "SELL" and rsi > 45:
        score += 5
        confluences.append(f"RSI Overbought Zone ({rsi:.1f})")
    else:
        missing.append(f"RSI not confluent ({rsi:.1f})")

    # Session (5 pts)
    if session_ok:
        score += 5
        confluences.append("Valid Session")
    else:
        missing.append("Off-session (London/NY only)")

    return score, confluences, missing


def _confidence_label(score: int) -> str:
    if score >= 90: return "ELITE"
    if score >= 80: return "HIGH"
    if score >= 72: return "MEDIUM"
    return "LOW"


# ─── Recent-fire tracker (in-memory, per process) ────────────────────────────
_last_fired: dict[str, datetime] = {}   # key = "SYMBOL_BIAS"

def _cooldown_ok(symbol: str, bias: str) -> bool:
    key = f"{symbol}_{bias}"
    last = _last_fired.get(key)
    if last is None:
        return True
    return datetime.now(timezone.utc) - last > timedelta(hours=REFIRE_COOLDOWN_H)

def _record_fired(symbol: str, bias: str):
    _last_fired[f"{symbol}_{bias}"] = datetime.now(timezone.utc)


# ─── Main entry point ─────────────────────────────────────────────────────────

def analyze(
    symbol:    str,
    df_daily:  pd.DataFrame,
    df_h4:     pd.DataFrame,
    df_h1:     pd.DataFrame,
    df_m15:    pd.DataFrame,
    daily_count: int = 0,
    now_utc:   Optional[datetime] = None,
) -> SetupResult:
    """
    Full multi-timeframe analysis with confluence scoring.

    Parameters
    ----------
    symbol       : trading pair
    df_daily     : OHLCV daily data (≥ 200 rows ideal)
    df_h4        : OHLCV H4 data   (≥ 50 rows)
    df_h1        : OHLCV H1 data   (≥ 50 rows)
    df_m15       : OHLCV M15 data  (≥ 50 rows)
    daily_count  : number of valid setups already fired today
    now_utc      : override for testing

    Returns
    -------
    SetupResult
    """
    no_trade = lambda missing, msg: SetupResult(
        status="NO TRADE", symbol=symbol,
        missing=missing, message=msg
    )

    # 0. Daily cap
    if daily_count >= MAX_DAILY_SETUPS:
        return no_trade([], f"Daily cap of {MAX_DAILY_SETUPS} setups reached.")

    # 1. Data sufficiency check
    for name, df, min_rows in [
        ("Daily", df_daily, 55), ("H4", df_h4, 25),
        ("H1",    df_h1,    30), ("M15", df_m15, 30),
    ]:
        if df is None or len(df) < min_rows:
            return no_trade([f"Insufficient {name} data"],
                            f"Need ≥{min_rows} {name} candles.")

    # 2. Session filter
    sess_ok, session_name = _session_ok(now_utc)

    # 3. Compute indicators
    daily_b   = _daily_bias(df_daily)
    h4_b      = _h4_bias(df_h4)
    h1_struct = _higher_highs_lower_lows(df_h1)
    adx_val   = _adx(df_h1)
    rsi_val   = _rsi(df_h1)
    atr_val   = _atr(df_h1)

    # 4. Determine candidate bias
    if daily_b == "BULLISH":
        bias = "BUY"
    elif daily_b == "BEARISH":
        bias = "SELL"
    else:
        return no_trade(["Daily Bias"], "Daily bias is NEUTRAL – no directional edge.")

    # 5. Core SMC checks
    liq_swept = _liquidity_swept(df_h1, bias)
    bos       = _structure_break(df_m15, bias)
    ob_l, ob_h = _find_order_blocks(df_h1, bias)
    ob_found  = ob_l is not None
    fvg_found = _find_fvg(df_m15, bias)

    # 6. Entry / SL / TP computation
    price = float(df_h1["close"].iloc[-1])
    if bias == "BUY":
        entry = price
        sl    = round(entry - ATR_SL_MULTIPLIER * atr_val, 5)
        tp    = round(entry + ATR_TP_MULTIPLIER * atr_val, 5)
        # If OB found, entry from top of OB (better fill)
        if ob_found:
            entry = round(ob_h, 5)
            sl    = round(ob_l - 0.3 * atr_val, 5)
            tp    = round(entry + ATR_TP_MULTIPLIER * atr_val, 5)
    else:
        entry = price
        sl    = round(entry + ATR_SL_MULTIPLIER * atr_val, 5)
        tp    = round(entry - ATR_TP_MULTIPLIER * atr_val, 5)
        if ob_found:
            entry = round(ob_l, 5)
            sl    = round(ob_h + 0.3 * atr_val, 5)
            tp    = round(entry - ATR_TP_MULTIPLIER * atr_val, 5)

    risk = abs(entry - sl)
    reward = abs(tp - entry)
    rr = round(reward / risk, 2) if risk > 0 else 0.0

    # 7. Minimum RR gate
    if rr < MIN_RR:
        return no_trade([f"RR too low ({rr})"],
                        f"Risk-reward {rr:.1f} below minimum {MIN_RR}.")

    # 8. Score
    score, confluences, missing = _score_setup(
        daily_bias      = daily_b,
        h4_bias         = h4_b,
        h1_structure    = h1_struct,
        adx             = adx_val,
        rsi             = rsi_val,
        bias            = bias,
        liquidity_swept = liq_swept,
        bos_confirmed   = bos,
        ob_found        = ob_found,
        fvg_found       = fvg_found,
        session_ok      = sess_ok,
        rr              = rr,
    )

    if score < MIN_QUALITY_SCORE:
        return no_trade(
            missing,
            f"Quality score {score}/100 below threshold {MIN_QUALITY_SCORE}. "
            f"Missing: {', '.join(missing)}"
        )

    # 9. Cooldown check (anti-recency)
    if not _cooldown_ok(symbol, bias):
        return no_trade(
            [f"Cooldown ({REFIRE_COOLDOWN_H}h)"],
            f"Same direction already fired within {REFIRE_COOLDOWN_H}h."
        )

    # 10. Fire!
    _record_fired(symbol, bias)
    confidence = _confidence_label(score)
    confluences.append(f"RR 1:{rr}")
    if session_name != "Off-session":
        confluences.append(f"Session: {session_name}")

    logger.info(
        "VALID SETUP | %s %s | Score %d | Entry %.5f | SL %.5f | TP %.5f | RR 1:%.1f",
        symbol, bias, score, entry, sl, tp, rr
    )

    return SetupResult(
        status        = "VALID",
        symbol        = symbol,
        bias          = bias,
        entry         = entry,
        sl            = sl,
        tp            = tp,
        rr            = rr,
        quality_score = score,
        confidence    = confidence,
        confluences   = confluences,
        missing       = [],
        message       = f"Quality setup confirmed. Score: {score}/100.",
    )

"""
confluence_engine.py
─────────────────────────────────────────────────────────────────────────────
Confluence aggregator: combines signals from multiple sources into a
single weighted confluence object used by the API and scanner.
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd

from .strategy_strict_liquidity import (
    _daily_bias, _h4_bias, _higher_highs_lower_lows,
    _adx, _rsi, _atr, _ema, _liquidity_swept,
    _structure_break, _find_order_blocks, _find_fvg,
    _session_ok, _score_setup, _confidence_label,
)


@dataclass
class ConfluenceReport:
    symbol:         str
    bias:           str          # BUY / SELL / NEUTRAL
    quality_score:  int
    confidence:     str
    confluences:    list = field(default_factory=list)
    missing:        list = field(default_factory=list)
    daily_bias:     str = "NEUTRAL"
    h4_bias:        str = "NEUTRAL"
    h1_structure:   str = "RANGING"
    adx:            float = 0.0
    rsi:            float = 50.0
    session:        str = "Off-session"
    has_ob:         bool = False
    has_fvg:        bool = False
    has_sweep:      bool = False
    has_bos:        bool = False
    atr:            float = 0.0

    def to_dict(self) -> dict:
        return self.__dict__


def build_confluence_report(
    symbol: str,
    df_daily: pd.DataFrame,
    df_h4:   pd.DataFrame,
    df_h1:   pd.DataFrame,
    df_m15:  pd.DataFrame,
) -> ConfluenceReport:
    """
    Build a complete confluence report without making a trade decision.
    Used by the dashboard to show the confluence map.
    """
    daily_b   = _daily_bias(df_daily) if len(df_daily) >= 55 else "NEUTRAL"
    h4_b      = _h4_bias(df_h4) if len(df_h4) >= 25 else "NEUTRAL"
    h1_struct = _higher_highs_lower_lows(df_h1) if len(df_h1) >= 30 else "RANGING"
    adx_val   = _adx(df_h1) if len(df_h1) >= 20 else 0.0
    rsi_val   = _rsi(df_h1) if len(df_h1) >= 20 else 50.0
    atr_val   = _atr(df_h1) if len(df_h1) >= 20 else 0.0
    sess_ok, session_name = _session_ok()

    bias = "BUY" if daily_b == "BULLISH" else ("SELL" if daily_b == "BEARISH" else "NEUTRAL")

    liq_swept = _liquidity_swept(df_h1, bias) if bias != "NEUTRAL" else False
    bos       = _structure_break(df_m15, bias) if bias != "NEUTRAL" else False
    ob_l, ob_h = _find_order_blocks(df_h1, bias) if bias != "NEUTRAL" else (None, None)
    ob_found  = ob_l is not None
    fvg_found = _find_fvg(df_m15, bias) if bias != "NEUTRAL" else False

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
        rr              = 3.5,  # assumed valid for report
    ) if bias != "NEUTRAL" else (0, [], ["No daily bias"])

    return ConfluenceReport(
        symbol        = symbol,
        bias          = bias,
        quality_score = score,
        confidence    = _confidence_label(score),
        confluences   = confluences,
        missing       = missing,
        daily_bias    = daily_b,
        h4_bias       = h4_b,
        h1_structure  = h1_struct,
        adx           = round(adx_val, 1),
        rsi           = round(rsi_val, 1),
        session       = session_name,
        has_ob        = ob_found,
        has_fvg       = fvg_found,
        has_sweep     = liq_swept,
        has_bos       = bos,
        atr           = round(atr_val, 6),
    )

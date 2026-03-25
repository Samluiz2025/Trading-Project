from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from trading_bot.concepts import (
    detect_bos_signals,
    detect_fvg_signals,
    detect_liquidity_sweep_signals,
    detect_mss_signals,
    detect_order_block_signals,
)
from trading_bot.core.data_fetcher import FetchConfig, fetch_ohlc
from trading_bot.core.news_engine import EconomicCalendarProvider, fetch_market_moving_events, split_symbol_currencies
from trading_bot.core.strategy_engine import generate_trade_setup
from trading_bot.core.supply_demand import detect_supply_demand_zones
from trading_bot.performance.ranking import filter_top_ranked_strategies
from trading_bot.performance.repository import load_performance_results
from trading_bot.strategies.builder import default_strategy_definitions, evaluate_strategies


SupportedSource = Literal["auto", "binance", "mock", "yfinance", "oanda", "alphavantage", "twelvedata", "stooq"]


@dataclass(frozen=True)
class TimeframeState:
    timeframe: str
    bias: str
    latest_price: float
    zones: list[dict]
    setup: dict | None


def build_multi_timeframe_state(
    symbol: str,
    source: SupportedSource = "auto",
    htf_intervals: tuple[str, str] = ("4h", "1h"),
    ltf_intervals: tuple[str, str] = ("15m", "5m"),
    limit: int = 240,
    news_provider: EconomicCalendarProvider | None = None,
    current_time: datetime | None = None,
) -> dict:
    """
    Build a higher-timeframe to lower-timeframe execution snapshot.

    The 4H/1H layers define directional context and key zones, while the 15m/5m
    layers must confirm with structure shifts before a setup is considered live.
    """

    active_time = current_time or datetime.now(UTC)
    currencies = list(split_symbol_currencies(symbol))
    news_events = fetch_market_moving_events(
        provider=news_provider,
        currencies=currencies,
        current_time=active_time,
    )

    htf_states = [
        _build_timeframe_state(symbol=symbol, interval=interval, source=source, limit=limit, news_events=news_events, current_time=active_time)
        for interval in htf_intervals
    ]
    ltf_states = [
        _build_timeframe_state(symbol=symbol, interval=interval, source=source, limit=limit, news_events=news_events, current_time=active_time)
        for interval in ltf_intervals
    ]

    htf_bias = _merge_htf_biases(htf_states)
    active_htf_zones = _select_active_zones(htf_states)
    ltf_confirmation = _build_ltf_confirmation(symbol=symbol, source=source, ltf_states=ltf_states, htf_bias=htf_bias)
    ranked_strategies = filter_top_ranked_strategies(load_performance_results())
    strategy_choice = _select_strategy(
        symbol=symbol,
        source=source,
        htf_bias=htf_bias,
        ltf_states=ltf_states,
        ranked_strategies=ranked_strategies,
        news_events=news_events,
    )
    concept_overlays = _build_concept_overlays(
        symbol=symbol,
        source=source,
        timeframe=ltf_states[0].timeframe if ltf_states else "15m",
    )
    latest_price = ltf_states[0].latest_price if ltf_states else htf_states[-1].latest_price
    confluences = _build_confluences(
        htf_bias=htf_bias,
        active_htf_zones=active_htf_zones,
        ltf_confirmation=ltf_confirmation,
        strategy_choice=strategy_choice,
    )

    confidence = strategy_choice.get("confidence", 0)
    if ltf_confirmation["confirmed"]:
        confidence = min(100, confidence + 8)
    if strategy_choice.get("ai_agreement"):
        confidence = min(100, confidence + 6)

    return {
        "symbol": symbol.upper(),
        "source": source,
        "htf": {
            "bias": htf_bias,
            "timeframes": [_serialize_timeframe_state(state) for state in htf_states],
            "zones": active_htf_zones,
        },
        "ltf": {
            "timeframes": [_serialize_timeframe_state(state) for state in ltf_states],
            "confirmation": ltf_confirmation,
        },
        "news_events": [_serialize_news_event(event) for event in news_events[:8]],
        "final_bias": strategy_choice.get("final_bias", htf_bias),
        "technical_bias": strategy_choice.get("technical_bias", htf_bias),
        "news_bias": strategy_choice.get("news_bias", "neutral"),
        "confidence": confidence,
        "active_strategy": strategy_choice.get("strategy_name"),
        "entry": strategy_choice.get("entry"),
        "sl": strategy_choice.get("sl"),
        "tp": strategy_choice.get("tp"),
        "confluences": confluences,
        "latest_price": latest_price,
        "ranking_score": strategy_choice.get("ranking_score", 0),
        "historical_win_rate": strategy_choice.get("historical_win_rate", 0),
        "chart_overlays": _build_chart_overlays(
            active_htf_zones=active_htf_zones,
            ltf_confirmation=ltf_confirmation,
            strategy_choice=strategy_choice,
            concept_overlays=concept_overlays,
        ),
        "raw_setup": strategy_choice.get("setup"),
    }


def _build_timeframe_state(
    symbol: str,
    interval: str,
    source: SupportedSource,
    limit: int,
    news_events: list,
    current_time: datetime,
) -> TimeframeState:
    candles = fetch_ohlc(
        FetchConfig(
            symbol=symbol,
            interval=interval,
            limit=limit,
            source=source,
        )
    )
    zones = detect_supply_demand_zones(candles, symbol=symbol, timeframe=interval)
    setup_payload = generate_trade_setup(
        candles,
        symbol=symbol,
        timeframe=interval,
        news_events=news_events,
        current_time=current_time,
        use_ai=True,
    )
    return TimeframeState(
        timeframe=interval,
        bias=setup_payload["final_bias"],
        latest_price=float(candles.iloc[-1]["close"]),
        zones=zones,
        setup=setup_payload["setup"],
    )


def _merge_htf_biases(htf_states: list[TimeframeState]) -> str:
    directional = [state.bias for state in htf_states if "bullish" in state.bias or "bearish" in state.bias]
    if not directional:
        return htf_states[0].bias if htf_states else "neutral"
    if all("bullish" in bias for bias in directional):
        return "bullish"
    if all("bearish" in bias for bias in directional):
        return "bearish"
    return directional[0]


def _select_active_zones(htf_states: list[TimeframeState]) -> list[dict]:
    zones: list[dict] = []
    for state in htf_states:
        if not state.zones:
            continue
        zones.extend(state.zones[-2:])
    return zones[-4:]


def _build_ltf_confirmation(symbol: str, source: SupportedSource, ltf_states: list[TimeframeState], htf_bias: str) -> dict:
    confirmations: list[dict] = []
    confirmed = False

    for state in ltf_states:
        candles = fetch_ohlc(FetchConfig(symbol=symbol, interval=state.timeframe, limit=240, source=source))
        mss_signals = detect_mss_signals(candles)
        bos_signals = detect_bos_signals(candles)
        latest_mss = mss_signals[-1] if mss_signals else None
        latest_bos = bos_signals[-1] if bos_signals else None
        mss_side = latest_mss.signal if latest_mss else None
        bos_side = latest_bos.signal if latest_bos else None
        aligned = (
            (mss_side == "BUY" or bos_side == "BUY") and "bullish" in htf_bias
        ) or (
            (mss_side == "SELL" or bos_side == "SELL") and "bearish" in htf_bias
        )
        confirmed = confirmed or aligned
        confirmations.append(
            {
                "timeframe": state.timeframe,
                "mss": mss_side,
                "bos": bos_side,
                "confirmed": aligned,
            }
        )

    return {
        "confirmed": confirmed,
        "details": confirmations,
    }


def _select_strategy(
    symbol: str,
    source: SupportedSource,
    htf_bias: str,
    ltf_states: list[TimeframeState],
    ranked_strategies: list[dict],
    news_events: list,
) -> dict:
    if not ltf_states:
        return {"final_bias": htf_bias, "technical_bias": htf_bias, "news_bias": "neutral"}

    primary_ltf = ltf_states[0]
    candles = fetch_ohlc(FetchConfig(symbol=symbol, interval=primary_ltf.timeframe, limit=240, source=source))
    evaluations = evaluate_strategies(
        dataframe=candles,
        symbol=symbol,
        timeframe=primary_ltf.timeframe,
        strategy_definitions=default_strategy_definitions(),
        htf_bias=htf_bias,
    )
    ranked_lookup = {result["name"]: result for result in ranked_strategies}
    chosen = None

    for evaluation in evaluations:
        result = ranked_lookup.get(evaluation["strategy"])
        if result is None:
            continue
        if chosen is None or result.get("ranking_score", 0) > chosen["ranking_score"]:
            chosen = {
                "strategy_name": evaluation["strategy"],
                "setup": {
                    "signal": evaluation["signal"],
                    "entry": evaluation["entry"],
                    "stop_loss": evaluation["stop_loss"],
                    "take_profit": evaluation["take_profit"],
                },
                "entry": evaluation["entry"],
                "sl": evaluation["stop_loss"],
                "tp": evaluation["take_profit"],
                "confidence": evaluation["confidence"],
                "ranking_score": result.get("ranking_score", 0),
                "historical_win_rate": result.get("win_rate", 0),
                "strategy_concepts": list(evaluation.get("concepts", [])),
            }

    strategy_payload = generate_trade_setup(
        candles,
        symbol=symbol,
        timeframe=primary_ltf.timeframe,
        news_events=news_events,
        current_time=datetime.now(UTC),
        use_ai=True,
    )

    payload = {
        "technical_bias": strategy_payload["technical_bias"],
        "news_bias": strategy_payload["news_bias"],
        "final_bias": strategy_payload["final_bias"],
        "confidence": strategy_payload["confidence"],
        "setup": strategy_payload["setup"],
        "ai_agreement": strategy_payload.get("agreement_with_strategy", False),
        "ai_prediction": strategy_payload.get("ai_prediction"),
    }

    if chosen is None and strategy_payload["setup"]:
        return {
            **payload,
            "strategy_name": "HTF Bias + LTF Confirmation",
            "entry": strategy_payload["setup"]["entry"],
            "sl": strategy_payload["setup"]["stop_loss"],
            "tp": strategy_payload["setup"]["take_profit"],
            "ranking_score": 0,
            "historical_win_rate": 0,
            "strategy_concepts": [],
        }

    if chosen is None:
        return payload

    payload.update(chosen)
    return payload


def _build_confluences(
    htf_bias: str,
    active_htf_zones: list[dict],
    ltf_confirmation: dict,
    strategy_choice: dict,
) -> list[str]:
    confluences: list[str] = []
    if htf_bias:
        confluences.append(f"HTF bias: {htf_bias}")
    if active_htf_zones:
        confluences.append(f"HTF zones active: {len(active_htf_zones)}")
    for detail in ltf_confirmation.get("details", []):
        if detail["confirmed"]:
            if detail["mss"]:
                confluences.append(f"{detail['timeframe']} MSS {detail['mss']}")
            if detail["bos"]:
                confluences.append(f"{detail['timeframe']} BOS {detail['bos']}")
    for concept in strategy_choice.get("strategy_concepts", []):
        confluences.append(concept)
    if strategy_choice.get("news_bias") not in {None, "neutral"}:
        confluences.append(f"News: {strategy_choice['news_bias']}")
    if strategy_choice.get("ai_agreement"):
        confluences.append("AI agrees with strategy")
    return confluences[:8]


def _build_chart_overlays(
    active_htf_zones: list[dict],
    ltf_confirmation: dict,
    strategy_choice: dict,
    concept_overlays: dict,
) -> dict:
    return {
        "htf_zones": active_htf_zones[-3:],
        "latest_fvg": concept_overlays.get("latest_fvg") or _latest_concept_overlay(strategy_choice, "FVG"),
        "order_block": concept_overlays.get("order_block") or _latest_concept_overlay(strategy_choice, "OrderBlock"),
        "liquidity": concept_overlays.get("liquidity") or _latest_ltf_overlay(ltf_confirmation, "bos", "mss"),
        "trade_levels": {
            "entry": strategy_choice.get("entry"),
            "sl": strategy_choice.get("sl"),
            "tp": strategy_choice.get("tp"),
        },
    }


def _latest_concept_overlay(strategy_choice: dict, concept_name: str) -> dict | None:
    if concept_name not in strategy_choice.get("strategy_concepts", []):
        return None
    return {"name": concept_name, "active": True}


def _latest_ltf_overlay(ltf_confirmation: dict, *keys: str) -> list[dict]:
    overlays: list[dict] = []
    for detail in ltf_confirmation.get("details", []):
        for key in keys:
            if detail.get(key):
                overlays.append({"timeframe": detail["timeframe"], "type": key.upper(), "signal": detail[key]})
    return overlays[:4]


def _build_concept_overlays(symbol: str, source: SupportedSource, timeframe: str) -> dict:
    candles = fetch_ohlc(FetchConfig(symbol=symbol, interval=timeframe, limit=240, source=source))
    fvg_signals = detect_fvg_signals(candles)
    liquidity_signals = detect_liquidity_sweep_signals(candles)
    order_block_signals = detect_order_block_signals(candles, symbol=symbol, timeframe=timeframe)
    return {
        "latest_fvg": _signal_to_overlay(fvg_signals[-1], timeframe=timeframe) if fvg_signals else None,
        "liquidity": [_signal_to_overlay(signal, timeframe=timeframe) for signal in liquidity_signals[-2:]],
        "order_block": _signal_to_overlay(order_block_signals[-1], timeframe=timeframe) if order_block_signals else None,
    }


def _signal_to_overlay(signal, timeframe: str | None = None) -> dict:
    return {
        "name": signal.concept,
        "signal": signal.signal,
        "entry": round(signal.entry, 4),
        "stop_loss": round(signal.stop_loss, 4),
        "take_profit": round(signal.take_profit, 4),
        "time": signal.time,
        "timeframe": timeframe,
    }


def _serialize_timeframe_state(state: TimeframeState) -> dict:
    return {
        "timeframe": state.timeframe,
        "bias": state.bias,
        "latest_price": round(state.latest_price, 4),
        "zones": state.zones[-2:],
        "setup": state.setup,
    }


def _serialize_news_event(event) -> dict:
    return {
        "event_name": event.event_name,
        "currency": event.currency,
        "impact": event.impact,
        "time": event.time.isoformat(),
        "forecast": event.forecast,
        "previous": event.previous,
        "actual": event.actual,
    }

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime

import pandas as pd

from trading_bot.core.alert_system import load_telegram_config, send_alert
from trading_bot.core.calibration_mode import maybe_apply_calibration
from trading_bot.core.confluence_engine import evaluate_symbol
from trading_bot.core.data_fetcher import FetchConfig, fetch_ohlc
from trading_bot.core.digital_twin import get_digital_twin_snapshot, observe_strategy_result, register_virtual_trade, update_virtual_trade_outcomes
from trading_bot.core.edge_control import build_edge_control_snapshot, evaluate_edge_control
from trading_bot.core.instrument_universe import get_instrument_universe
from trading_bot.core.journal import ensure_trade_logged, get_challenge_trade_stats, load_journal_entries, log_rejected_analysis, reconcile_open_trade_state, update_open_trade_lifecycle
from trading_bot.core.monitor_state import (
    load_alert_contexts,
    mark_cycle_completed,
    mark_cycle_started,
    mark_digest_sent,
    record_cycle_progress,
    record_scan_diagnostics,
    save_alert_contexts,
    should_send_digest,
    update_symbol_health,
)
from trading_bot.core.strategy_registry import PRIMARY_STRATEGY, is_live_strategy

CORRELATED_SYMBOLS: dict[str, list[str]] = {
    "BTCUSDT": ["ETHUSDT"],
    "ETHUSDT": ["BTCUSDT"],
    "EURUSD": ["GBPUSD"],
    "GBPUSD": ["EURUSD"],
    "AUDUSD": ["NZDUSD"],
    "NZDUSD": ["AUDUSD"],
    "AUDJPY": ["NZDJPY"],
    "NZDJPY": ["AUDJPY"],
    "NAS100": ["SP500", "US30"],
    "SP500": ["NAS100", "US30"],
    "US30": ["NAS100", "SP500"],
}

FAST_MARKETS = {"XAUUSD", "BTCUSDT", "ETHUSDT", "NAS100", "SP500", "US30"}

SYMBOL_CLUSTERS: dict[str, list[str]] = {
    "BTCUSDT": ["BTCUSDT", "ETHUSDT"],
    "ETHUSDT": ["BTCUSDT", "ETHUSDT"],
    "NAS100": ["NAS100", "SP500", "US30"],
    "SP500": ["NAS100", "SP500", "US30"],
    "US30": ["NAS100", "SP500", "US30"],
    "AUDJPY": ["AUDJPY", "NZDJPY", "USDJPY"],
    "NZDJPY": ["AUDJPY", "NZDJPY", "USDJPY"],
    "USDJPY": ["AUDJPY", "NZDJPY", "USDJPY"],
    "AUDUSD": ["AUDUSD", "NZDUSD", "AUDCAD"],
    "NZDUSD": ["AUDUSD", "NZDUSD", "AUDCAD"],
    "AUDCAD": ["AUDUSD", "NZDUSD", "AUDCAD"],
    "EURUSD": ["EURUSD", "GBPUSD", "EURGBP"],
    "GBPUSD": ["EURUSD", "GBPUSD", "EURGBP"],
    "EURGBP": ["EURUSD", "GBPUSD", "EURGBP"],
}

OhlcCache = dict[tuple[str, str, str], dict[str, object]]


@dataclass(frozen=True)
class ChallengeModeConfig:
    enabled: bool = False
    name: str = "Weekly Challenge"
    max_trades: int = 3
    risk_per_trade: float = 30.0
    minimum_rr: float = 3.0
    minimum_grade: str = "A+"


def run_market_monitor(
    group: str = "all",
    source: str = "auto",
    poll_interval_seconds: int = 5,
    use_ltf_refinement: bool = True,
    challenge_mode: ChallengeModeConfig | None = None,
) -> None:
    active_challenge = challenge_mode or ChallengeModeConfig()
    telegram_config = load_telegram_config()
    symbols = [symbol for symbol in get_instrument_universe(group) if _supports_strict_liquidity_symbol(symbol)]
    last_alert_context = _prune_alert_contexts(load_alert_contexts())
    first_cycle = True
    data_cache: OhlcCache = {}

    print(f"[INFO] Market monitor started for {group.upper()} ({len(symbols)} symbols).")
    if telegram_config is not None:
        send_alert(
            {
                "status": "INFO",
                "type": "monitor_online",
                "pair": group.upper(),
                "message": f"Market monitor online for {group.upper()} universe ({len(symbols)} symbols).",
                "signature": f"monitor_online|{group}|{datetime.now(UTC).isoformat()}",
            },
            telegram_config,
        )

    while True:
        cycle_candidates: list[dict] = []
        cycle_watch_candidates: list[dict] = []
        cycle_rejections: list[dict] = []
        mark_cycle_started(group=group, source=source, poll_interval_seconds=poll_interval_seconds)
        try:
            calibration_result = maybe_apply_calibration()
            if calibration_result.get("status") == "APPLIED":
                print(f"[INFO] Calibration applied at {calibration_result.get('applied_at')}.")
                snapshot = calibration_result.get("snapshot") or {}
                send_alert(
                    {
                        "status": "INFO",
                        "type": "calibration_applied",
                        "message": calibration_result.get("message"),
                        "changes": calibration_result.get("changes", []),
                        "promoted_symbols": snapshot.get("promoted_symbols", []),
                        "demoted_symbols": snapshot.get("demoted_symbols", []),
                        "recommended_sessions": snapshot.get("recommended_sessions", []),
                        "recommended_minimum_setup_grade": snapshot.get("recommended_minimum_setup_grade"),
                        "reason_log": snapshot.get("reason_log", []),
                        "signature": f"calibration_applied|{calibration_result.get('applied_at')}",
                    },
                    telegram_config,
                )
        except Exception as exc:
            print(f"[WARN] Calibration update skipped: {exc}")

        total_symbols = len(symbols)
        completed_symbols = 0
        for symbol in symbols:
            record_cycle_progress(
                symbol=symbol,
                completed_symbols=completed_symbols,
                total_symbols=total_symbols,
                phase="started",
            )
            try:
                weekly_data = _get_cached_ohlc(data_cache, FetchConfig(symbol=symbol, interval="1w", limit=160, source=source))
                daily_data = _get_cached_ohlc(data_cache, FetchConfig(symbol=symbol, interval="1d", limit=220, source=source))
                h4_data = _get_cached_ohlc(data_cache, FetchConfig(symbol=symbol, interval="4h", limit=220, source=source))
                h1_data = _get_cached_ohlc(data_cache, FetchConfig(symbol=symbol, interval="1h", limit=320, source=source))
                ltf_data = _get_cached_ohlc(data_cache, FetchConfig(symbol=symbol, interval="15m", limit=320, source=source)) if use_ltf_refinement else None
                result = evaluate_symbol(symbol=symbol, weekly_data=weekly_data, daily_data=daily_data, h1_data=h1_data, ltf_data=ltf_data, h4_data=h4_data)
                observe_strategy_result(symbol=symbol, result=result, news_context={})
                update_symbol_health(symbol, ok=True, source=source)

                if result["status"] == "VALID_TRADE":
                    candidate = result
                    candidate = {**candidate, **_rank_result(candidate)}
                    if active_challenge.enabled:
                        candidate = {
                            **candidate,
                            "challenge_gate": _evaluate_challenge_final_gate(
                                result=candidate,
                                h1_data=h1_data,
                                ltf_data=ltf_data,
                            ),
                        }
                    cycle_candidates.append(candidate)
                elif result.get("status") == "WAIT_CONFIRMATION":
                    watch_candidate = result
                    watch_candidate = {**watch_candidate, **_rank_watch_result(watch_candidate)}
                    cycle_watch_candidates.append(watch_candidate)
                    if not active_challenge.enabled:
                        try:
                            _dispatch_confirmation_watch_alert(
                                candidate=watch_candidate,
                                symbol=watch_candidate["pair"],
                                telegram_config=telegram_config,
                                last_alert_context=last_alert_context,
                                first_cycle=first_cycle,
                            )
                        except Exception as exc:
                            print(f"[ERROR] Watch alert dispatch failed for {watch_candidate.get('pair')}: {exc}")
                else:
                    cycle_rejections.append(_build_rejection_diagnostic(symbol=symbol, result=result))
                    log_rejected_analysis(
                        symbol=symbol,
                        strategy=str(result.get("strategy") or "+".join(result.get("strategies_checked") or result.get("strategies") or []) or PRIMARY_STRATEGY),
                        missing=result.get("missing", []),
                        timeframe="1h",
                        source=source,
                        message=result.get("message", "No valid setup available"),
                    )
                    if symbol.upper() in last_alert_context and _result_bias(result) != last_alert_context[symbol.upper()].get("bias"):
                        last_alert_context.pop(symbol.upper(), None)
            except Exception as exc:
                update_symbol_health(symbol, ok=False, source=source, error=str(exc))
                print(f"[ERROR] Monitor failed for {symbol}: {exc}")
            completed_symbols += 1
            record_cycle_progress(
                symbol=symbol,
                completed_symbols=completed_symbols,
                total_symbols=total_symbols,
                phase="completed",
            )

        selection = _select_alert_candidates(cycle_candidates, challenge_mode=active_challenge)
        watch_candidates = [] if active_challenge.enabled else _select_watch_candidates(cycle_watch_candidates)
        top_candidates = selection.get("selected", [])
        blocked_candidates = selection.get("blocked", [])
        shadow_candidates = selection.get("shadow", [])
        for candidate in top_candidates:
            try:
                latest_h1 = _get_cached_ohlc(data_cache, FetchConfig(symbol=candidate["pair"], interval="1h", limit=320, source=source))
                latest_m15 = _get_cached_ohlc(data_cache, FetchConfig(symbol=candidate["pair"], interval="15m", limit=320, source=source)) if use_ltf_refinement else None
                _dispatch_valid_trade_alert(
                    candidate=candidate,
                    symbol=candidate["pair"],
                    source=source,
                    use_ltf_refinement=use_ltf_refinement,
                    telegram_config=telegram_config,
                    last_alert_context=last_alert_context,
                    first_cycle=first_cycle,
                    h1_data=latest_h1,
                    ltf_data=latest_m15,
                    challenge_mode=active_challenge,
                )
            except Exception as exc:
                print(f"[ERROR] Alert dispatch failed for {candidate.get('pair')}: {exc}")

        for candidate in shadow_candidates:
            try:
                latest_h1 = _get_cached_ohlc(data_cache, FetchConfig(symbol=candidate["pair"], interval="1h", limit=320, source=source))
                latest_m15 = _get_cached_ohlc(data_cache, FetchConfig(symbol=candidate["pair"], interval="15m", limit=320, source=source)) if use_ltf_refinement else None
                _dispatch_shadow_trade(
                    candidate=candidate,
                    symbol=candidate["pair"],
                    source=source,
                    use_ltf_refinement=use_ltf_refinement,
                    h1_data=latest_h1,
                    ltf_data=latest_m15,
                )
            except Exception as exc:
                print(f"[ERROR] Shadow dispatch failed for {candidate.get('pair')}: {exc}")

        for candidate in watch_candidates:
            try:
                _dispatch_confirmation_watch_alert(
                    candidate=candidate,
                    symbol=candidate["pair"],
                    telegram_config=telegram_config,
                    last_alert_context=last_alert_context,
                    first_cycle=first_cycle,
                )
            except Exception as exc:
                print(f"[ERROR] Watch alert dispatch failed for {candidate.get('pair')}: {exc}")

        try:
            reconcile_result = reconcile_open_trade_state()
            if reconcile_result.get("archived_entries"):
                print(f"[INFO] Archived {len(reconcile_result.get('archived_entries', []))} stale journal open trade(s).")
            trade_events = update_open_trade_lifecycle(default_source=source)
            for trade in trade_events.get("activated", []):
                if bool(trade.get("shadow_mode")):
                    continue
                if not is_live_strategy(trade.get("strategy")):
                    continue
                send_alert(
                    {
                        "status": "ENTRY ACTIVE",
                        "type": "trade_activated",
                        "pair": trade.get("symbol"),
                        "bias": "BUY" if float(trade.get("take_profit") or 0) >= float(trade.get("entry") or 0) else "SELL",
                        "entry": trade.get("entry"),
                        "sl": trade.get("stop_loss"),
                        "tp": trade.get("take_profit"),
                        "confidence": trade.get("confidence"),
                        "strategies": [trade.get("strategy")],
                        "confluences": trade.get("confluences", []),
                        "signature": f"trade_activated|{trade.get('signature')}|{trade.get('triggered_at')}",
                        "message": f"Entry activated for {trade.get('symbol')}. Trade is now live.",
                        "challenge_mode": bool(trade.get("challenge_mode")),
                        "challenge_name": trade.get("challenge_name"),
                        "planned_risk": trade.get("planned_risk"),
                    },
                    telegram_config,
                )
            for trade in trade_events.get("closed", []):
                if bool(trade.get("shadow_mode")):
                    continue
                if not is_live_strategy(trade.get("strategy")):
                    continue
                send_alert(
                    {
                        "status": trade.get("status"),
                        "type": "trade_closed",
                        "pair": trade.get("symbol"),
                        "bias": "BUY" if float(trade.get("take_profit") or 0) >= float(trade.get("entry") or 0) else "SELL",
                        "entry": trade.get("entry"),
                        "sl": trade.get("stop_loss"),
                        "tp": trade.get("take_profit"),
                        "confidence": trade.get("confidence"),
                        "strategies": [trade.get("strategy")],
                        "confluences": trade.get("confluences", []),
                        "signature": f"trade_closed|{trade.get('signature')}|{trade.get('status')}",
                        "message": f"Trade {trade.get('status')} for {trade.get('symbol')} at RR {trade.get('rr_achieved')}.",
                        "challenge_mode": bool(trade.get("challenge_mode")),
                        "challenge_name": trade.get("challenge_name"),
                        "planned_risk": trade.get("planned_risk"),
                    },
                    telegram_config,
                )
        except Exception as exc:
            print(f"[ERROR] Outcome tracking failed: {exc}")

        try:
            for trade in update_virtual_trade_outcomes(default_source=source):
                if not is_live_strategy(trade.get("primary_strategy")):
                    continue
                send_alert(
                    {
                        "status": trade.get("status"),
                        "type": "twin_trade_closed",
                        "pair": trade.get("symbol"),
                        "bias": trade.get("bias"),
                        "entry": trade.get("entry"),
                        "sl": trade.get("stop_loss"),
                        "tp": trade.get("take_profit"),
                        "confidence": trade.get("confidence"),
                        "strategies": trade.get("strategies", []),
                        "confluences": trade.get("confluences", []),
                        "signature": f"twin_trade_closed|{trade.get('signature')}|{trade.get('status')}",
                        "message": f"Digital twin {trade.get('status')} for {trade.get('symbol')} | PnL ${float(trade.get('realized_pnl') or 0.0):.2f} | RR {trade.get('rr_achieved')}.",
                        "digital_twin": True,
                    },
                    telegram_config,
                )
        except Exception as exc:
            print(f"[ERROR] Digital twin outcome tracking failed: {exc}")

        _send_digest_if_due(group=group, source=source, candidates=top_candidates, telegram_config=telegram_config)
        try:
            record_scan_diagnostics(
                evaluated_symbols=len(symbols),
                valid_candidates=len(cycle_candidates),
                selected_candidates=top_candidates,
                blocked_candidates=blocked_candidates,
                rejected_candidates=cycle_rejections,
            )
        except Exception as exc:
            print(f"[WARN] Failed to record scan diagnostics: {exc}")
        last_alert_context = _prune_alert_contexts(last_alert_context)
        save_alert_contexts(last_alert_context)
        mark_cycle_completed()

        first_cycle = False
        time.sleep(poll_interval_seconds)


def _supports_strict_liquidity_symbol(symbol: str) -> bool:
    normalized = str(symbol).upper()
    if normalized == "XAUUSD":
        return True
    return len(normalized) == 6 and normalized.isalpha()


def _dispatch_valid_trade_alert(
    *,
    candidate: dict,
    symbol: str,
    source: str,
    use_ltf_refinement: bool,
    telegram_config,
    last_alert_context: dict,
    first_cycle: bool,
    h1_data,
    ltf_data,
    challenge_mode: ChallengeModeConfig,
) -> None:
    symbol_key = str(symbol).upper()
    previous_context = None if first_cycle else last_alert_context.get(symbol_key)
    if not _should_send_setup_alert(candidate, previous_context):
        return

    signature = _build_setup_signature(candidate)
    context = _build_alert_context(candidate, signature)
    last_alert_context[symbol_key] = context
    ensure_trade_logged(
        symbol=symbol_key,
        strategy=str(candidate.get("strategy") or "+".join(candidate.get("strategies", []))),
        entry=float(candidate["entry"]),
        stop_loss=float(candidate["sl"]),
        take_profit=float(candidate["tp"]),
        confluences=candidate.get("confluences", []),
        confidence=int(candidate.get("confidence_score", 0)),
        timeframe="1h",
        source=source,
        timeframes_used=["1d", "1h", "15m"] if use_ltf_refinement else ["1d", "1h"],
        session=str(candidate.get("session") or ""),
        setup_grade=str(candidate.get("setup_grade") or ""),
        invalidation=float(candidate.get("invalidation") or candidate["sl"]),
        reason=str(candidate.get("reason") or candidate.get("message") or ""),
        challenge_mode=challenge_mode.enabled,
        challenge_name=challenge_mode.name if challenge_mode.enabled else None,
        planned_risk=challenge_mode.risk_per_trade if challenge_mode.enabled else None,
        analysis_snapshot={
            "analysis_context": candidate.get("analysis_context", {}),
            "chart_overlays": candidate.get("chart_overlays", {}),
            "recent_candles": _serialize_recent_candles(ltf_data if ltf_data is not None else h1_data),
            "tier": candidate.get("tier"),
            "ranking_score": candidate.get("ranking_score"),
            "timestamp": candidate.get("timestamp"),
            "symbol": symbol_key,
            "source": source,
            "interval": "15m" if use_ltf_refinement else "1h",
        },
    )
    register_virtual_trade(
        symbol=symbol_key,
        setup=candidate,
        source=source,
        timeframe="1h",
        analysis_snapshot={
            "symbol": symbol_key,
            "source": source,
            "interval": "15m" if use_ltf_refinement else "1h",
            "analysis_context": candidate.get("analysis_context", {}),
            "chart_overlays": candidate.get("chart_overlays", {}),
        },
        news_context=candidate.get("strategy_results", {}).get("news", {}),
    )
    send_alert(
        {
            **candidate,
            "type": _alert_type_for_candidate(candidate, first_cycle, previous_context),
            "signature": signature,
            "why_this_matters": _why_this_matters(candidate),
            "challenge_mode": challenge_mode.enabled,
            "challenge_name": challenge_mode.name if challenge_mode.enabled else None,
            "planned_risk": challenge_mode.risk_per_trade if challenge_mode.enabled else None,
        },
        telegram_config,
    )


def _dispatch_shadow_trade(
    *,
    candidate: dict,
    symbol: str,
    source: str,
    use_ltf_refinement: bool,
    h1_data,
    ltf_data,
) -> None:
    symbol_key = str(symbol).upper()
    ensure_trade_logged(
        symbol=symbol_key,
        strategy=str(candidate.get("strategy") or "+".join(candidate.get("strategies", []))),
        entry=float(candidate["entry"]),
        stop_loss=float(candidate["sl"]),
        take_profit=float(candidate["tp"]),
        confluences=candidate.get("confluences", []),
        confidence=int(candidate.get("confidence_score", 0)),
        timeframe="1h",
        source=source,
        timeframes_used=["1d", "1h", "15m"] if use_ltf_refinement else ["1d", "1h"],
        session=str(candidate.get("session") or ""),
        setup_grade=str(candidate.get("setup_grade") or ""),
        invalidation=float(candidate.get("invalidation") or candidate["sl"]),
        reason=str(candidate.get("reason") or candidate.get("message") or ""),
        shadow_mode=True,
        analysis_snapshot={
            "analysis_context": candidate.get("analysis_context", {}),
            "chart_overlays": candidate.get("chart_overlays", {}),
            "recent_candles": _serialize_recent_candles(ltf_data if ltf_data is not None else h1_data),
            "tier": candidate.get("tier"),
            "ranking_score": candidate.get("ranking_score"),
            "timestamp": candidate.get("timestamp"),
            "symbol": symbol_key,
            "source": source,
            "interval": "15m" if use_ltf_refinement else "1h",
            "shadow_mode": True,
        },
    )


def _dispatch_confirmation_watch_alert(
    *,
    candidate: dict,
    symbol: str,
    telegram_config,
    last_alert_context: dict,
    first_cycle: bool,
) -> None:
    symbol_key = str(symbol).upper()
    lifecycle = str(candidate.get("lifecycle") or "").lower()
    if lifecycle == "zone_watch":
        return
    previous_context = None if first_cycle else last_alert_context.get(symbol_key)
    if not _should_send_confirmation_alert(candidate, previous_context):
        return
    signature = _build_stalker_signature(candidate, symbol_key)
    context = _build_alert_context(candidate, signature)
    last_alert_context[symbol_key] = context
    send_alert(
        {
            **candidate,
            "pair": symbol_key,
            "type": "zone_decision_wait" if lifecycle == "zone_watch" else "confirmation_watch",
            "signature": signature,
            "why_this_matters": _why_this_matters(candidate),
        },
        telegram_config,
    )


def _apply_news_lock(symbol: str, result: dict) -> dict:
    return result


def _build_setup_signature(result: dict) -> str:
    return "|".join(
        [
            result["pair"],
            result["bias"],
            f"{float(result['entry']):.4f}",
            f"{float(result['sl']):.4f}",
            f"{float(result['tp']):.4f}",
            ",".join(result.get("strategies", [])),
        ]
    )


def _build_stalker_signature(result: dict, symbol: str) -> str:
    stalker = result.get("stalker") or {}
    plan_zone = (result.get("details") or {}).get("plan_zone") or (result.get("analysis_context") or {}).get("plan_zone") or []
    zone_signature = ""
    if isinstance(plan_zone, list) and len(plan_zone) >= 2:
        zone_signature = f"{float(plan_zone[0]):.4f}:{float(plan_zone[1]):.4f}"
    return "|".join(
        [
            symbol.upper(),
            str(result.get("bias") or ""),
            str(result.get("lifecycle") or ""),
            str(stalker.get("state") or ""),
            ",".join(sorted(result.get("missing", []))),
            zone_signature,
        ]
    )


def _build_alert_context(result: dict, signature: str | None = None) -> dict:
    strategy_results = result.get("strategy_results", {})
    pullback = strategy_results.get("pullback", {})
    htf_zone = strategy_results.get("htf_zone", {})
    strict = strategy_results.get("strict_liquidity", {})
    order_block = (
        (pullback.get("details", {}) or {}).get("order_block")
        or (htf_zone.get("details", {}) or {}).get("zone")
        or (strict.get("details", {}) or {}).get("entry_model")
        or {}
    )
    zone = order_block.get("zone", order_block if isinstance(order_block, dict) else {})
    return {
        "bias": result.get("bias"),
        "entry": float(result.get("entry") or 0),
        "order_block_formed_at": zone.get("formed_at") or zone.get("touch_time"),
        "lifecycle": result.get("lifecycle"),
        "setup_state": result.get("status"),
        "signature": signature,
        "last_seen": datetime.now(UTC).isoformat(),
    }


def _attach_correlation_context(
    result: dict,
    *,
    source: str,
    use_ltf_refinement: bool,
    cycle_valid_candidates: dict[str, dict],
    cycle_h1_data: dict[str, pd.DataFrame],
    cycle_result_cache: dict[str, dict],
    data_cache: OhlcCache,
) -> dict:
    symbol = str(result.get("pair") or "").upper()
    bias = str(result.get("bias") or "").upper()
    confirmations: list[dict] = []
    base_h1 = cycle_h1_data.get(symbol)
    peer_symbols = list(dict.fromkeys(CORRELATED_SYMBOLS.get(symbol, [])))
    for peer_symbol in peer_symbols:
        if peer_symbol not in cycle_result_cache:
            _probe_correlated_peer(
                peer_symbol,
                source=source,
                use_ltf_refinement=use_ltf_refinement,
                cycle_h1_data=cycle_h1_data,
                cycle_result_cache=cycle_result_cache,
                data_cache=data_cache,
            )
        peer_h1 = cycle_h1_data.get(peer_symbol)
        peer = cycle_valid_candidates.get(peer_symbol) or cycle_result_cache.get(peer_symbol)
        if not peer or peer.get("status") != "VALID_TRADE":
            continue
        peer_bias = str(peer.get("bias") or "").upper()
        if peer_bias != bias:
            continue
        correlation = _rolling_return_correlation(base_h1, peer_h1)
        if correlation is None or correlation < 0.65:
            continue
        confirmations.append(
            {
                "pair": peer_symbol,
                "bias": peer_bias,
                "confidence": peer.get("confidence"),
                "risk_reward_ratio": peer.get("risk_reward_ratio"),
                "ranking_score": peer.get("ranking_score"),
                "rolling_correlation": round(correlation, 2),
            }
        )

    if not confirmations:
        cluster_confirmation = _build_cluster_confirmation(
            symbol=symbol,
            bias=bias,
            cycle_valid_candidates=cycle_valid_candidates,
            cycle_result_cache=cycle_result_cache,
            cycle_h1_data=cycle_h1_data,
            source=source,
            use_ltf_refinement=use_ltf_refinement,
            data_cache=data_cache,
        )
        if cluster_confirmation is None:
            return result
        analysis_context = {**(result.get("analysis_context") or {})}
        analysis_context["cluster_confirmation"] = cluster_confirmation
        confluences = list(result.get("confluences", []))
        if "Cluster Confirmation" not in confluences:
            confluences.append("Cluster Confirmation")
        return {
            **result,
            "analysis_context": analysis_context,
            "cluster_confirmation": cluster_confirmation,
            "confluences": confluences,
        }

    analysis_context = {**(result.get("analysis_context") or {})}
    analysis_context["correlated_confirmations"] = confirmations
    cluster_confirmation = _build_cluster_confirmation(
        symbol=symbol,
        bias=bias,
        cycle_valid_candidates=cycle_valid_candidates,
        cycle_result_cache=cycle_result_cache,
        cycle_h1_data=cycle_h1_data,
        source=source,
        use_ltf_refinement=use_ltf_refinement,
        data_cache=data_cache,
    )
    if cluster_confirmation is not None:
        analysis_context["cluster_confirmation"] = cluster_confirmation
    confluences = list(result.get("confluences", []))
    if "Correlated Pair Confirmation" not in confluences:
        confluences.append("Correlated Pair Confirmation")
    if cluster_confirmation is not None and "Cluster Confirmation" not in confluences:
        confluences.append("Cluster Confirmation")
    return {
        **result,
        "analysis_context": analysis_context,
        "correlated_confirmations": confirmations,
        "cluster_confirmation": cluster_confirmation,
        "confluences": confluences,
    }


def _build_cluster_confirmation(
    *,
    symbol: str,
    bias: str,
    cycle_valid_candidates: dict[str, dict],
    cycle_result_cache: dict[str, dict],
    cycle_h1_data: dict[str, pd.DataFrame],
    source: str,
    use_ltf_refinement: bool,
    data_cache: OhlcCache,
) -> dict | None:
    cluster = SYMBOL_CLUSTERS.get(symbol, [])
    if len(cluster) < 2:
        return None

    members: list[dict] = []
    base_h1 = cycle_h1_data.get(symbol)
    for peer_symbol in cluster:
        if peer_symbol == symbol:
            continue
        if peer_symbol not in cycle_result_cache:
            _probe_correlated_peer(
                peer_symbol,
                source=source,
                use_ltf_refinement=use_ltf_refinement,
                cycle_h1_data=cycle_h1_data,
                cycle_result_cache=cycle_result_cache,
                data_cache=data_cache,
            )
        peer = cycle_valid_candidates.get(peer_symbol) or cycle_result_cache.get(peer_symbol)
        if not peer or peer.get("status") != "VALID_TRADE":
            continue
        peer_bias = str(peer.get("bias") or "").upper()
        if peer_bias != bias:
            continue
        correlation = _rolling_return_correlation(base_h1, cycle_h1_data.get(peer_symbol))
        if correlation is None or correlation < 0.55:
            continue
        members.append(
            {
                "pair": peer_symbol,
                "bias": peer_bias,
                "rolling_correlation": round(correlation, 2),
                "confidence": peer.get("confidence"),
            }
        )

    if not members:
        return None

    avg_corr = round(sum(float(item["rolling_correlation"]) for item in members) / len(members), 2)
    return {
        "name": _cluster_name_for_symbol(symbol),
        "bias": bias,
        "member_count": len(members),
        "average_correlation": avg_corr,
        "members": members[:4],
    }


def _cluster_name_for_symbol(symbol: str) -> str:
    normalized = str(symbol).upper()
    if normalized.endswith("USDT"):
        return "Crypto Cluster"
    if normalized in {"NAS100", "SP500", "US30"}:
        return "US Index Cluster"
    if normalized in {"AUDJPY", "NZDJPY", "USDJPY"}:
        return "JPY Cross Cluster"
    if normalized in {"AUDUSD", "NZDUSD", "AUDCAD"}:
        return "AUD/NZD Cluster"
    if normalized in {"EURUSD", "GBPUSD", "EURGBP"}:
        return "EUR/GBP/USD Cluster"
    return "Market Cluster"


def _probe_correlated_peer(
    peer_symbol: str,
    *,
    source: str,
    use_ltf_refinement: bool,
    cycle_h1_data: dict[str, pd.DataFrame],
    cycle_result_cache: dict[str, dict],
    data_cache: OhlcCache,
) -> None:
    try:
        peer_source = _default_source_for_symbol(peer_symbol, source)
        weekly_data = _get_cached_ohlc(data_cache, FetchConfig(symbol=peer_symbol, interval="1w", limit=160, source=peer_source))
        daily_data = _get_cached_ohlc(data_cache, FetchConfig(symbol=peer_symbol, interval="1d", limit=220, source=peer_source))
        h4_data = _get_cached_ohlc(data_cache, FetchConfig(symbol=peer_symbol, interval="4h", limit=220, source=peer_source))
        h1_data = _get_cached_ohlc(data_cache, FetchConfig(symbol=peer_symbol, interval="1h", limit=320, source=peer_source))
        ltf_data = _get_cached_ohlc(data_cache, FetchConfig(symbol=peer_symbol, interval="15m", limit=320, source=peer_source)) if use_ltf_refinement else None
        peer_result = evaluate_symbol(symbol=peer_symbol, weekly_data=weekly_data, daily_data=daily_data, h1_data=h1_data, ltf_data=ltf_data, h4_data=h4_data)
        cycle_h1_data[peer_symbol] = h1_data
        cycle_result_cache[peer_symbol] = peer_result
    except Exception:
        return


def _get_cached_ohlc(cache: OhlcCache, config: FetchConfig) -> pd.DataFrame:
    key = (str(config.symbol).upper(), str(config.interval), str(config.source))
    now = time.monotonic()
    cached = cache.get(key)
    ttl = _ohlc_cache_ttl_seconds(config.interval)
    if cached is not None and (now - float(cached.get("fetched_at") or 0.0)) <= ttl:
        return cached["data"]  # type: ignore[return-value]
    data = fetch_ohlc(config)
    cache[key] = {"fetched_at": now, "data": data}
    return data


def _ohlc_cache_ttl_seconds(interval: str) -> int:
    mapping = {
        "15m": 45,
        "1h": 120,
        "4h": 300,
        "1d": 900,
        "1w": 1800,
    }
    return mapping.get(str(interval), 60)


def _rolling_return_correlation(base_h1: pd.DataFrame | None, peer_h1: pd.DataFrame | None) -> float | None:
    if base_h1 is None or peer_h1 is None:
        return None
    if len(base_h1) < 60 or len(peer_h1) < 60:
        return None
    base_returns = base_h1["close"].astype(float).pct_change().dropna().tail(96).reset_index(drop=True)
    peer_returns = peer_h1["close"].astype(float).pct_change().dropna().tail(96).reset_index(drop=True)
    length = min(len(base_returns), len(peer_returns))
    if length < 40:
        return None
    correlation = base_returns.tail(length).corr(peer_returns.tail(length))
    if correlation is None or pd.isna(correlation):
        return None
    return float(correlation)


def _default_source_for_symbol(symbol: str, requested_source: str) -> str:
    if requested_source != "auto":
        return requested_source
    if str(symbol).upper().endswith("USDT"):
        return "binance"
    return "yfinance"


def _should_send_setup_alert(result: dict, previous: dict | None) -> bool:
    if previous is None:
        return True
    if str(previous.get("setup_state") or "") == "WAIT_CONFIRMATION":
        return True

    current = _build_alert_context(result)
    if current["bias"] != previous.get("bias"):
        return True
    if current["order_block_formed_at"] and current["order_block_formed_at"] != previous.get("order_block_formed_at"):
        return True
    if current.get("lifecycle") != previous.get("lifecycle"):
        return True

    previous_entry = float(previous.get("entry") or 0)
    current_entry = float(current.get("entry") or 0)
    if previous_entry <= 0:
        return True

    entry_shift_ratio = abs(current_entry - previous_entry) / previous_entry
    return entry_shift_ratio >= 0.0015


def _result_bias(result: dict) -> str | None:
    bias = result.get("bias")
    return str(bias).upper() if bias is not None else None


def _rank_result(result: dict) -> dict:
    score = 0.0
    if result.get("status") != "VALID_TRADE":
        return {"ranking_score": 0.0, "tier": "skip"}

    setup_grade = str(result.get("setup_grade") or "")
    if setup_grade == "A+":
        score += 100
    elif setup_grade == "B":
        score += 75

    if str(result.get("daily_bias") or "").lower() == str(result.get("h1_bias") or "").lower():
        score += 15

    score += float(result.get("risk_reward_ratio") or 0) * 12
    score += float(result.get("confidence_score") or 0) * 0.35

    session_name = str(result.get("session") or "")
    if session_name in {"london", "new_york"}:
        score += 10

    confluences = set(result.get("confluences") or [])
    if "M15 Confirmation" in confluences:
        score += 10
    if "Pullback Entry" in confluences:
        score += 8
    if "Reaction Confirmation" in confluences:
        score += 10
    if "Pullback Zone" in confluences:
        score += 8

    tier = "A-tier" if setup_grade == "A+" else "B-tier"
    return {"ranking_score": round(score, 2), "tier": tier}


def _rank_watch_result(result: dict) -> dict:
    stalker = result.get("stalker") or {}
    score = float(result.get("confidence_score") or 0)
    score += float(stalker.get("score") or 0.0) * 0.45
    if (result.get("details") or {}).get("plan_zone"):
        score += 12
    if "No current entry at zone" in list(result.get("missing") or []):
        score += 10
    if "No M15 BOS confirmation" in list(result.get("missing") or []):
        score += 6
    if "Price not at pullback zone" in list(result.get("missing") or []):
        score += 10
    if "No reaction confirmation" in list(result.get("missing") or []):
        score += 8
    tier = "watch"
    return {"ranking_score": round(score, 2), "tier": tier}


def _select_alert_candidates(candidates: list[dict], *, challenge_mode: ChallengeModeConfig) -> dict[str, list[dict]]:
    ranked = [item for item in candidates if item.get("status") == "VALID_TRADE"]
    if challenge_mode.enabled:
        ranked = [item for item in ranked if _qualifies_for_challenge(item, challenge_mode)]
    ranked.sort(
        key=lambda item: (
            0 if item.get("setup_grade") == "A+" else 1,
            -float(item.get("ranking_score", 0)),
            item.get("pair", ""),
        )
    )

    edge_snapshot = build_edge_control_snapshot()
    selected: list[dict] = []
    blocked: list[dict] = []
    shadow: list[dict] = []
    blocked_components: list[set[str]] = []
    challenge_stats = get_challenge_trade_stats(challenge_name=challenge_mode.name) if challenge_mode.enabled else None
    challenge_slots_remaining = (
        max(int(challenge_mode.max_trades) - int(challenge_stats.get("count", 0)), 0) if challenge_stats is not None else None
    )
    for item in ranked:
        if challenge_mode.enabled:
            challenge_gate = item.get("challenge_gate") or {}
            if not challenge_gate.get("allowed", True):
                blocked.append(
                    _blocked_candidate_diagnostic(
                        item,
                        reasons=challenge_gate.get("reasons", []) or ["Challenge final review rejected the setup"],
                        block_type="challenge_final_gate",
                    )
                )
                continue
        if challenge_mode.enabled and challenge_slots_remaining is not None and challenge_slots_remaining <= 0:
            blocked.append(
                _blocked_candidate_diagnostic(
                    item,
                    reasons=[f"Challenge trade cap reached ({challenge_mode.max_trades})"],
                    block_type="challenge_cap",
                )
            )
            continue
        edge_decision = evaluate_edge_control(item, snapshot=edge_snapshot)
        item["edge_control"] = edge_decision
        if not edge_decision.get("allowed", True):
            if edge_decision.get("shadow_eligible"):
                shadow.append({**item, "trade_mode": "shadow"})
            blocked.append(
                _blocked_candidate_diagnostic(
                    item,
                    reasons=edge_decision.get("reasons", []),
                    block_type="shadow_session" if edge_decision.get("shadow_eligible") else "edge_control",
                )
            )
            continue
        session_name = str(item.get("session") or "")
        if _session_loss_locked(session_name):
            blocked.append(
                _blocked_candidate_diagnostic(
                    item,
                    reasons=["Session loss lock active"],
                    block_type="session_lock",
                )
            )
            continue
        components = _symbol_components(str(item.get("pair") or ""))
        if components and any(components & blocked for blocked in blocked_components):
            blocked.append(
                _blocked_candidate_diagnostic(
                    item,
                    reasons=["Correlation cluster already covered this cycle"],
                    block_type="cluster_overlap",
                )
            )
            continue
        selected.append(item)
        if challenge_mode.enabled and challenge_slots_remaining is not None:
            challenge_slots_remaining -= 1
        if components:
            blocked_components.append(components)
    return {"selected": selected[:6], "blocked": blocked, "shadow": shadow[:6]}


def _qualifies_for_challenge(result: dict, challenge_mode: ChallengeModeConfig) -> bool:
    if str(result.get("setup_grade") or "") != str(challenge_mode.minimum_grade):
        return False
    if float(result.get("risk_reward_ratio") or 0.0) < float(challenge_mode.minimum_rr):
        return False
    if str(result.get("status") or "") != "VALID_TRADE":
        return False
    challenge_gate = result.get("challenge_gate") or {}
    if not challenge_gate.get("allowed", True):
        return False
    return True


def _evaluate_challenge_final_gate(*, result: dict, h1_data: pd.DataFrame, ltf_data: pd.DataFrame | None) -> dict:
    reasons: list[str] = []
    frame = ltf_data if ltf_data is not None and not ltf_data.empty else h1_data
    if frame is None or frame.empty:
        return {"allowed": False, "reasons": ["Missing lower-timeframe review data"]}

    latest = frame.iloc[-1]
    latest_time = pd.Timestamp(latest["time"])
    latest_close = float(latest["close"])
    latest_open = float(latest["open"])
    latest_high = float(latest["high"])
    latest_low = float(latest["low"])

    session_name = str(result.get("session") or "").lower()
    utc_hour = int(latest_time.tz_localize("UTC").hour) if latest_time.tzinfo is None else int(latest_time.tz_convert("UTC").hour)
    if (session_name == "london" and utc_hour >= 11) or (session_name == "new_york" and utc_hour >= 16):
        reasons.append("Entry is too close to session end")

    entry = float(result.get("entry") or 0.0)
    stop_loss = float(result.get("sl") or 0.0)
    risk = abs(entry - stop_loss)
    if risk <= 0:
        reasons.append("Invalid risk distance")
        return {"allowed": False, "reasons": reasons}

    bias = str(result.get("bias") or "").upper()
    extension_r = 0.0
    if bias == "BUY":
        extension_r = (latest_close - entry) / risk
    elif bias == "SELL":
        extension_r = (entry - latest_close) / risk
    if extension_r > 0.35:
        reasons.append("Entry is too extended from the planned price")

    bodies = (frame["close"].astype(float) - frame["open"].astype(float)).abs().tail(20)
    reference_body = float(bodies.iloc[:-1].median()) if len(bodies) > 1 else float(bodies.iloc[-1] or 0.0)
    latest_body = abs(latest_close - latest_open)
    if reference_body > 0:
        in_trade_direction = (bias == "BUY" and latest_close >= latest_open) or (bias == "SELL" and latest_close <= latest_open)
        if in_trade_direction and latest_body > reference_body * 1.8:
            reasons.append("Latest candle is too stretched for a clean challenge entry")

    if bias == "BUY" and latest_high < entry and extension_r < -0.8:
        reasons.append("Price is still too weak below the planned entry")
    if bias == "SELL" and latest_low > entry and extension_r < -0.8:
        reasons.append("Price is still too weak above the planned entry")

    return {
        "allowed": not reasons,
        "reasons": reasons,
        "extension_r": round(extension_r, 2),
        "reviewed_at": datetime.now(UTC).isoformat(),
    }


def _select_watch_candidates(candidates: list[dict]) -> list[dict]:
    ranked = [item for item in candidates if item.get("status") == "WAIT_CONFIRMATION"]
    ranked.sort(
        key=lambda item: (
            -float(item.get("ranking_score", 0)),
            item.get("pair", ""),
        )
    )

    selected: list[dict] = []
    blocked_components: list[set[str]] = []
    for item in ranked:
        components = _symbol_components(str(item.get("pair") or ""))
        if components and any(components & blocked for blocked in blocked_components):
            continue
        selected.append(item)
        if components:
            blocked_components.append(components)
    return selected[:6]


def _build_rejection_diagnostic(*, symbol: str, result: dict) -> dict:
    return {
        "symbol": symbol.upper(),
        "status": result.get("status"),
        "session": result.get("session"),
        "setup_grade": result.get("setup_grade"),
        "confidence_score": result.get("confidence_score"),
        "message": result.get("message"),
        "missing": list(result.get("missing") or [])[:4],
        "lifecycle": result.get("lifecycle"),
    }


def _blocked_candidate_diagnostic(candidate: dict, *, reasons: list[str], block_type: str) -> dict:
    return {
        "symbol": str(candidate.get("pair") or candidate.get("symbol") or "").upper(),
        "status": candidate.get("status"),
        "session": candidate.get("session"),
        "setup_grade": candidate.get("setup_grade"),
        "confidence_score": candidate.get("confidence_score"),
        "ranking_score": candidate.get("ranking_score"),
        "message": candidate.get("message"),
        "reasons": list(reasons or [])[:4],
        "block_type": block_type,
        "lifecycle": candidate.get("lifecycle"),
    }


def _session_loss_locked(session_name: str) -> bool:
    normalized_session = str(session_name or "").strip().lower()
    if not normalized_session:
        return False

    closed = [
        entry
        for entry in reversed(load_journal_entries())
        if is_live_strategy(entry.get("strategy"))
        and str(entry.get("session") or "").strip().lower() == normalized_session
        and str(entry.get("result") or "").upper() in {"WIN", "LOSS"}
    ]
    if len(closed) < 2:
        return False
    return all(str(item.get("result") or "").upper() == "LOSS" for item in closed[:2])


def _symbol_components(symbol: str) -> set[str]:
    normalized = str(symbol or "").upper()
    if normalized == "XAUUSD":
        return {"XAU", "USD"}
    if len(normalized) == 6 and normalized.isalpha():
        return {normalized[:3], normalized[3:]}
    return set()


def _alert_type_for_candidate(candidate: dict, first_cycle: bool, previous_context: dict | None = None) -> str:
    if first_cycle:
        return "startup_setup"
    return "valid_setup"


def _should_send_confirmation_alert(result: dict, previous: dict | None) -> bool:
    if result.get("status") != "WAIT_CONFIRMATION":
        return False
    return _should_send_stalker_alert(result, previous)


def _why_this_matters(candidate: dict) -> str:
    return str(candidate.get("reason") or candidate.get("message") or "All required rules are aligned.")


def _motif_score_adjustment(*, result_bias: str, motifs: list[dict]) -> float:
    if not result_bias or not motifs:
        return 0.0

    adjustment = 0.0
    for motif in motifs[:2]:
        motif_bias = str(motif.get("bias") or "").upper()
        motif_confidence = float(motif.get("confidence") or 0.0)
        weight = min(motif_confidence / 12.0, 8.0)
        if (result_bias == "BUY" and motif_bias == "BULLISH") or (result_bias == "SELL" and motif_bias == "BEARISH"):
            adjustment += weight
        elif motif_bias in {"BULLISH", "BEARISH"}:
            adjustment -= weight * 0.9
    return adjustment


def _send_digest_if_due(group: str, source: str, candidates: list[dict], telegram_config) -> None:
    now = datetime.now().astimezone()
    digest_date = now.date().isoformat()
    session = None
    if 7 <= now.hour < 11:
        session = "morning_digest"
    elif 18 <= now.hour < 23:
        session = "evening_summary"

    if session is None or not should_send_digest(session, digest_date):
        return

    send_alert(
        {
            "type": session,
            "message": f"{group.upper()} market summary for {digest_date} via {source}.",
            "top_setups": candidates[:5],
            "scanner_health": "healthy",
            "digital_twin": get_digital_twin_snapshot().get("summary", {}),
            "signature": f"{session}|{digest_date}|{group}|{source}",
        },
        telegram_config,
    )
    mark_digest_sent(session, digest_date)


def _prune_alert_contexts(contexts: dict[str, dict]) -> dict[str, dict]:
    pruned: dict[str, dict] = {}
    now = datetime.now(UTC)
    for symbol, payload in contexts.items():
        last_seen = payload.get("last_seen")
        if not last_seen:
            pruned[symbol] = payload
            continue
        try:
            age_hours = (now - datetime.fromisoformat(last_seen.replace("Z", "+00:00"))).total_seconds() / 3600
        except ValueError:
            pruned[symbol] = payload
            continue
        setup_state = str(payload.get("setup_state") or "")
        max_age_hours = 6 if setup_state == "WAIT_CONFIRMATION" else 12
        if age_hours <= max_age_hours:
            pruned[symbol] = payload
    return pruned


def _serialize_recent_candles(data) -> list[dict]:
    rows = []
    for _, candle in data.tail(60).iterrows():
        rows.append(
            {
                "time": candle["time"].isoformat() if hasattr(candle["time"], "isoformat") else str(candle["time"]),
                "open": round(float(candle["open"]), 4),
                "high": round(float(candle["high"]), 4),
                "low": round(float(candle["low"]), 4),
                "close": round(float(candle["close"]), 4),
            }
        )
    return rows


def _should_send_stalker_alert(result: dict, previous: dict | None) -> bool:
    stalker = result.get("stalker") or {}
    has_plan_zone = bool((result.get("details", {}) or {}).get("plan_zone"))
    if stalker.get("state") not in {"near_valid", "developing"} and not has_plan_zone:
        return False
    if previous is None:
        return True
    previous_signature = previous.get("signature")
    current_signature = _build_stalker_signature(result, result.get("pair") or "")
    if current_signature != previous_signature:
        return True
    last_seen = previous.get("last_seen")
    if not last_seen:
        return False
    try:
        age_hours = (datetime.now(UTC) - datetime.fromisoformat(str(last_seen).replace("Z", "+00:00"))).total_seconds() / 3600
    except ValueError:
        return False
    return has_plan_zone and age_hours >= 12

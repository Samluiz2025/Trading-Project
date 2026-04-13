from __future__ import annotations

from collections import Counter, defaultdict

from trading_bot.core.calibration_mode import build_calibration_snapshot
from trading_bot.core.edge_control import build_edge_control_snapshot
from trading_bot.core.journal import build_open_trade_snapshot, get_recent_journal
from trading_bot.core.monitor_state import build_scanner_health_snapshot, load_monitor_state
from trading_bot.core.validation_mode import build_validation_snapshot
from trading_bot.performance.repository import load_performance_results


def build_performance_snapshot() -> dict:
    journal = get_recent_journal(limit=500)
    open_trade_snapshot = build_open_trade_snapshot(entries=journal)
    active_open_entries = open_trade_snapshot.get("active_open_entries", [])
    stale_open_entries = open_trade_snapshot.get("stale_open_entries", [])
    monitor_state = load_monitor_state()
    scanner_health = build_scanner_health_snapshot(monitor_state)
    scan_diagnostics = monitor_state.get("scan_diagnostics", {})
    live_journal = [entry for entry in journal if not bool(entry.get("shadow_mode"))]
    shadow_journal = [entry for entry in journal if bool(entry.get("shadow_mode"))]
    research_results = load_performance_results()
    validation_snapshot = build_validation_snapshot(entries=journal)
    edge_snapshot = build_edge_control_snapshot(entries=journal)
    calibration_snapshot = build_calibration_snapshot(entries=journal)
    shadow_sessions = edge_snapshot.get("shadow_sessions", [])
    shadow_validation_snapshot = build_validation_snapshot(entries=journal, shadow_mode="only", sessions=shadow_sessions)

    rejected = [entry for entry in live_journal if entry.get("status") == "NO_TRADE"]
    closed = [entry for entry in live_journal if entry.get("result") in {"WIN", "LOSS"}]
    wins = [entry for entry in closed if entry.get("result") == "WIN"]
    losses = [entry for entry in closed if entry.get("result") == "LOSS"]

    gross_profit = sum(max(float(entry.get("rr_achieved") or 0), 0) for entry in closed)
    gross_loss = abs(sum(min(float(entry.get("rr_achieved") or 0), 0) for entry in closed))
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss else round(gross_profit, 2)
    win_rate = round((len(wins) / len(closed)) * 100, 2) if closed else 0.0
    expectancy_r = round(sum(float(entry.get("rr_achieved") or 0) for entry in closed) / len(closed), 2) if closed else 0.0

    strategy_breakdown: dict[str, dict] = {}
    pair_breakdown: dict[str, dict] = {}
    strategy_groups: defaultdict[str, list[dict]] = defaultdict(list)
    pair_groups: defaultdict[str, list[dict]] = defaultdict(list)
    concept_counter: Counter[str] = Counter()
    confluence_combo_counter: Counter[str] = Counter()

    for entry in live_journal:
        strategy_groups[entry.get("strategy", "Unknown")].append(entry)
        pair_groups[entry.get("symbol", "Unknown")].append(entry)
        for concept in entry.get("confluences", []):
            concept_counter[_normalize_confluence_name(concept)] += 1
        combo = " + ".join(sorted(_normalize_confluence_name(item) for item in entry.get("confluences", [])))
        if combo:
            confluence_combo_counter[combo] += 1

    for strategy_name, entries in strategy_groups.items():
        closed_entries = [entry for entry in entries if entry.get("result") in {"WIN", "LOSS"}]
        wins_for_strategy = sum(1 for entry in closed_entries if entry.get("result") == "WIN")
        losses_for_strategy = sum(1 for entry in closed_entries if entry.get("result") == "LOSS")
        rr_profit = sum(max(float(entry.get("rr_achieved") or 0), 0) for entry in closed_entries)
        rr_loss = abs(sum(min(float(entry.get("rr_achieved") or 0), 0) for entry in closed_entries))
        strategy_breakdown[strategy_name] = {
            "total_trades": len(entries),
            "win_rate": round((wins_for_strategy / len(closed_entries)) * 100, 2) if closed_entries else 0.0,
            "profit_factor": round(rr_profit / rr_loss, 2) if rr_loss else round(rr_profit, 2),
        }

    for pair_name, entries in pair_groups.items():
        closed_entries = [entry for entry in entries if entry.get("result") in {"WIN", "LOSS"}]
        wins_for_pair = sum(1 for entry in closed_entries if entry.get("result") == "WIN")
        losses_for_pair = sum(1 for entry in closed_entries if entry.get("result") == "LOSS")
        rr_profit = sum(max(float(entry.get("rr_achieved") or 0), 0) for entry in closed_entries)
        rr_loss = abs(sum(min(float(entry.get("rr_achieved") or 0), 0) for entry in closed_entries))
        pair_breakdown[pair_name] = {
            "total_trades": len(entries),
            "win_rate": round((wins_for_pair / len(closed_entries)) * 100, 2) if closed_entries else 0.0,
            "profit_factor": round(rr_profit / rr_loss, 2) if rr_loss else round(rr_profit, 2),
        }

    best_research_strategies = sorted(
        research_results,
        key=lambda item: item.get("ranking_score", 0),
        reverse=True,
    )[:5]

    return {
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "expectancy_r": expectancy_r,
        "total_trades": len(live_journal),
        "closed_trades": len(closed),
        "rejected_trades": len(rejected),
        "open_trades": len(active_open_entries),
        "pending_open_trades": sum(1 for entry in active_open_entries if not bool(entry.get("entry_triggered"))),
        "triggered_open_trades": sum(1 for entry in active_open_entries if bool(entry.get("entry_triggered"))),
        "stale_open_trades": len(stale_open_entries),
        "shadow_trades": len(shadow_journal),
        "best_concepts": [{"name": name, "count": count} for name, count in concept_counter.most_common(5)],
        "strongest_confluence_combinations": [
            {"name": name, "count": count}
            for name, count in confluence_combo_counter.most_common(5)
        ],
        "strategy_breakdown": strategy_breakdown,
        "pair_breakdown": pair_breakdown,
        "strict_symbol_breakdown": edge_snapshot.get("symbol_breakdown", [])[:6],
        "strict_session_breakdown": edge_snapshot.get("session_breakdown", []),
        "validation": {
            "validated_closed_trades": validation_snapshot.get("validated_closed_trades", 0),
            "raw_net_r": validation_snapshot.get("raw_net_r", 0.0),
            "adjusted_net_r": validation_snapshot.get("adjusted_net_r", 0.0),
            "adjusted_profit_factor": validation_snapshot.get("adjusted_profit_factor", 0.0),
            "adjusted_win_rate": validation_snapshot.get("adjusted_win_rate", 0.0),
            "adjusted_expectancy_r": validation_snapshot.get("adjusted_expectancy_r", 0.0),
            "underperforming_symbols": validation_snapshot.get("underperforming_symbols", []),
            "top_symbols": validation_snapshot.get("top_symbols", []),
            "bottom_symbols": validation_snapshot.get("bottom_symbols", []),
            "recent_days": validation_snapshot.get("recent_days", []),
            "recent_weeks": validation_snapshot.get("recent_weeks", []),
            "equity_curve": validation_snapshot.get("equity_curve", []),
        },
        "edge_control": {
            "enabled": edge_snapshot.get("enabled", True),
            "locked": edge_snapshot.get("locked", False),
            "lock_reasons": edge_snapshot.get("lock_reasons", []),
            "symbol_filter_mode": edge_snapshot.get("symbol_filter_mode", "score_only"),
            "session_filter_mode": edge_snapshot.get("session_filter_mode", "score_only"),
            "allowed_sessions": edge_snapshot.get("allowed_sessions", []),
            "shadow_sessions": edge_snapshot.get("shadow_sessions", []),
            "minimum_setup_grade": edge_snapshot.get("minimum_setup_grade"),
            "symbol_mode": edge_snapshot.get("symbol_mode"),
            "allowed_symbols": edge_snapshot.get("allowed_symbols", []),
            "calibration_blocked_symbols": edge_snapshot.get("calibration_blocked_symbols", []),
            "validation_blocked_symbols": edge_snapshot.get("validation_blocked_symbols", []),
            "validation_summary": edge_snapshot.get("validation_summary", {}),
            "daily": edge_snapshot.get("daily", {}),
            "weekly": edge_snapshot.get("weekly", {}),
            "consecutive_losses": edge_snapshot.get("consecutive_losses", 0),
            "open_positions": edge_snapshot.get("open_positions", {}),
        },
        "calibration": {
            "can_apply": calibration_snapshot.get("can_apply", False),
            "pending_changes": calibration_snapshot.get("pending_changes", []),
            "promoted_symbols": calibration_snapshot.get("promoted_symbols", []),
            "demoted_symbols": calibration_snapshot.get("demoted_symbols", []),
            "recommended_sessions": calibration_snapshot.get("recommended_sessions", []),
            "recommended_minimum_setup_grade": calibration_snapshot.get("recommended_minimum_setup_grade"),
            "recent_adjusted_net_r": calibration_snapshot.get("recent_adjusted_net_r", 0.0),
            "recent_adjusted_expectancy_r": calibration_snapshot.get("recent_adjusted_expectancy_r", 0.0),
            "last_application": calibration_snapshot.get("last_application"),
            "shadow_sessions": calibration_snapshot.get("shadow_sessions", []),
            "shadow_sessions_ready": calibration_snapshot.get("shadow_sessions_ready", []),
            "shadow_session_reviews": calibration_snapshot.get("shadow_session_reviews", []),
        },
        "shadow_tracking": {
            "sessions": shadow_sessions,
            "validated_closed_trades": shadow_validation_snapshot.get("validated_closed_trades", 0),
            "adjusted_net_r": shadow_validation_snapshot.get("adjusted_net_r", 0.0),
            "adjusted_profit_factor": shadow_validation_snapshot.get("adjusted_profit_factor", 0.0),
            "adjusted_win_rate": shadow_validation_snapshot.get("adjusted_win_rate", 0.0),
            "adjusted_expectancy_r": shadow_validation_snapshot.get("adjusted_expectancy_r", 0.0),
            "top_symbols": shadow_validation_snapshot.get("top_symbols", []),
            "bottom_symbols": shadow_validation_snapshot.get("bottom_symbols", []),
            "ready_sessions": calibration_snapshot.get("shadow_sessions_ready", []),
            "session_reviews": calibration_snapshot.get("shadow_session_reviews", []),
        },
        "scan_diagnostics": {
            "last_updated_at": scan_diagnostics.get("last_updated_at"),
            "evaluated_symbols": scan_diagnostics.get("evaluated_symbols", 0),
            "valid_candidates": scan_diagnostics.get("valid_candidates", 0),
            "selected_count": scan_diagnostics.get("selected_count", 0),
            "blocked_count": scan_diagnostics.get("blocked_count", 0),
            "rejected_count": scan_diagnostics.get("rejected_count", 0),
            "selected_candidates": scan_diagnostics.get("selected_candidates", []),
            "blocked_candidates": scan_diagnostics.get("blocked_candidates", []),
            "rejected_candidates": scan_diagnostics.get("rejected_candidates", []),
        },
        "scanner_health": scanner_health,
        "best_strategies": best_research_strategies,
    }


def _normalize_confluence_name(value) -> str:
    if isinstance(value, dict):
        confluence_type = value.get("type", "Unknown")
        confluence_tf = value.get("tf")
        return f"{confluence_type} ({confluence_tf})" if confluence_tf else confluence_type
    return str(value)

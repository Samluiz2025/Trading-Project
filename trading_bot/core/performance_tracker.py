from __future__ import annotations

from collections import Counter, defaultdict

from trading_bot.core.journal import get_recent_journal
from trading_bot.performance.repository import load_performance_results


def build_performance_snapshot() -> dict:
    journal = get_recent_journal(limit=500)
    research_results = load_performance_results()

    closed = [entry for entry in journal if entry.get("result") in {"WIN", "LOSS"}]
    wins = [entry for entry in closed if entry.get("result") == "WIN"]
    losses = [entry for entry in closed if entry.get("result") == "LOSS"]

    gross_profit = sum(max(float(entry.get("rr_achieved") or 0), 0) for entry in closed)
    gross_loss = abs(sum(min(float(entry.get("rr_achieved") or 0), 0) for entry in closed))
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss else round(gross_profit, 2)
    win_rate = round((len(wins) / len(closed)) * 100, 2) if closed else 0.0

    strategy_breakdown: dict[str, dict] = {}
    strategy_groups: defaultdict[str, list[dict]] = defaultdict(list)
    concept_counter: Counter[str] = Counter()
    confluence_combo_counter: Counter[str] = Counter()

    for entry in journal:
        strategy_groups[entry.get("strategy", "Unknown")].append(entry)
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

    best_research_strategies = sorted(
        research_results,
        key=lambda item: item.get("ranking_score", 0),
        reverse=True,
    )[:5]

    return {
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "total_trades": len(journal),
        "closed_trades": len(closed),
        "best_concepts": [{"name": name, "count": count} for name, count in concept_counter.most_common(5)],
        "strongest_confluence_combinations": [
            {"name": name, "count": count}
            for name, count in confluence_combo_counter.most_common(5)
        ],
        "strategy_breakdown": strategy_breakdown,
        "best_strategies": best_research_strategies,
    }


def _normalize_confluence_name(value) -> str:
    if isinstance(value, dict):
        confluence_type = value.get("type", "Unknown")
        confluence_tf = value.get("tf")
        return f"{confluence_type} ({confluence_tf})" if confluence_tf else confluence_type
    return str(value)

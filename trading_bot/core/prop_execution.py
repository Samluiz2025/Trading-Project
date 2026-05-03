from __future__ import annotations

from typing import Any


def build_prop_trade_plan(
    *,
    bias: str,
    entry: float,
    stop_loss: float,
    take_profit: float,
    risk_reward_ratio: float | None,
    partial_rr: float = 2.0,
    minimum_primary_rr: float = 3.0,
    maximum_primary_rr: float = 4.0,
    partial_fraction: float = 0.5,
) -> dict[str, Any] | None:
    normalized_bias = str(bias or "").upper()
    if normalized_bias not in {"BUY", "SELL"}:
        return None

    risk = abs(float(entry) - float(stop_loss))
    if risk <= 0:
        return None

    raw_rr = float(risk_reward_ratio or 0.0)
    if raw_rr <= 0:
        raw_rr = abs(float(take_profit) - float(entry)) / risk
    if raw_rr < minimum_primary_rr:
        return None

    effective_partial_rr = min(float(partial_rr), raw_rr)
    primary_rr = min(float(maximum_primary_rr), raw_rr)
    if primary_rr < minimum_primary_rr:
        return None

    direction = 1.0 if normalized_bias == "BUY" else -1.0
    partial_take_profit = round(float(entry) + (direction * risk * effective_partial_rr), 4)
    primary_take_profit = round(float(entry) + (direction * risk * primary_rr), 4)
    extended_take_profit = round(float(take_profit), 4)
    break_even_stop = round(float(entry), 4)
    partial_fraction = min(max(float(partial_fraction), 0.1), 0.9)
    remaining_fraction = round(1.0 - partial_fraction, 4)
    locked_rr_after_partial = round(partial_fraction * effective_partial_rr, 2)
    realized_rr_at_primary = round(locked_rr_after_partial + (remaining_fraction * primary_rr), 2)

    return {
        "mode": "prop_precision",
        "partial_enabled": True,
        "partial_fraction": partial_fraction,
        "partial_rr": round(effective_partial_rr, 2),
        "partial_take_profit": partial_take_profit,
        "break_even_enabled": True,
        "break_even_after_rr": round(effective_partial_rr, 2),
        "break_even_stop": break_even_stop,
        "primary_rr": round(primary_rr, 2),
        "primary_take_profit": primary_take_profit,
        "extended_take_profit": extended_take_profit if abs(extended_take_profit - primary_take_profit) > 0.0001 else None,
        "locked_rr_after_partial": locked_rr_after_partial,
        "realized_rr_at_primary": realized_rr_at_primary,
        "notes": "Take partial at 2R, move SL to breakeven, and pay the main position at 3R-4R.",
    }


def apply_prop_execution_to_setup(setup: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(setup, dict):
        return setup

    bias = setup.get("bias")
    entry = setup.get("entry")
    stop_loss = setup.get("sl")
    take_profit = setup.get("tp")
    rr = setup.get("risk_reward_ratio")
    if bias is None or entry is None or stop_loss is None or take_profit is None:
        return setup

    prop_plan = build_prop_trade_plan(
        bias=str(bias),
        entry=float(entry),
        stop_loss=float(stop_loss),
        take_profit=float(take_profit),
        risk_reward_ratio=float(rr) if rr is not None else None,
    )
    if not prop_plan:
        return setup

    updated = dict(setup)
    updated["tp"] = round(float(prop_plan["primary_take_profit"]), 4)
    updated["risk_reward_ratio"] = round(float(prop_plan["primary_rr"]), 2)
    updated["primary_tp"] = round(float(prop_plan["primary_take_profit"]), 4)
    updated["extended_tp"] = prop_plan.get("extended_take_profit")
    updated["trade_management"] = prop_plan

    confluences = list(updated.get("confluences") or [])
    if "Prop Execution Plan" not in confluences:
        confluences.append("Prop Execution Plan")
    updated["confluences"] = confluences

    details = dict(updated.get("details") or {})
    details["prop_execution"] = prop_plan
    updated["details"] = details

    execution_model = str(updated.get("execution_model") or "").strip()
    if "prop" not in execution_model.lower():
        suffix = "2R partial -> breakeven -> 3R-4R primary"
        updated["execution_model"] = f"{execution_model} -> {suffix}" if execution_model else suffix

    return updated

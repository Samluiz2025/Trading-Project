from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trading_bot.core.journal import load_journal_entries


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
BROKER_DRAFTS_PATH = DATA_DIR / "broker_drafts.json"
MAX_DRAFT_HISTORY = 300
DEFAULT_RISK = 100.0


def load_broker_drafts(limit: int | None = None) -> list[dict[str, Any]]:
    if not BROKER_DRAFTS_PATH.exists():
        return []
    try:
        raw = BROKER_DRAFTS_PATH.read_text(encoding="utf-8-sig")
    except OSError:
        return []
    cleaned = raw.replace("\x00", "").strip()
    if not cleaned:
        _save_broker_drafts([])
        return []
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        _save_broker_drafts([])
        return []
    drafts = payload if isinstance(payload, list) else []
    if limit is None:
        return drafts
    return drafts[-limit:]


def build_broker_draft_snapshot(limit: int | None = None) -> dict[str, Any]:
    drafts = load_broker_drafts(limit=limit)
    journal_by_signature = {
        str(entry.get("signature") or ""): entry
        for entry in load_journal_entries()
        if str(entry.get("signature") or "")
    }
    enriched = [_enrich_draft_with_journal(item, journal_by_signature.get(str(item.get("signature") or ""))) for item in drafts]
    active = [item for item in enriched if item.get("draft_status") in {"pending", "active"}]
    closed = [item for item in enriched if item.get("draft_status") in {"win", "loss", "archived"}]
    return {
        "entries": enriched if limit is None else enriched[-limit:],
        "summary": {
            "count": len(enriched),
            "active": len(active),
            "closed": len(closed),
            "wins": sum(1 for item in enriched if item.get("draft_status") == "win"),
            "losses": sum(1 for item in enriched if item.get("draft_status") == "loss"),
        },
    }


def create_broker_draft(
    *,
    symbol: str,
    setup: dict[str, Any],
    strategy: str,
    planned_risk: float | None = None,
    challenge_mode: bool = False,
    challenge_name: str | None = None,
    forward_test_mode: bool = False,
    forward_test_name: str | None = None,
    execution_controls: dict[str, Any] | None = None,
) -> dict[str, Any]:
    drafts = load_broker_drafts()
    signature = str(setup.get("signature") or "")
    if signature and any(str(item.get("signature") or "") == signature for item in drafts):
        return next(item for item in drafts if str(item.get("signature") or "") == signature)

    entry = float(setup.get("entry") or 0.0)
    stop_loss = float(setup.get("sl") or 0.0)
    take_profit = float(setup.get("tp") or 0.0)
    bias = str(setup.get("bias") or "").upper()
    risk_amount = float(planned_risk or os.getenv("BROKER_DRAFT_RISK") or DEFAULT_RISK)
    risk_per_unit = abs(entry - stop_loss)
    size_units = round(risk_amount / risk_per_unit, 4) if risk_per_unit > 0 else None
    session = str(setup.get("session") or "").strip().lower()
    rr = float(setup.get("risk_reward_ratio") or 0.0)
    execution_checklist = _build_execution_checklist(
        setup=setup,
        session=session,
        risk_per_unit=risk_per_unit,
        rr=rr,
        execution_controls=execution_controls or {},
    )

    draft = {
        "signature": signature or f"{symbol.upper()}|{strategy}|{entry:.4f}|draft",
        "symbol": symbol.upper(),
        "bias": bias,
        "strategy": strategy,
        "order_type": "limit",
        "entry": round(entry, 4),
        "stop_loss": round(stop_loss, 4),
        "take_profit": round(take_profit, 4),
        "risk_amount": round(risk_amount, 2),
        "risk_per_unit": round(risk_per_unit, 6) if risk_per_unit > 0 else None,
        "size_units": size_units,
        "setup_grade": setup.get("setup_grade"),
        "risk_reward_ratio": rr or setup.get("risk_reward_ratio"),
        "layer": "broker_draft",
        "execution_ready": bool(execution_checklist.get("ready")),
        "status": "draft",
        "trigger": "valid_setup",
        "reason": setup.get("reason") or setup.get("message") or "Executable setup drafted for broker review.",
        "created_at": datetime.now(UTC).isoformat(),
        "session": session or None,
        "execution_checklist": execution_checklist,
        "execution_controls": execution_controls or {},
        "challenge_mode": bool(challenge_mode),
        "challenge_name": challenge_name,
        "forward_test_mode": bool(forward_test_mode),
        "forward_test_name": forward_test_name,
    }
    drafts.append(draft)
    _save_broker_drafts(drafts[-MAX_DRAFT_HISTORY:])
    return draft


def _save_broker_drafts(items: list[dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp_path = BROKER_DRAFTS_PATH.with_name(f"{BROKER_DRAFTS_PATH.stem}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        temp_path.write_text(json.dumps(items[-MAX_DRAFT_HISTORY:], separators=(",", ":")), encoding="utf-8")
        _replace_with_retry(temp_path, BROKER_DRAFTS_PATH)
    except OSError as exc:
        print(f"[WARN] Failed to persist broker drafts: {exc}")
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            pass


def _replace_with_retry(temp_path: Path, target_path: Path, retries: int = 12) -> None:
    last_error: OSError | None = None
    for attempt in range(retries):
        try:
            temp_path.replace(target_path)
            return
        except OSError as exc:
            last_error = exc
            if attempt >= retries - 1:
                break
            time.sleep(min(1.0, 0.08 * (attempt + 1)))
    if last_error is not None:
        raise last_error


def _enrich_draft_with_journal(draft: dict[str, Any], journal_entry: dict[str, Any] | None) -> dict[str, Any]:
    if not journal_entry:
        return {**draft, "draft_status": "draft", "entry_triggered": False, "rr_achieved": None}

    status = str(journal_entry.get("status") or "").upper()
    result = str(journal_entry.get("result") or "").upper()
    if result == "WIN":
        draft_status = "win"
    elif result == "LOSS":
        draft_status = "loss"
    elif result == "ARCHIVED":
        draft_status = "archived"
    elif bool(journal_entry.get("entry_triggered")):
        draft_status = "active"
    else:
        draft_status = "pending"

    return {
        **draft,
        "draft_status": draft_status,
        "entry_triggered": bool(journal_entry.get("entry_triggered")),
        "triggered_at": journal_entry.get("triggered_at"),
        "closed_at": journal_entry.get("closed_at"),
        "rr_achieved": journal_entry.get("rr_achieved"),
    }


def _build_execution_checklist(
    *,
    setup: dict[str, Any],
    session: str,
    risk_per_unit: float,
    rr: float,
    execution_controls: dict[str, Any],
) -> dict[str, Any]:
    control_reasons = list((execution_controls or {}).get("reasons") or [])
    items = [
        {"name": "Status valid", "ok": str(setup.get("status") or "").upper() == "VALID_TRADE"},
        {"name": "Bias present", "ok": str(setup.get("bias") or "").upper() in {"BUY", "SELL"}},
        {"name": "Entry price", "ok": float(setup.get("entry") or 0.0) > 0},
        {"name": "Stop loss", "ok": float(setup.get("sl") or 0.0) > 0},
        {"name": "Take profit", "ok": float(setup.get("tp") or 0.0) > 0},
        {"name": "Positive risk distance", "ok": risk_per_unit > 0},
        {"name": "Minimum RR", "ok": rr >= 2.0},
        {"name": "Session allowed", "ok": session in {"london", "new_york"} or session == ""},
        {"name": "No missing rules", "ok": not bool(setup.get("missing"))},
        {"name": "Execution controls pass", "ok": not control_reasons},
    ]
    return {
        "ready": all(item["ok"] for item in items),
        "items": items,
        "control_reasons": control_reasons,
    }

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
ALERTS_PATH = DATA_DIR / "alerts.json"
MAX_ALERT_HISTORY = 200


def load_alerts(limit: int | None = None) -> list[dict[str, Any]]:
    if not ALERTS_PATH.exists():
        return []
    try:
        raw = ALERTS_PATH.read_text(encoding="utf-8-sig")
    except OSError:
        return []
    cleaned = raw.replace("\x00", "").strip()
    if not cleaned:
        _save_alerts([])
        return []
    try:
        alerts = json.loads(cleaned)
    except json.JSONDecodeError:
        _save_alerts([])
        return []
    if not isinstance(alerts, list):
        _save_alerts([])
        return []
    if limit is None:
        return alerts
    return alerts[-limit:]


def append_alert(alert: dict[str, Any], max_items: int = MAX_ALERT_HISTORY) -> dict[str, Any]:
    alerts = load_alerts()
    signature = alert.get("signature")
    if signature and any(existing.get("signature") == signature for existing in alerts):
        return alert

    payload = _json_safe(
        {
        **alert,
        "timestamp": alert.get("timestamp") or datetime.now(UTC).isoformat(),
        }
    )
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    alerts.append(payload)
    trimmed_alerts = _merge_alerts(load_alerts(), alerts)[-max_items:]
    _save_alerts(trimmed_alerts)
    return payload


def _merge_alerts(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    ordered: list[str] = []
    for item in existing + incoming:
        signature = str(item.get("signature") or item.get("timestamp") or f"alert:{len(ordered)}")
        if signature not in merged:
            ordered.append(signature)
        merged[signature] = item
    return [merged[key] for key in ordered]


def _save_alerts(items: list[dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp_path = ALERTS_PATH.with_name(f"{ALERTS_PATH.stem}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        temp_path.write_text(json.dumps(items[-MAX_ALERT_HISTORY:], separators=(",", ":")), encoding="utf-8")
        _replace_with_retry(temp_path, ALERTS_PATH)
    except OSError as exc:
        print(f"[WARN] Failed to persist alerts: {exc}")
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            pass


def _json_safe(value: Any):
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


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

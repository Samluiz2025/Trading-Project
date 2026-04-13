from __future__ import annotations

import json
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
        alerts = json.loads(ALERTS_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
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
    trimmed_alerts = alerts[-max_items:]
    try:
        ALERTS_PATH.write_text(json.dumps(trimmed_alerts, separators=(",", ":")), encoding="utf-8")
    except OSError as exc:
        print(f"[WARN] Failed to persist alerts: {exc}")
    return payload


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

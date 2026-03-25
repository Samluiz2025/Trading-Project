from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
ALERTS_PATH = DATA_DIR / "alerts.json"


def load_alerts(limit: int | None = None) -> list[dict[str, Any]]:
    if not ALERTS_PATH.exists():
        return []
    alerts = json.loads(ALERTS_PATH.read_text(encoding="utf-8"))
    if limit is None:
        return alerts
    return alerts[-limit:]


def append_alert(alert: dict[str, Any], max_items: int = 500) -> dict[str, Any]:
    alerts = load_alerts()
    signature = alert.get("signature")
    if signature and any(existing.get("signature") == signature for existing in alerts):
        return alert

    payload = {
        **alert,
        "timestamp": alert.get("timestamp") or datetime.now(UTC).isoformat(),
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    alerts.append(payload)
    ALERTS_PATH.write_text(json.dumps(alerts[-max_items:], indent=2), encoding="utf-8")
    return payload

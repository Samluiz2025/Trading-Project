from __future__ import annotations

import json
from pathlib import Path

from trading_bot.core.digital_twin import _default_twin_state, save_digital_twin_state
from trading_bot.core.monitor_state import reset_runtime_monitor_state


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
PENDING_TELEGRAM_PATH = DATA_DIR / "pending_telegram_alerts.json"


def _reset_pending_telegram_queue() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PENDING_TELEGRAM_PATH.write_text("[]", encoding="utf-8")


def main() -> None:
    reset_runtime_monitor_state(keep_telegram_status=True)
    _reset_pending_telegram_queue()
    save_digital_twin_state(_default_twin_state())
    print(
        json.dumps(
            {
                "status": "ok",
                "message": "Monday-start runtime reset completed.",
                "reset": [
                    "monitor_state runtime",
                    "scan diagnostics",
                    "alert contexts",
                    "symbol health",
                    "queued telegram alerts",
                    "digital twin runtime state",
                ],
                "preserved": [
                    "trade journal",
                    "alerts history",
                    "weekly outlook history",
                ],
            }
        )
    )


if __name__ == "__main__":
    main()

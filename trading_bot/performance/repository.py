"""Persistence helpers for research results."""

from __future__ import annotations

import json
from pathlib import Path


PERFORMANCE_PATH = Path(__file__).resolve().parents[1] / "data" / "strategy_performance.json"


def load_performance_results() -> list[dict]:
    """Load previously saved performance summaries."""

    if not PERFORMANCE_PATH.exists():
        return []
    return json.loads(PERFORMANCE_PATH.read_text(encoding="utf-8"))


def save_performance_results(results: list[dict]) -> Path:
    """Persist performance summaries to disk."""

    PERFORMANCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PERFORMANCE_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return PERFORMANCE_PATH

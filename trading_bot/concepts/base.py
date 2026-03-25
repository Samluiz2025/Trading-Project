"""Common data structures for concept research."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


SignalSide = Literal["BUY", "SELL"]


@dataclass(frozen=True)
class ConceptSignal:
    """Normalized signal emitted by an individual concept detector."""

    concept: str
    signal: SignalSide
    index: int
    time: str
    entry: float
    stop_loss: float
    take_profit: float
    confidence: int
    metadata: dict[str, Any] = field(default_factory=dict)

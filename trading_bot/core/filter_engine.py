from __future__ import annotations


REQUIRED_SMC_CONDITIONS = [
    "Daily Bias",
    "Bias mismatch",
    "No BOS/MSS",
    "No inducement",
    "No OB",
    "No FVG",
]


def filter_trade(candidate: dict) -> dict:
    if candidate.get("status") == "VALID_TRADE":
        return candidate

    missing = list(candidate.get("missing", []))
    return {
        "status": "NO TRADE",
        "message": candidate.get("message", "No valid setup available"),
        "missing": missing,
        "strategy": candidate.get("strategy", "SMC"),
        "details": candidate.get("details", {}),
        "pair": candidate.get("pair"),
        "bias": candidate.get("bias"),
        "entry": candidate.get("entry"),
        "sl": candidate.get("sl"),
        "tp": candidate.get("tp"),
        "confluences": candidate.get("confluences", []),
        "confidence": "LOW",
        "confidence_score": 0,
        "strategies": [],
        "lifecycle": candidate.get("lifecycle"),
        "stalker": candidate.get("stalker"),
    }

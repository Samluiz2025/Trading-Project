from __future__ import annotations

from typing import Literal

import pandas as pd


StructureLabel = Literal["HH", "HL", "LH", "LL"]
SwingType = Literal["high", "low"]
TrendLabel = Literal["bullish", "bearish", "ranging"]


def detect_market_structure(dataframe: pd.DataFrame, swing_window: int = 2) -> dict:
    """
    Detect swing points, classify structure, and derive a simple trend bias.

    A candle is considered a swing high if its high is greater than the highs
    on both sides within `swing_window`. The same applies inversely for lows.
    """

    _validate_ohlc_dataframe(dataframe)

    swings = _detect_swings(dataframe, swing_window=swing_window)
    labeled_swings = _label_swings(swings)
    trend = _determine_trend(labeled_swings)

    return {
        "trend": trend,
        "swing_count": len(labeled_swings),
        "swings": labeled_swings,
        "last_HH": _get_last_price_by_label(labeled_swings, "HH"),
        "last_HL": _get_last_price_by_label(labeled_swings, "HL"),
        "last_LH": _get_last_price_by_label(labeled_swings, "LH"),
        "last_LL": _get_last_price_by_label(labeled_swings, "LL"),
    }


def detect_swings(dataframe: pd.DataFrame, swing_window: int = 2) -> list[dict]:
    """Public helper for retrieving raw swing highs and lows."""

    validate_ohlc_dataframe(dataframe)
    return _detect_swings(dataframe, swing_window=swing_window)


def validate_ohlc_dataframe(dataframe: pd.DataFrame) -> None:
    """Public validation helper shared by later analysis modules."""

    _validate_ohlc_dataframe(dataframe)


def _validate_ohlc_dataframe(dataframe: pd.DataFrame) -> None:
    required_columns = {"time", "open", "high", "low", "close"}
    missing_columns = required_columns.difference(dataframe.columns)

    if missing_columns:
        missing_string = ", ".join(sorted(missing_columns))
        raise ValueError(f"OHLC dataframe is missing required columns: {missing_string}")

    if len(dataframe) < 5:
        raise ValueError("At least 5 candles are required to detect swing structure.")


def _detect_swings(dataframe: pd.DataFrame, swing_window: int) -> list[dict]:
    swings: list[dict] = []

    for index in range(swing_window, len(dataframe) - swing_window):
        current_row = dataframe.iloc[index]
        left_slice = dataframe.iloc[index - swing_window : index]
        right_slice = dataframe.iloc[index + 1 : index + swing_window + 1]

        is_swing_high = current_row["high"] > left_slice["high"].max() and current_row["high"] > right_slice["high"].max()
        is_swing_low = current_row["low"] < left_slice["low"].min() and current_row["low"] < right_slice["low"].min()

        if is_swing_high:
            swings.append(
                {
                    "index": int(index),
                    "time": current_row["time"],
                    "type": "high",
                    "price": float(current_row["high"]),
                }
            )

        if is_swing_low:
            swings.append(
                {
                    "index": int(index),
                    "time": current_row["time"],
                    "type": "low",
                    "price": float(current_row["low"]),
                }
            )

    swings.sort(key=lambda item: item["index"])
    return swings


def _label_swings(swings: list[dict]) -> list[dict]:
    labeled_swings: list[dict] = []
    previous_high: float | None = None
    previous_low: float | None = None

    for swing in swings:
        label: StructureLabel | None = None

        if swing["type"] == "high":
            label = "HH" if previous_high is None or swing["price"] > previous_high else "LH"
            previous_high = swing["price"]
        elif swing["type"] == "low":
            label = "HL" if previous_low is None or swing["price"] > previous_low else "LL"
            previous_low = swing["price"]

        labeled_swings.append(
            {
                "index": swing["index"],
                "time": pd.Timestamp(swing["time"]).isoformat(),
                "type": swing["type"],
                "price": round(float(swing["price"]), 4),
                "label": label,
            }
        )

    return labeled_swings


def _determine_trend(labeled_swings: list[dict]) -> TrendLabel:
    recent_high_labels = [swing["label"] for swing in labeled_swings if swing["type"] == "high"][-2:]
    recent_low_labels = [swing["label"] for swing in labeled_swings if swing["type"] == "low"][-2:]

    if len(recent_high_labels) >= 1 and len(recent_low_labels) >= 1:
        if recent_high_labels[-1] == "HH" and recent_low_labels[-1] == "HL":
            return "bullish"
        if recent_high_labels[-1] == "LH" and recent_low_labels[-1] == "LL":
            return "bearish"

    if recent_high_labels == ["HH", "HH"] and recent_low_labels == ["HL", "HL"]:
        return "bullish"
    if recent_high_labels == ["LH", "LH"] and recent_low_labels == ["LL", "LL"]:
        return "bearish"

    return "ranging"


def _get_last_price_by_label(swings: list[dict], label: StructureLabel) -> float | None:
    for swing in reversed(swings):
        if swing["label"] == label:
            return swing["price"]
    return None

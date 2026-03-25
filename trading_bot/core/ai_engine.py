from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score
from sklearn.model_selection import train_test_split

from trading_bot.core.market_structure import detect_market_structure, validate_ohlc_dataframe
from trading_bot.core.news_engine import derive_news_bias, get_pair_news_bias, split_symbol_currencies
from trading_bot.core.supply_demand import detect_supply_demand_zones


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
MODEL_PATH = DATA_DIR / "model.pkl"
METRICS_PATH = DATA_DIR / "ai_metrics.json"
DATASET_PATH = DATA_DIR / "ai_dataset.csv"
PREDICTIONS_LOG_PATH = DATA_DIR / "ai_predictions.csv"
TRADE_RESULTS_PATH = DATA_DIR / "trade_results.csv"


@dataclass(frozen=True)
class AiPredictionResult:
    """Standardized AI prediction output used across the bot."""

    ai_prediction: str
    confidence: int
    agreement_with_strategy: bool
    probabilities: dict[str, float]


@dataclass(frozen=True)
class ModelStatus:
    """Model metadata returned by the status endpoint."""

    model_available: bool
    model_path: str
    trained_at: str | None
    accuracy: float | None
    win_rate: float | None
    dataset_rows: int
    feature_count: int


def prepare_training_dataset(
    dataframe: pd.DataFrame,
    symbol: str,
    timeframe: str,
    news_events: list | None = None,
    future_horizon: int = 3,
    movement_threshold: float = 0.002,
) -> pd.DataFrame:
    """
    Build a supervised dataset from historical candles.

    Labels:
    - 1   => BUY
    - -1  => SELL
    - 0   => NO TRADE
    """

    validate_ohlc_dataframe(dataframe)
    dataset = _build_feature_frame(
        dataframe=dataframe,
        symbol=symbol,
        timeframe=timeframe,
        news_events=news_events or [],
    )
    dataset["label"] = _label_future_movement(
        dataframe=dataframe,
        future_horizon=future_horizon,
        movement_threshold=movement_threshold,
    )
    dataset.dropna(inplace=True)
    return dataset


def train_model(
    dataframe: pd.DataFrame,
    symbol: str,
    timeframe: str,
    news_events: list | None = None,
    future_horizon: int = 3,
    movement_threshold: float = 0.002,
) -> dict[str, Any]:
    """Train the lightweight classifier and persist the model and metrics."""

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    dataset = prepare_training_dataset(
        dataframe=dataframe,
        symbol=symbol,
        timeframe=timeframe,
        news_events=news_events,
        future_horizon=future_horizon,
        movement_threshold=movement_threshold,
    )
    if dataset.empty or len(dataset) < 30:
        raise ValueError("Not enough labeled rows to train the AI model.")

    feature_columns = [column for column in dataset.columns if column not in {"label", "time"}]
    X = dataset[feature_columns]
    y = dataset["label"]

    X_train, X_test, y_train, y_test = train_test_split(
        X,
        y,
        test_size=0.2,
        random_state=42,
        stratify=y if y.nunique() > 1 else None,
    )

    model = RandomForestClassifier(
        n_estimators=200,
        max_depth=8,
        min_samples_leaf=4,
        random_state=42,
        class_weight="balanced_subsample",
    )
    model.fit(X_train, y_train)

    test_predictions = model.predict(X_test)
    accuracy = float(accuracy_score(y_test, test_predictions))

    payload = {
        "model": model,
        "feature_columns": feature_columns,
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "trained_at": datetime.now(UTC).isoformat(),
    }
    joblib.dump(payload, MODEL_PATH)

    dataset.to_csv(DATASET_PATH, index=False)
    metrics = _build_metrics(
        accuracy=accuracy,
        dataset_rows=len(dataset),
        feature_count=len(feature_columns),
        trained_at=payload["trained_at"],
    )
    METRICS_PATH.write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    return {
        "message": "Model trained successfully.",
        "accuracy": accuracy,
        "dataset_rows": len(dataset),
        "feature_count": len(feature_columns),
        "model_path": str(MODEL_PATH),
    }


def predict_signal(
    dataframe: pd.DataFrame,
    symbol: str,
    timeframe: str,
    strategy_bias: str,
    news_events: list | None = None,
) -> AiPredictionResult:
    """Predict BUY / SELL / NO TRADE from the latest engineered feature row."""

    payload = _load_model_payload()
    feature_frame = _build_feature_frame(
        dataframe=dataframe,
        symbol=symbol,
        timeframe=timeframe,
        news_events=news_events or [],
    )
    if feature_frame.empty:
        raise ValueError("Unable to prepare AI features for prediction.")

    latest_row = feature_frame.iloc[[-1]]
    feature_columns = payload["feature_columns"]
    latest_features = latest_row[feature_columns]

    model: RandomForestClassifier = payload["model"]
    prediction_value = int(model.predict(latest_features)[0])
    probabilities_raw = model.predict_proba(latest_features)[0]
    classes = [int(label) for label in model.classes_]
    probability_map = {
        _label_to_name(label): float(probability)
        for label, probability in zip(classes, probabilities_raw)
    }
    predicted_label = _label_to_name(prediction_value)
    confidence = int(round(max(probability_map.values(), default=0.0) * 100))
    agreement = _does_ai_agree_with_strategy(predicted_label, strategy_bias)

    result = AiPredictionResult(
        ai_prediction=predicted_label,
        confidence=confidence,
        agreement_with_strategy=agreement,
        probabilities=probability_map,
    )
    log_prediction(
        symbol=symbol,
        timeframe=timeframe,
        strategy_bias=strategy_bias,
        result=result,
    )
    return result


def get_model_status() -> ModelStatus:
    """Return the current AI model status."""

    metrics = _load_metrics()
    return ModelStatus(
        model_available=MODEL_PATH.exists(),
        model_path=str(MODEL_PATH),
        trained_at=metrics.get("trained_at"),
        accuracy=metrics.get("accuracy"),
        win_rate=metrics.get("win_rate"),
        dataset_rows=int(metrics.get("dataset_rows", 0)),
        feature_count=int(metrics.get("feature_count", 0)),
    )


def log_prediction(
    symbol: str,
    timeframe: str,
    strategy_bias: str,
    result: AiPredictionResult,
) -> None:
    """Append each AI prediction to a log for later evaluation."""

    _append_row(
        PREDICTIONS_LOG_PATH,
        {
            "time": datetime.now(UTC).isoformat(),
            "symbol": symbol.upper(),
            "timeframe": timeframe,
            "strategy_bias": strategy_bias,
            "ai_prediction": result.ai_prediction,
            "confidence": result.confidence,
            "agreement_with_strategy": result.agreement_with_strategy,
        },
    )


def record_trade_result(
    symbol: str,
    timeframe: str,
    outcome: str,
    pnl: float,
    features: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Store trade outcomes for continuous learning and performance tracking.

    Outcome should usually be "win", "loss", or "breakeven".
    """

    row = {
        "time": datetime.now(UTC).isoformat(),
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "outcome": outcome.lower(),
        "pnl": pnl,
    }
    if features:
        row.update(features)

    _append_row(TRADE_RESULTS_PATH, row)
    win_rate = _calculate_win_rate()
    metrics = _load_metrics()
    metrics["win_rate"] = win_rate
    METRICS_PATH.write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    return {
        "message": "Trade result recorded.",
        "win_rate": win_rate,
        "trade_results_path": str(TRADE_RESULTS_PATH),
    }


def retrain_from_saved_dataset() -> dict[str, Any]:
    """Retrain the model from the persisted dataset and refresh metrics."""

    if not DATASET_PATH.exists():
        raise ValueError("No saved dataset found for retraining.")

    dataset = pd.read_csv(DATASET_PATH)
    if dataset.empty or "label" not in dataset.columns:
        raise ValueError("Saved dataset is invalid.")

    feature_columns = [column for column in dataset.columns if column not in {"label", "time"}]
    X = dataset[feature_columns]
    y = dataset["label"]

    X_train, X_test, y_train, y_test = train_test_split(
        X,
        y,
        test_size=0.2,
        random_state=42,
        stratify=y if y.nunique() > 1 else None,
    )

    model = RandomForestClassifier(
        n_estimators=200,
        max_depth=8,
        min_samples_leaf=4,
        random_state=42,
        class_weight="balanced_subsample",
    )
    model.fit(X_train, y_train)
    accuracy = float(accuracy_score(y_test, model.predict(X_test)))

    existing_payload = _load_model_payload() if MODEL_PATH.exists() else {}
    payload = {
        "model": model,
        "feature_columns": feature_columns,
        "symbol": existing_payload.get("symbol"),
        "timeframe": existing_payload.get("timeframe"),
        "trained_at": datetime.now(UTC).isoformat(),
    }
    joblib.dump(payload, MODEL_PATH)

    metrics = _build_metrics(
        accuracy=accuracy,
        dataset_rows=len(dataset),
        feature_count=len(feature_columns),
        trained_at=payload["trained_at"],
        win_rate=_calculate_win_rate(),
    )
    METRICS_PATH.write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    return {
        "message": "Model retrained from saved dataset.",
        "accuracy": accuracy,
        "dataset_rows": len(dataset),
        "feature_count": len(feature_columns),
    }


def build_integrated_ai_decision(
    strategy_payload: dict[str, Any],
    ai_result: AiPredictionResult | None,
) -> dict[str, Any]:
    """Merge AI output with the existing technical/news decision."""

    if ai_result is None:
        return {
            **strategy_payload,
            "ai_prediction": "NO TRADE",
            "ai_confidence": 0,
            "agreement_with_strategy": False,
        }

    final_bias = strategy_payload["final_bias"]
    base_confidence = int(strategy_payload["confidence"])
    adjusted_confidence = base_confidence
    setup = strategy_payload.get("setup")

    if ai_result.agreement_with_strategy:
        adjusted_confidence = min(100, base_confidence + 10)
    else:
        adjusted_confidence = max(0, base_confidence - 20)
        if setup and ai_result.ai_prediction == "NO TRADE":
            setup = None

    return {
        **strategy_payload,
        "setup": setup,
        "confidence": adjusted_confidence,
        "ai_prediction": ai_result.ai_prediction,
        "ai_confidence": ai_result.confidence,
        "agreement_with_strategy": ai_result.agreement_with_strategy,
        "final_bias": _resolve_final_bias_from_ai(final_bias, ai_result),
    }


def _build_feature_frame(
    dataframe: pd.DataFrame,
    symbol: str,
    timeframe: str,
    news_events: list,
) -> pd.DataFrame:
    """Build tabular ML features from the existing market analysis primitives."""

    validate_ohlc_dataframe(dataframe)
    features = dataframe[["time", "open", "high", "low", "close"]].copy()
    features["return_1"] = features["close"].pct_change()
    features["range"] = (features["high"] - features["low"]) / features["close"].replace(0, np.nan)
    features["body"] = (features["close"] - features["open"]) / features["open"].replace(0, np.nan)
    features["sma_10"] = features["close"].rolling(10).mean()
    features["sma_20"] = features["close"].rolling(20).mean()
    features["ema_10"] = features["close"].ewm(span=10, adjust=False).mean()
    features["ema_20"] = features["close"].ewm(span=20, adjust=False).mean()
    features["rsi_14"] = _calculate_rsi(features["close"], period=14)
    features["ma_spread"] = (features["sma_10"] - features["sma_20"]) / features["close"].replace(0, np.nan)

    structure = detect_market_structure(dataframe)
    features["trend_score"] = _bias_to_numeric(structure["trend"])

    zones = detect_supply_demand_zones(dataframe, symbol=symbol, timeframe=timeframe)
    latest_demand_zone = next((zone for zone in reversed(zones) if zone["type"] == "demand"), None)
    latest_supply_zone = next((zone for zone in reversed(zones) if zone["type"] == "supply"), None)
    features["distance_to_demand"] = features["close"].apply(
        lambda price: _distance_to_zone(price, latest_demand_zone)
    )
    features["distance_to_supply"] = features["close"].apply(
        lambda price: _distance_to_zone(price, latest_supply_zone)
    )

    currencies = list(split_symbol_currencies(symbol))
    news_bias_by_currency = derive_news_bias(
        currencies=currencies,
        events=news_events,
        current_time=datetime.now(UTC),
    )
    pair_news_bias = get_pair_news_bias(symbol=symbol, bias_by_currency=news_bias_by_currency)
    features["news_bias_score"] = _bias_to_numeric(pair_news_bias)

    features.replace([np.inf, -np.inf], np.nan, inplace=True)
    return features


def _label_future_movement(
    dataframe: pd.DataFrame,
    future_horizon: int,
    movement_threshold: float,
) -> pd.Series:
    """Create BUY / SELL / NO TRADE labels from forward returns."""

    future_close = dataframe["close"].shift(-future_horizon)
    future_return = (future_close - dataframe["close"]) / dataframe["close"].replace(0, np.nan)

    labels = np.where(
        future_return > movement_threshold,
        1,
        np.where(future_return < -movement_threshold, -1, 0),
    )
    return pd.Series(labels, index=dataframe.index, dtype=float)


def _calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Compute RSI without adding heavy TA dependencies."""

    delta = series.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)

    average_gain = gains.rolling(period).mean()
    average_loss = losses.rolling(period).mean()
    rs = average_gain / average_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)


def _bias_to_numeric(bias: str) -> int:
    mapping = {
        "strong bullish": 2,
        "bullish": 1,
        "neutral": 0,
        "ranging": 0,
        "bearish": -1,
        "strong bearish": -2,
    }
    return mapping.get(str(bias).lower(), 0)


def _distance_to_zone(price: float, zone: dict[str, Any] | None) -> float:
    if zone is None:
        return 1.0

    lower = min(zone["start_price"], zone["end_price"])
    upper = max(zone["start_price"], zone["end_price"])
    if lower <= price <= upper:
        return 0.0
    return min(abs(price - lower), abs(price - upper)) / max(price, 1e-9)


def _label_to_name(label: int) -> str:
    return {1: "BUY", -1: "SELL", 0: "NO TRADE"}.get(int(label), "NO TRADE")


def _does_ai_agree_with_strategy(ai_prediction: str, strategy_bias: str) -> bool:
    normalized_prediction = ai_prediction.upper()
    normalized_bias = str(strategy_bias).lower()
    if normalized_prediction == "BUY" and "bullish" in normalized_bias:
        return True
    if normalized_prediction == "SELL" and "bearish" in normalized_bias:
        return True
    if normalized_prediction == "NO TRADE" and normalized_bias in {"neutral", "ranging"}:
        return True
    return False


def _resolve_final_bias_from_ai(final_bias: str, ai_result: AiPredictionResult) -> str:
    if ai_result.agreement_with_strategy:
        return final_bias
    if ai_result.ai_prediction == "BUY":
        return "bullish"
    if ai_result.ai_prediction == "SELL":
        return "bearish"
    return final_bias


def _load_model_payload() -> dict[str, Any]:
    if not MODEL_PATH.exists():
        raise ValueError("AI model is not trained yet. Call /train first.")
    return joblib.load(MODEL_PATH)


def _append_row(path: Path, row: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame([row])
    if path.exists():
        frame.to_csv(path, mode="a", header=False, index=False)
    else:
        frame.to_csv(path, index=False)


def _build_metrics(
    accuracy: float,
    dataset_rows: int,
    feature_count: int,
    trained_at: str,
    win_rate: float | None = None,
) -> dict[str, Any]:
    return {
        "trained_at": trained_at,
        "accuracy": accuracy,
        "dataset_rows": dataset_rows,
        "feature_count": feature_count,
        "win_rate": win_rate if win_rate is not None else _calculate_win_rate(),
    }


def _load_metrics() -> dict[str, Any]:
    if not METRICS_PATH.exists():
        return {}
    return json.loads(METRICS_PATH.read_text(encoding="utf-8"))


def _calculate_win_rate() -> float | None:
    if not TRADE_RESULTS_PATH.exists():
        return None

    frame = pd.read_csv(TRADE_RESULTS_PATH)
    if frame.empty or "outcome" not in frame.columns:
        return None

    normalized = frame["outcome"].astype(str).str.lower()
    wins = (normalized == "win").sum()
    losses = (normalized == "loss").sum()
    total = wins + losses
    if total == 0:
        return None
    return float(wins / total)

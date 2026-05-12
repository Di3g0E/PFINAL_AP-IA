"""
Predicción temporal del gasto mensual (origen: P1_AP-IA/src/models/trainer.py).

Adaptación para P6:
  - Sin TensorFlow/Keras (excluido del stack).
  - Sin pmdarima (excluido del stack).
  - Sin XGBoost (excluido del stack).
  - Mantenidos: RandomForest, HistGradientBoosting, ARIMA (statsmodels).

A diferencia de P1, en P6 no entrenamos y persistimos modelos por usuario:
predecimos al vuelo sobre la serie mensual del usuario, que es del orden
de decenas de puntos. Esto evita el problema de versión de sklearn entre
P1 (1.8) y P6 (1.5) y la gestión de un fichero de modelo por usuario.

Cada predictor devuelve un dict:
    {"forecast": float, "lower": float, "upper": float, "method": str}
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from statsmodels.tsa.arima.model import ARIMA


def _build_lag_features(series: pd.Series, n_lags: int = 3) -> tuple[np.ndarray, np.ndarray]:
    """Crea matriz X (lags) e y (objetivo) a partir de una serie 1D."""
    values = series.values.astype(float)
    rows = []
    targets = []
    for i in range(n_lags, len(values)):
        rows.append(values[i - n_lags:i])
        targets.append(values[i])
    return np.asarray(rows), np.asarray(targets)


def predict_with_rf(series: pd.Series, n_lags: int = 3) -> dict:
    """Predice el siguiente periodo con RandomForest sobre features de lag.

    series: pd.Series indexada por periodo (ej. YearMonth) con valores numéricos.
    """
    if len(series) < n_lags + 2:
        raise ValueError(f"Se necesitan al menos {n_lags + 2} puntos; recibidos {len(series)}")

    series_sorted = series.sort_index()
    X, y = _build_lag_features(series_sorted, n_lags=n_lags)

    model = RandomForestRegressor(n_estimators=100, random_state=42)
    model.fit(X, y)

    # Predicción del siguiente periodo: usar los últimos n_lags valores
    last_window = series_sorted.values[-n_lags:].astype(float).reshape(1, -1)
    forecast = float(model.predict(last_window)[0])

    # Intervalo basado en la dispersión de árboles
    tree_preds = np.array([t.predict(last_window)[0] for t in model.estimators_])
    std = float(tree_preds.std())

    return {
        "forecast": forecast,
        "lower": forecast - 1.96 * std,
        "upper": forecast + 1.96 * std,
        "method": "RandomForest(lag=3)",
    }


def predict_with_hgb(series: pd.Series, n_lags: int = 3) -> dict:
    """Predice con HistGradientBoosting (rápido, robusto, sin gridsearch en v1)."""
    if len(series) < n_lags + 2:
        raise ValueError(f"Se necesitan al menos {n_lags + 2} puntos; recibidos {len(series)}")

    series_sorted = series.sort_index()
    X, y = _build_lag_features(series_sorted, n_lags=n_lags)

    model = HistGradientBoostingRegressor(max_iter=200, random_state=42)
    model.fit(X, y)

    last_window = series_sorted.values[-n_lags:].astype(float).reshape(1, -1)
    forecast = float(model.predict(last_window)[0])

    # Sin intervalo nativo: aproximamos con residuos in-sample
    in_sample = model.predict(X)
    residual_std = float(np.std(y - in_sample))

    return {
        "forecast": forecast,
        "lower": forecast - 1.96 * residual_std,
        "upper": forecast + 1.96 * residual_std,
        "method": "HistGradientBoosting(lag=3)",
    }


def predict_with_arima(series: pd.Series, order: tuple[int, int, int] = (1, 0, 1)) -> dict:
    """Predice con ARIMA (statsmodels nativo, sin pmdarima).

    En v1 usamos un orden fijo (1,0,1); para datos financieros mensuales
    es un punto de partida razonable. Búsqueda automática se podría añadir
    en v2 con grid manual o re-incluyendo pmdarima.
    """
    if len(series) < 6:
        raise ValueError(f"ARIMA necesita al menos 6 puntos; recibidos {len(series)}")

    series_sorted = series.sort_index().astype(float)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = ARIMA(series_sorted, order=order)
        fit = model.fit()

    forecast_obj = fit.get_forecast(steps=1)
    mean = float(forecast_obj.predicted_mean.iloc[0])
    ci = forecast_obj.conf_int(alpha=0.05).iloc[0]
    lower = float(ci.iloc[0])
    upper = float(ci.iloc[1])

    return {
        "forecast": mean,
        "lower": lower,
        "upper": upper,
        "method": f"ARIMA{order}",
    }


def predict_next_month(series: pd.Series, method: str = "rf") -> dict:
    """Punto de entrada único: elige predictor por nombre.

    Métodos disponibles: 'rf' | 'hgb' | 'arima'
    """
    method = method.lower()
    if method == "rf":
        return predict_with_rf(series)
    if method == "hgb":
        return predict_with_hgb(series)
    if method == "arima":
        return predict_with_arima(series)
    raise ValueError(f"Método desconocido: {method!r} (usa 'rf', 'hgb' o 'arima')")

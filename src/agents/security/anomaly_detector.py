"""
Detector de anomalías financieras (origen: P5_AP-IA/src/models/anomaly_detector.py).

Adaptación para P6:
  - Recibe un DataFrame ya cargado (columnas P6: `Amount_clean`, `Date_parsed`,
    `Area`, `Type`) en lugar de un CSV path.
  - El campo `Area` puede ser str o lista (multilabel del esquema). Lo
    aplanamos a string canónico antes de las estadísticas.
  - Mantiene el enfoque híbrido: Isolation Forest (multivariante) + 3-Sigma
    (univariante por categoría/tipo/global).

Uso típico:
    detector = FinancialAnomalyDetector(df)
    is_anomalous, reasons = detector.predict(
        date=date(2026, 4, 30), amount=5000.0, area="Leisure", type_val="Expenses"
    )
"""

from __future__ import annotations

from datetime import date
from typing import Optional

import pandas as pd
from loguru import logger
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import LabelEncoder


def _normalize_area(area) -> str:
    """`Area` puede ser str ('Leisure') o lista ('Leisure, Vacations'). Devuelve string canónico."""
    if isinstance(area, list):
        return ", ".join(area) if area else "Other"
    return str(area) if area else "Other"


class FinancialAnomalyDetector:
    """
    Detector híbrido (3-Sigma + Isolation Forest) que se entrena al instanciarse
    con el histórico del usuario y predice si una nueva transacción es atípica.
    """

    # Umbral mínimo de historia para que las reglas se activen. Por debajo
    # de esto, el detector simplemente devuelve "no anómalo" en cualquier
    # transacción (mejor falso negativo que falso positivo cuando el usuario
    # acaba de empezar y aún no hay datos suficientes para definir su patrón).
    MIN_HISTORY_FOR_RULES = 5

    def __init__(
        self,
        df: pd.DataFrame,
        contamination: float = 0.02,
        max_history: int = 5000,
        min_iforest_rows: int = 30,
    ):
        self.contamination = contamination
        self.max_history = max_history
        self.min_iforest_rows = min_iforest_rows

        self.iso_forest: Optional[IsolationForest] = None
        self.le_area = LabelEncoder()
        self.le_type = LabelEncoder()
        self._iforest_ready = False

        self.stats_by_area: dict[str, dict] = {}
        self.stats_by_type: dict[str, dict] = {}
        self.stats_global: dict = {}
        self.n_history: int = 0

        self._fit(df)

    def _fit(self, df: pd.DataFrame) -> None:
        if df is None or df.empty:
            logger.debug("AnomalyDetector: histórico vacío, sin entrenar.")
            return

        df = df.copy()
        # Normalizar Area si viene como lista
        df["Area_str"] = df["Area"].apply(_normalize_area)

        # Limitar el histórico
        if len(df) > self.max_history:
            df = df.tail(self.max_history).copy()

        self.n_history = len(df)

        # 3-Sigma global
        self.stats_global = {
            "mean": float(df["Amount_clean"].mean()),
            "std": float(df["Amount_clean"].std()),
        }

        # 3-Sigma por categoría
        for area, group in df.groupby("Area_str"):
            self.stats_by_area[str(area)] = {
                "mean": float(group["Amount_clean"].mean()),
                "std": float(group["Amount_clean"].std()) if len(group) > 1 else 0.0,
                "count": int(len(group)),
            }

        # 3-Sigma por tipo
        for t, group in df.groupby("Type"):
            self.stats_by_type[str(t)] = {
                "mean": float(group["Amount_clean"].mean()),
                "std": float(group["Amount_clean"].std()) if len(group) > 1 else 0.0,
                "count": int(len(group)),
            }

        # Isolation Forest (solo si hay suficientes filas y no hay NaN)
        if self.n_history >= self.min_iforest_rows:
            df_clean = df.dropna(subset=["Date_parsed", "Amount_clean"]).copy()
            if not df_clean.empty:
                df_clean["Area_encoded"] = self.le_area.fit_transform(df_clean["Area_str"])
                df_clean["Type_encoded"] = self.le_type.fit_transform(df_clean["Type"])
                df_clean["Month"] = df_clean["Date_parsed"].dt.month
                df_clean["DayOfWeek"] = df_clean["Date_parsed"].dt.dayofweek
                features = df_clean[[
                    "Amount_clean", "Area_encoded", "Type_encoded", "Month", "DayOfWeek",
                ]]
                self.iso_forest = IsolationForest(
                    contamination=self.contamination, random_state=42,
                )
                self.iso_forest.fit(features)
                self._iforest_ready = True
                logger.debug(f"AnomalyDetector entrenado con {self.n_history} filas.")

    def predict(
        self,
        *,
        date: "str | date",  # type: ignore[name-defined]  # noqa: F821
        amount: float,
        area,
        type_val: str,
    ) -> tuple[bool, list[str]]:
        """
        Evalúa una transacción candidata.

        Args:
            date: fecha (str 'YYYY-MM-DD' / 'DD/MM/YYYY' o `datetime.date`).
            amount: importe positivo en EUR.
            area: str o lista de áreas.
            type_val: 'Income' | 'Expenses'.

        Returns:
            (is_anomalous, reasons): True si al menos una regla la marca anómala.
        """
        reasons: list[str] = []
        area_str = _normalize_area(area)

        # Sin histórico significativo: no aplicamos ninguna regla. El usuario
        # acaba de empezar; flagearle todas las transacciones nuevas sería
        # contraproducente.
        if self.n_history < self.MIN_HISTORY_FOR_RULES:
            return False, []

        # 3-Sigma global
        g_mean = self.stats_global.get("mean")
        g_std = self.stats_global.get("std")
        if g_std is not None and g_std > 0 and g_mean is not None:
            if abs(amount - g_mean) > 3 * g_std:
                reasons.append(
                    f"3-Sigma global: importe atípico (media: {g_mean:.2f} EUR, σ: {g_std:.2f})"
                )

        # 3-Sigma por categoría
        if area_str in self.stats_by_area:
            stats = self.stats_by_area[area_str]
            if stats["count"] >= 3 and stats["std"] > 0:
                if abs(amount - stats["mean"]) > 3 * stats["std"]:
                    reasons.append(
                        f"3-Sigma categoría: importe atípico para '{area_str}' "
                        f"(media: {stats['mean']:.2f} EUR)"
                    )
        elif len(self.stats_by_area) >= 3:
            # Solo flageamos "nunca antes vista" si el usuario ya tiene un
            # repertorio de categorías razonable.
            reasons.append(f"Categoría '{area_str}' nunca antes vista.")

        # 3-Sigma por tipo
        if type_val in self.stats_by_type:
            stats = self.stats_by_type[type_val]
            if stats["count"] >= 3 and stats["std"] > 0:
                if abs(amount - stats["mean"]) > 3 * stats["std"]:
                    reasons.append(
                        f"3-Sigma tipo: importe atípico para '{type_val}' "
                        f"(media: {stats['mean']:.2f} EUR)"
                    )

        # Isolation Forest (si está entrenado)
        if self._iforest_ready and self.iso_forest is not None:
            try:
                area_enc = int(self.le_area.transform([area_str])[0])
            except ValueError:
                area_enc = -1
            try:
                type_enc = int(self.le_type.transform([type_val])[0])
            except ValueError:
                type_enc = -1

            month, day_of_week = _date_features(date)

            # Pasamos DataFrame (no ndarray) con los mismos nombres de columna
            # con los que se entrenó: evita el UserWarning de sklearn 1.5+.
            features = pd.DataFrame(
                [[amount, area_enc, type_enc, month, day_of_week]],
                columns=["Amount_clean", "Area_encoded", "Type_encoded", "Month", "DayOfWeek"],
            )
            try:
                pred = int(self.iso_forest.predict(features)[0])
                if pred == -1:
                    reasons.append("Isolation Forest: patrón multivariante inusual.")
            except Exception as e:
                logger.warning(f"Isolation Forest predict falló: {e}")

        return (len(reasons) > 0), reasons


def _date_features(value) -> tuple[int, int]:
    """Extrae (month, day_of_week) de varios formatos."""
    if hasattr(value, "month") and hasattr(value, "weekday"):
        return int(value.month), int(value.weekday())
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
            try:
                ts = pd.to_datetime(value, format=fmt)
                return int(ts.month), int(ts.dayofweek)
            except (ValueError, TypeError):
                continue
        try:
            ts = pd.to_datetime(value)
            return int(ts.month), int(ts.dayofweek)
        except Exception:
            pass
    return 1, 0

"""
Clasificador de área financiera (origen: P2_AP-IA/src/models/classifier.py).

Modelo de producción: TF-IDF (n-gramas de caracteres 2-5) + SGDClassifier
con `loss=log_loss`. Soporta `partial_fit` para aprendizaje incremental.

En P6 reutilizamos el modelo `models/area_classifier.joblib` ya entrenado con
las descripciones del CSV unificado. Para reentrenar: `FinancialClassifier.fit`.
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path

import joblib
from sklearn.exceptions import InconsistentVersionWarning
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import SGDClassifier

# Los modelos copiados de P2 fueron entrenados con sklearn 1.7/1.8 y P6 fija
# 1.5.0 (compatibilidad PaddleOCR). El pickle funciona, pero sklearn avisa.
# Silenciamos el aviso una sola vez aquí: el contrato del modelo es estable.
warnings.filterwarnings("ignore", category=InconsistentVersionWarning)


# Hiperparámetros heredados de P2 (probados en producción)
TFIDF_PARAMS: dict = {
    "analyzer": "char_wb",
    "ngram_range": (2, 5),
    "max_features": 10000,
}
SGD_PARAMS: dict = {
    "loss": "log_loss",
    "penalty": "l2",
    "alpha": 0.0001,
    "random_state": 42,
}


class FinancialClassifier:
    """Clasificador eficiente con soporte de aprendizaje incremental."""

    def __init__(self):
        self.vectorizer = TfidfVectorizer(**TFIDF_PARAMS)
        self.clf = SGDClassifier(**SGD_PARAMS)
        self.classes_ = None

    def fit(self, X, y):
        X_vec = self.vectorizer.fit_transform(X)
        self.clf.fit(X_vec, y)
        self.classes_ = self.clf.classes_

    def partial_fit(self, X, y):
        """Aprendizaje incremental sobre nuevas muestras (sin reentrenar todo)."""
        X_vec = self.vectorizer.transform(X)
        self.clf.partial_fit(X_vec, y, classes=self.classes_)

    def predict(self, X):
        X_vec = self.vectorizer.transform(X)
        return self.clf.predict(X_vec)

    def predict_proba(self, X):
        X_vec = self.vectorizer.transform(X)
        return self.clf.predict_proba(X_vec)

    def save(self, path: str | Path) -> None:
        path = str(path)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        joblib.dump({
            "vectorizer": self.vectorizer,
            "clf": self.clf,
            "classes": self.classes_,
        }, path)

    @classmethod
    def load(cls, path: str | Path) -> "FinancialClassifier":
        path = str(path)
        if not os.path.exists(path):
            raise FileNotFoundError(f"Modelo no encontrado: {path}")
        data = joblib.load(path)
        instance = cls()
        instance.vectorizer = data["vectorizer"]
        instance.clf = data["clf"]
        instance.classes_ = data["classes"]
        return instance

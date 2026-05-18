"""
Tests del HybridClassifier (la evolución E1 de P2).

Validan los tres modos básicos: zero-shot (usuario nuevo), bootstrap
al alcanzar el umbral, y personal tras entrenamiento.

NOTA: el `FakeEmbedder` del conftest devuelve vectores deterministas
pero NO mide calidad semántica. La accuracy real se mide aparte con
`scripts/eval/eval_p2_classifier.py`.
"""
from __future__ import annotations

import uuid

import pytest

from src.agents.registrar.classifier_hybrid import (
    KNOWN_CLASSES, ClassificationResult, HybridClassifier,
)
from src.agents.registrar.classifier_personal import COLD_START_THRESHOLD


@pytest.fixture
def user_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def hybrid() -> HybridClassifier:
    return HybridClassifier.shared()


def test_predict_returns_classification_result(hybrid, user_id):
    """Smoke test: el método devuelve la estructura esperada."""
    result = hybrid.predict(user_id, "cena en restaurante")

    assert isinstance(result, ClassificationResult)
    assert isinstance(result.area, list) and len(result.area) == 1
    assert result.area[0] in KNOWN_CLASSES
    assert 0.0 <= result.confidence <= 1.0


def test_new_user_uses_zero_shot(hybrid, user_id):
    """Un usuario sin histórico cae en zero-shot (no hay modelo personal)."""
    result = hybrid.predict(user_id, "cena en restaurante")

    assert result.mode == "zero_shot"
    assert result.user_history_size == 0


def test_bootstrap_after_threshold_samples(hybrid, user_id):
    """Tras N=COLD_START_THRESHOLD muestras confirmadas se entrena el modelo personal."""
    # Mezclamos clases para que el bootstrap tenga al menos 2 etiquetas
    labels = ["Food", "Leisure", "Salary"]
    for i in range(COLD_START_THRESHOLD - 1):
        state = hybrid.record_confirmed(user_id, f"compra #{i}", labels[i % 3])
        assert state == "buffering"

    # La muestra Nº (umbral) dispara el bootstrap
    state = hybrid.record_confirmed(user_id, "compra umbral", "Food")
    assert state in ("bootstrapped", "personal_updated")

    # A partir de aquí el predict debería usar el modelo personal
    result = hybrid.predict(user_id, "cena en pizzería")
    assert result.mode == "personal"

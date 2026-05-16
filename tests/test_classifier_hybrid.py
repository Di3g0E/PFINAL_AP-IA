"""Tests del HybridClassifier (E1 — evolución de P2).

Validan la lógica de orquestación (zero-shot vs personal), bootstrap al
alcanzar el umbral, partial_fit incremental, persistencia y fallback.

Usan el `FakeEmbedder` inyectado en conftest — NO miden calidad
semántica del modelo real, que se evalúa por separado con
`scripts/eval/eval_p2_classifier.py`.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from src.agents.registrar import classifier_personal
from src.agents.registrar.classifier_hybrid import (
    KNOWN_CLASSES, ClassificationResult, HybridClassifier,
)
from src.agents.registrar.classifier_personal import (
    COLD_START_THRESHOLD, PersonalClassifier, PersonalClassifierRegistry,
)
from src.agents.registrar.classifier_zeroshot import ZeroShotClassifier


@pytest.fixture
def user_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def hybrid() -> HybridClassifier:
    return HybridClassifier.shared()


# Estructura general


def test_known_classes_match_zeroshot_descriptions() -> None:
    """Garantiza que el universo de clases del híbrido y del zero-shot coincide."""
    zs = ZeroShotClassifier()
    assert set(zs.classes_) == set(KNOWN_CLASSES)


def test_predict_returns_classification_result(hybrid, user_id) -> None:
    result = hybrid.predict(user_id, "cena en restaurante")
    assert isinstance(result, ClassificationResult)
    assert isinstance(result.area, list) and len(result.area) == 1
    assert 0.0 <= result.confidence <= 1.0
    assert result.mode in {"zero_shot", "personal"}
    assert result.user_history_size >= 0


# Cold-start (zero-shot)


def test_cold_start_uses_zero_shot(hybrid, user_id) -> None:
    result = hybrid.predict(user_id, "cena en restaurante")
    assert result.mode == "zero_shot"
    assert result.user_history_size == 0


def test_zero_shot_label_is_known_class(hybrid, user_id) -> None:
    result = hybrid.predict(user_id, "cena en restaurante")
    assert result.area[0] in KNOWN_CLASSES


# Bootstrap → personal


def test_bootstrap_triggers_after_threshold(hybrid, user_id) -> None:
    """Tras N=COLD_START_THRESHOLD muestras confirmadas, se crea el modelo personal."""
    for i in range(COLD_START_THRESHOLD - 1):
        state = hybrid.record_confirmed(user_id, f"cena en restaurante #{i}", "Food")
        assert state == "buffering", f"sample {i}: {state}"
    # Muestra Nº (umbral) → bootstrap
    state = hybrid.record_confirmed(
        user_id, "cena nº umbral", "Food",
    )
    assert state == "bootstrapped"


def test_personal_used_after_bootstrap(hybrid, user_id) -> None:
    """Tras bootstrap, las predicciones usan el modelo personal."""
    # Llenamos el buffer con descripciones variadas (varias clases)
    samples = (
        [("cena pizza", "Food")] * 8
        + [("entrada cine", "Leisure")] * 8
        + [("recibo de la luz", "Invoice")] * 4
    )
    for desc, label in samples:
        hybrid.record_confirmed(user_id, desc, label)
    assert hybrid.registry.get(user_id) is not None

    result = hybrid.predict(user_id, "cena pizza margarita")
    assert result.mode == "personal"
    assert result.user_history_size >= COLD_START_THRESHOLD


def test_partial_fit_after_bootstrap_keeps_personal_mode(hybrid, user_id) -> None:
    """Una muestra adicional tras bootstrap debe usar partial_fit."""
    for i in range(COLD_START_THRESHOLD):
        hybrid.record_confirmed(user_id, f"compra {i}", "Food")
    state = hybrid.record_confirmed(user_id, "nueva muestra", "Food")
    assert state == "updated"
    pc = hybrid.registry.get(user_id)
    assert pc is not None
    assert pc.n_samples == COLD_START_THRESHOLD + 1


def test_ignored_when_label_unknown(hybrid, user_id) -> None:
    state = hybrid.record_confirmed(user_id, "alquiler", "ClaseInexistente")
    assert state == "ignored"


# Persistencia


def test_personal_classifier_persists_to_disk(hybrid, user_id) -> None:
    """Tras bootstrap, el .joblib debe existir en `PERSONAL_DIR`."""
    for i in range(COLD_START_THRESHOLD):
        # Mezcla clases para tener al menos 2 en el bootstrap
        label = "Food" if i % 2 == 0 else "Leisure"
        hybrid.record_confirmed(user_id, f"compra #{i}", label)
    pc = hybrid.registry.get(user_id)
    assert pc is not None
    assert pc.path().is_file()


def test_persisted_model_loads_correctly(hybrid, user_id) -> None:
    """Un modelo guardado se puede recargar sin pasar por el registry."""
    for i in range(COLD_START_THRESHOLD):
        hybrid.record_confirmed(user_id, f"compra #{i}", "Food")
    pc = hybrid.registry.get(user_id)
    assert pc is not None

    # Limpia el cache y fuerza recarga desde disco
    PersonalClassifierRegistry.reset()
    reloaded = PersonalClassifier.load(user_id)
    assert reloaded is not None
    assert reloaded.n_samples == COLD_START_THRESHOLD
    assert reloaded.classes_ == pc.classes_


# Integración con el Registrar agent


def test_classify_area_full_returns_metadata(user_id) -> None:
    """`classify_area_full` debe devolver área + confidence + mode + history."""
    from src.agents.registrar import agent as registrar
    result = registrar.classify_area_full(user_id, "cena en restaurante")
    assert "area" in result
    assert "confidence" in result
    assert "mode" in result
    assert "user_history_size" in result
    assert result["mode"] in {"zero_shot", "personal", "legacy"}


def test_classify_area_compat_returns_list(user_id) -> None:
    """`_classify_area` mantiene la firma `list[str]` para compatibilidad."""
    from src.agents.registrar import agent as registrar
    areas = registrar._classify_area(user_id, "cena en restaurante")
    assert isinstance(areas, list)
    assert all(isinstance(a, str) for a in areas)
    assert len(areas) >= 1

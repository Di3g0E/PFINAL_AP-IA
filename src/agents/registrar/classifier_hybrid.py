"""Clasificador híbrido: zero-shot + personal por usuario (E1).

Decide qué clasificador usar para cada usuario:
  - **Zero-shot** (transformer + centroides de clase): cuando el usuario
    tiene menos de N=20 transacciones confirmadas, o cuando su modelo
    personal aún no se ha entrenado en disco.
  - **Personal** (SGDClassifier sobre embeddings, partial_fit incremental):
    una vez que el usuario supera el umbral; se entrena en batch sobre
    sus N primeras muestras y luego se actualiza por cada confirmación.

Es el reemplazo directo de `FinancialClassifier` para la operación
`classify-area`. Mantiene API multilabel para compatibilidad (devuelve
`list[str]`), aunque internamente cada predicción es single-label
(la etiqueta más probable). Las clases multilabel como `Food, Vacations`
ya son un tag único en el universo del modelo.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from loguru import logger

from src.agents.registrar.classifier_personal import (
    COLD_START_THRESHOLD, PersonalClassifierRegistry,
)
from src.agents.registrar.classifier_zeroshot import (
    CLASS_DESCRIPTIONS, ZeroShotClassifier,
)
from src.agents.registrar.embedder import TransformerEmbedder


# Universo de clases conocido. Coincide con CLASS_DESCRIPTIONS y con las
# clases del modelo legacy `area_classifier.joblib` para asegurar que el
# híbrido pueda usar ambos sin inconsistencias.
KNOWN_CLASSES: list[str] = list(CLASS_DESCRIPTIONS.keys())


@dataclass
class ClassificationResult:
    """Resultado enriquecido del HybridClassifier."""
    area: list[str]
    confidence: float
    mode: str  # 'zero_shot' | 'personal'
    user_history_size: int


class HybridClassifier:
    """Orquesta zero-shot vs personal por usuario."""

    _instance: Optional["HybridClassifier"] = None

    def __init__(self,
                 cold_start_threshold: int = COLD_START_THRESHOLD,
                 embedder: Optional[TransformerEmbedder] = None,
                 zero_shot: Optional[ZeroShotClassifier] = None,
                 registry: Optional[PersonalClassifierRegistry] = None):
        self.cold_start_threshold = cold_start_threshold
        self.embedder = embedder or TransformerEmbedder.shared()
        self.zero_shot = zero_shot or ZeroShotClassifier(embedder=self.embedder)
        self.registry = registry or PersonalClassifierRegistry.shared(KNOWN_CLASSES)

    @classmethod
    def shared(cls) -> "HybridClassifier":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Limpia la instancia singleton — útil entre tests."""
        cls._instance = None

    # API principal

    def predict(self, user_id: str, description: str) -> ClassificationResult:
        """Predice el área para la descripción del usuario.

        Decide el modo en función del historial:
          - history_size < cold_start_threshold → zero-shot
          - history_size ≥ cold_start_threshold y modelo personal disponible → personal
          - personal no disponible (fichero corrupto, fallback) → zero-shot

        El campo `area` se devuelve como lista para compatibilidad con el
        contrato anterior multilabel. Hoy contiene un solo elemento.
        """
        try:
            history = self.registry.history_size(user_id)
        except Exception as e:
            logger.warning(f"history_size falló para {user_id}: {e}")
            history = 0

        # Cold-start: usar zero-shot
        if history < self.cold_start_threshold:
            label, conf = self.zero_shot.predict_with_confidence(description)
            return ClassificationResult(
                area=[label], confidence=conf, mode="zero_shot",
                user_history_size=history,
            )

        # Personal: intentar; si no hay modelo cargado, fallback a zero-shot
        personal = self.registry.predict(user_id, description)
        if personal is None:
            label, conf = self.zero_shot.predict_with_confidence(description)
            return ClassificationResult(
                area=[label], confidence=conf, mode="zero_shot",
                user_history_size=history,
            )
        label, conf = personal
        return ClassificationResult(
            area=[label], confidence=conf, mode="personal",
            user_history_size=history,
        )

    def record_confirmed(self, user_id: str, description: str, area: list[str] | str) -> str:
        """Registra una transacción confirmada para entrenamiento.

        `area` se acepta como lista (compatibilidad multilabel) o string.
        Si es lista de varios elementos se usa el primero (la firma del
        modelo personal es single-label).

        Devuelve el estado del entrenamiento (ver
        `PersonalClassifierRegistry.record_sample`):
        'buffering' | 'bootstrapped' | 'updated' | 'ignored'.
        """
        if isinstance(area, list):
            if not area:
                return "ignored"
            label = area[0]
        else:
            label = area
        return self.registry.record_sample(user_id, description, label)

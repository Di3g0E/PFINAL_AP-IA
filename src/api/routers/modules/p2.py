"""
P2 — Clasificación de categoría (Area) por descripción.

Tras la evolución E1, expone el `HybridClassifier` (zero-shot por
similitud con centroides de clase para usuarios nuevos + SGDClassifier
personal incremental sobre embeddings de transformer para usuarios con
historia). El clasificador legacy `FinancialClassifier` (TF-IDF char
n-grams + SGDClassifier global) se mantiene como fallback si el
transformer no está disponible.

Endpoints:
  - POST /modules/p2/classify-area
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from pydantic import BaseModel, Field

from src.agents.registrar import agent as registrar
from src.api.dependencies import get_current_user_id


router = APIRouter(prefix="/modules/p2", tags=["modules:P2 (classifier)"])


class ClassifyRequest(BaseModel):
    description: str = Field(..., min_length=1, max_length=500,
                             description="Texto descriptivo de la transacción.")


class ClassifyResponse(BaseModel):
    description: str
    area: list[str] = Field(
        default_factory=list,
        description=("Lista de categorías predichas. Multilabel: una descripción "
                     "como 'cine y restaurante' puede devolver ['Leisure', "
                     "'Restauración']. ['Other'] si no hay modelo cargado."),
    )
    confidence: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description="Confianza ∈ [0, 1] de la predicción top-1.",
    )
    mode: str = Field(
        default="legacy",
        description=("Modo del clasificador que produjo la predicción: "
                     "'zero_shot' (usuario cold-start, vía transformer + "
                     "centroides), 'personal' (modelo SGD entrenado con "
                     "historial del usuario), o 'legacy' (fallback al modelo "
                     "global TF-IDF si el transformer no está disponible)."),
    )
    user_history_size: int = Field(
        default=0,
        description="Número de transacciones confirmadas del usuario vistas "
                    "por el clasificador personal (informativo).",
    )


@router.post(
    "/classify-area",
    response_model=ClassifyResponse,
    summary="Clasifica el área/categoría de una transacción a partir de su descripción (P2)",
)
def classify_area(
    body: ClassifyRequest,
    user_id: str = Depends(get_current_user_id),
) -> ClassifyResponse:
    try:
        result = registrar.classify_area_full(user_id, body.description)
    except Exception as e:
        logger.exception(f"P2.classify_area falló: {e}")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Error clasificando: {type(e).__name__}",
        ) from e

    return ClassifyResponse(
        description=body.description,
        area=result["area"],
        confidence=result["confidence"],
        mode=result["mode"],
        user_history_size=result["user_history_size"],
    )

"""
P2 — Clasificación de categoría (Area) por descripción.

Expone como microservicio el `FinancialClassifier` (SGDClassifier + char
n-grams) entrenado en P2 y cargado por `src/agents/registrar/agent.py`.

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


@router.post(
    "/classify-area",
    response_model=ClassifyResponse,
    summary="Clasifica el área/categoría de una transacción a partir de su descripción (P2)",
)
def classify_area(
    body: ClassifyRequest,
    _user_id: str = Depends(get_current_user_id),
) -> ClassifyResponse:
    try:
        # `_classify_area` ya gestiona el caso de modelo no disponible
        # devolviendo ['Other'], así que no propaga excepciones triviales.
        area = registrar._classify_area(body.description)
    except Exception as e:
        logger.exception(f"P2.classify_area falló: {e}")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Error clasificando: {type(e).__name__}",
        ) from e

    return ClassifyResponse(description=body.description, area=area)

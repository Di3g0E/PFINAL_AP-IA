"""
P1 — Predicción temporal del gasto.

Expone como microservicio REST los predictores temporales (Random Forest,
HistGradientBoosting, ARIMA) implementados en `src/agents/analyst/forecasters.py`
y orquestados por `analyst.predict_next_month`.

Endpoints:
  - POST /modules/p1/predict-next-month
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from pydantic import BaseModel, Field

from src.agents.analyst import agent as analyst
from src.agents.analyst.data_source import load_user_transactions
from src.api.dependencies import get_current_user_id


router = APIRouter(prefix="/modules/p1", tags=["modules:P1 (forecasting)"])


class PredictNextMonthRequest(BaseModel):
    area: Optional[str] = Field(
        None,
        description=("Categoría sobre la que predecir el gasto del próximo mes. "
                     "Si se omite, se predice sobre el total de gastos."),
    )
    method: Literal["rf", "hgb", "arima"] = Field(
        "rf",
        description=("Método de predicción: rf=RandomForest, "
                     "hgb=HistGradientBoosting, arima=ARIMA."),
    )


class PredictNextMonthResponse(BaseModel):
    """Mismo shape que `AnalysisReport` para que el Orquestador pueda
    interpretarlo igual que la llamada in-process."""
    type: str
    period: Optional[str] = None
    metrics: dict[str, Any]
    series: list[dict[str, Any]] = []


@router.post(
    "/predict-next-month",
    response_model=PredictNextMonthResponse,
    summary="Predicción del gasto del próximo mes (P1)",
)
def predict_next_month(
    body: PredictNextMonthRequest,
    user_id: str = Depends(get_current_user_id),
) -> PredictNextMonthResponse:
    try:
        df = load_user_transactions(user_id)
        report = analyst.predict_next_month(df, area=body.area, method=body.method)
    except Exception as e:
        logger.exception(f"P1.predict_next_month falló: {e}")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Error en la predicción: {type(e).__name__}",
        ) from e

    return PredictNextMonthResponse(
        type=report.type,
        period=report.period,
        metrics=report.metrics,
        series=[p.model_dump() for p in report.series],
    )

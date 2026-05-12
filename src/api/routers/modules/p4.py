"""
P4 — Analytics financiero + gestión de objetivos.

Expone como microservicios todas las operaciones del agente Analyst que no
encajan en P1 (predicción) — los resúmenes mensuales, tendencias, anomalías,
gastos recurrentes y el CRUD de objetivos.

Endpoints (todos POST salvo el listado/borrado de objetivos):
  - POST   /modules/p4/monthly-summary
  - POST   /modules/p4/category-breakdown
  - POST   /modules/p4/spending-trends
  - POST   /modules/p4/savings-rate
  - POST   /modules/p4/detect-anomalies
  - POST   /modules/p4/recurring-expenses
  - POST   /modules/p4/recent-transactions
  - POST   /modules/p4/check-goals
  - POST   /modules/p4/goals          (set / upsert)
  - GET    /modules/p4/goals          (list)
  - DELETE /modules/p4/goals/{area}   (remove)
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from pydantic import BaseModel, Field

from src.agents.analyst import agent as analyst
from src.agents.analyst.data_source import load_user_transactions
from src.api.dependencies import get_current_user_id


router = APIRouter(prefix="/modules/p4", tags=["modules:P4 (analytics)"])


# Schemas

class ReportOut(BaseModel):
    """Mismo shape que `AnalysisReport` (Pydantic-friendly)."""
    type: str
    period: Optional[str] = None
    metrics: dict[str, Any]
    series: list[dict[str, Any]] = []
    goal_alerts: list[dict[str, Any]] = []


class MonthlySummaryRequest(BaseModel):
    year: Optional[int] = Field(None, ge=2000, le=2100)
    month: Optional[int] = Field(None, ge=1, le=12)


class PeriodRequest(BaseModel):
    period: Optional[str] = Field(
        None,
        description="YYYY-MM. Si se omite, usa el mes en curso.",
        pattern=r"^\d{4}-\d{2}$",
    )


class NMonthsRequest(BaseModel):
    n_months: int = Field(6, ge=1, le=60)


class TopNRequest(BaseModel):
    n: int = Field(10, ge=1, le=100)


class SetGoalRequest(BaseModel):
    area: str = Field(..., min_length=1, max_length=64)
    max_amount: Decimal = Field(..., gt=0)
    period: str = Field("monthly", pattern="^(monthly|weekly)$")


# Helpers

def _to_out(report) -> ReportOut:
    return ReportOut(
        type=report.type,
        period=report.period,
        metrics=report.metrics,
        series=[p.model_dump() for p in report.series],
        goal_alerts=[a.model_dump(mode="json") for a in report.goal_alerts],
    )


def _safe(op_name: str, fn, *args, **kwargs) -> ReportOut:
    """Llama a una operación del Analyst y traduce excepciones a HTTP 500.

    Se invoca dentro de cada handler para conservar la introspección de
    FastAPI sobre los `Depends(...)` y los `BaseModel` de los parámetros
    (los decoradores rompen `inspect.signature`).
    """
    try:
        return _to_out(fn(*args, **kwargs))
    except Exception as e:
        logger.exception(f"P4.{op_name} falló: {e}")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Error en {op_name}: {type(e).__name__}",
        ) from e


# Endpoints — análisis

@router.post("/monthly-summary", response_model=ReportOut,
             summary="Resumen mensual de ingresos/gastos/ahorro")
def monthly_summary(
    body: MonthlySummaryRequest,
    user_id: str = Depends(get_current_user_id),
) -> ReportOut:
    df = load_user_transactions(user_id)
    return _safe("monthly_summary", analyst.monthly_summary, df,
                 year=body.year, month=body.month)


@router.post("/category-breakdown", response_model=ReportOut,
             summary="Desglose de gasto por categoría")
def category_breakdown(
    body: PeriodRequest,
    user_id: str = Depends(get_current_user_id),
) -> ReportOut:
    df = load_user_transactions(user_id)
    return _safe("category_breakdown", analyst.category_breakdown, df,
                 period=body.period)


@router.post("/spending-trends", response_model=ReportOut,
             summary="Tendencia de gasto mes a mes (N últimos meses)")
def spending_trends(
    body: NMonthsRequest,
    user_id: str = Depends(get_current_user_id),
) -> ReportOut:
    df = load_user_transactions(user_id)
    return _safe("spending_trends", analyst.spending_trends, df,
                 n_months=body.n_months)


@router.post("/savings-rate", response_model=ReportOut,
             summary="Tasa de ahorro mensual (N últimos meses)")
def savings_rate(
    body: NMonthsRequest,
    user_id: str = Depends(get_current_user_id),
) -> ReportOut:
    df = load_user_transactions(user_id)
    return _safe("savings_rate", analyst.savings_rate, df,
                 n_months=body.n_months)


@router.post("/detect-anomalies", response_model=ReportOut,
             summary="Detección de gastos anómalamente altos")
def detect_anomalies(
    user_id: str = Depends(get_current_user_id),
) -> ReportOut:
    df = load_user_transactions(user_id)
    return _safe("detect_anomalies", analyst.detect_anomalies, df)


@router.post("/recurring-expenses", response_model=ReportOut,
             summary="Gastos recurrentes (suscripciones, facturas...)")
def recurring_expenses(
    user_id: str = Depends(get_current_user_id),
) -> ReportOut:
    df = load_user_transactions(user_id)
    return _safe("recurring_expenses", analyst.recurring_expenses, df)


@router.post("/recent-transactions", response_model=ReportOut,
             summary="Últimas N transacciones del usuario (orden desc)")
def recent_transactions(
    body: TopNRequest,
    user_id: str = Depends(get_current_user_id),
) -> ReportOut:
    df = load_user_transactions(user_id)
    return _safe("recent_transactions", analyst.recent_transactions, df, n=body.n)


# Endpoints — objetivos (CRUD)

@router.post("/check-goals", response_model=ReportOut,
             summary="Evalúa los objetivos activos contra el gasto del mes")
def check_goals(
    user_id: str = Depends(get_current_user_id),
) -> ReportOut:
    df = load_user_transactions(user_id)
    return _safe("check_goals", analyst.check_goals, df, user_id=user_id)


@router.post("/goals", response_model=ReportOut, status_code=status.HTTP_201_CREATED,
             summary="Crea o actualiza un objetivo de gasto (upsert por area)")
def set_goal(
    body: SetGoalRequest,
    user_id: str = Depends(get_current_user_id),
) -> ReportOut:
    return _safe("set_goal", analyst.set_goal, user_id,
                 area=body.area, max_amount=body.max_amount, period=body.period)


@router.get("/goals", response_model=ReportOut,
            summary="Lista los objetivos activos del usuario")
def list_goals(
    user_id: str = Depends(get_current_user_id),
) -> ReportOut:
    return _safe("list_goals", analyst.list_goals, user_id)


@router.delete("/goals/{area}", response_model=ReportOut,
               summary="Elimina (soft-delete) el objetivo activo de un area")
def remove_goal(
    area: str,
    user_id: str = Depends(get_current_user_id),
) -> ReportOut:
    return _safe("remove_goal", analyst.remove_goal, user_id, area=area)

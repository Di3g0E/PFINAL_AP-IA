"""
Tools del agente Analyst: hablan con /modules/p1/* (forecasting) y
/modules/p4/* (analytics + objetivos).

Cada función está decorada con `@tool` para que sea reconocible como tool
LangChain (introspección, schema Pydantic automático). Las llamamos desde
`orchestrator/nodes.py` vía `monthly_summary.invoke({...})`.

Devuelven `AnalysisReport` para que el nodo orquestador encaje 1:1 con la
versión anterior in-process (mismo contrato, distinto transporte).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from langchain_core.tools import tool

from src.agents.contracts import AnalysisReport, DataPoint, GoalAlert
from src.agents.tools import http_client


def _report_from_response(data: dict[str, Any]) -> AnalysisReport:
    """Reconstruye un AnalysisReport desde el JSON devuelto por /modules/p4 o p1."""
    return AnalysisReport(
        type=data.get("type", "summary"),
        period=data.get("period"),
        metrics=data.get("metrics", {}) or {},
        series=[DataPoint(**p) for p in data.get("series", []) or []],
        goal_alerts=[GoalAlert(**a) for a in data.get("goal_alerts", []) or []],
    )


@tool
def monthly_summary(user_id: str, year: Optional[int] = None,
                    month: Optional[int] = None) -> AnalysisReport:
    """Resumen mensual de ingresos, gastos y ahorro del usuario.

    Si no se pasa year/month, usa el mes en curso.
    """
    body: dict[str, Any] = {}
    if year is not None:
        body["year"] = year
    if month is not None:
        body["month"] = month
    data = http_client.post("/modules/p4/monthly-summary", user_id, body)
    return _report_from_response(data)


@tool
def category_breakdown(user_id: str, period: Optional[str] = None) -> AnalysisReport:
    """Desglose del gasto por categoría/área en un periodo concreto (YYYY-MM)."""
    body: dict[str, Any] = {}
    if period:
        body["period"] = period
    data = http_client.post("/modules/p4/category-breakdown", user_id, body)
    return _report_from_response(data)


@tool
def spending_trends(user_id: str, n_months: int = 6) -> AnalysisReport:
    """Tendencia mensual del gasto en los últimos N meses."""
    data = http_client.post("/modules/p4/spending-trends", user_id,
                            {"n_months": n_months})
    return _report_from_response(data)


@tool
def savings_rate(user_id: str, n_months: int = 6) -> AnalysisReport:
    """Tasa de ahorro mensual (ingresos - gastos) / ingresos en los últimos N meses."""
    data = http_client.post("/modules/p4/savings-rate", user_id,
                            {"n_months": n_months})
    return _report_from_response(data)


@tool
def detect_anomalies(user_id: str) -> AnalysisReport:
    """Detecta gastos anormalmente altos en el histórico del usuario."""
    data = http_client.post("/modules/p4/detect-anomalies", user_id, {})
    return _report_from_response(data)


@tool
def recurring_expenses(user_id: str) -> AnalysisReport:
    """Identifica gastos recurrentes (suscripciones, facturas...) del usuario."""
    data = http_client.post("/modules/p4/recurring-expenses", user_id, {})
    return _report_from_response(data)


@tool
def recent_transactions(user_id: str, n: int = 10) -> AnalysisReport:
    """Las N transacciones más recientes (orden descendente por fecha)."""
    data = http_client.post("/modules/p4/recent-transactions", user_id, {"n": n})
    return _report_from_response(data)


@tool
def predict_next_month(user_id: str, area: Optional[str] = None,
                       method: str = "rf") -> AnalysisReport:
    """Predicción del gasto del próximo mes (RF, HistGradientBoosting o ARIMA).

    Si `area` se especifica filtra por categoría; si no, predice sobre el total.
    """
    body: dict[str, Any] = {"method": method}
    if area:
        body["area"] = area
    data = http_client.post("/modules/p1/predict-next-month", user_id, body)
    return _report_from_response(data)


@tool
def check_goals(user_id: str) -> AnalysisReport:
    """Evalúa los objetivos activos del usuario contra el gasto del mes en curso."""
    data = http_client.post("/modules/p4/check-goals", user_id, {})
    return _report_from_response(data)


@tool
def set_goal(user_id: str, area: str, max_amount: float,
             period: str = "monthly") -> AnalysisReport:
    """Crea o actualiza un objetivo de gasto máximo para una categoría."""
    body = {
        "area": area,
        "max_amount": str(Decimal(str(max_amount))),
        "period": period,
    }
    data = http_client.post("/modules/p4/goals", user_id, body)
    return _report_from_response(data)


@tool
def list_goals(user_id: str) -> AnalysisReport:
    """Lista los objetivos activos del usuario."""
    data = http_client.get("/modules/p4/goals", user_id)
    return _report_from_response(data)


@tool
def remove_goal(user_id: str, area: str) -> AnalysisReport:
    """Borra (soft-delete) el objetivo activo de la categoría indicada."""
    data = http_client.delete(f"/modules/p4/goals/{area}", user_id)
    return _report_from_response(data or {})

"""
Endpoints REST del agente Monitor.

Requieren autenticación: cualquier usuario logueado puede ver el snapshot
de salud (no expone datos sensibles, solo agregados sin PII).

  - GET /monitor/health-detailed?window_minutes=60
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from src.agents.monitor import MonitorAgent, MonitoringReport
from src.api.dependencies import get_current_user_id


router = APIRouter(prefix="/monitor", tags=["monitor"])


@router.get(
    "/health-detailed",
    response_model=MonitoringReport,
    summary="Snapshot agregado del estado del sistema (Punto 4 del enunciado)",
)
def health_detailed(
    window_minutes: int = Query(
        60, ge=5, le=1440,
        description="Ventana de tiempo en minutos (5–1440). Default: 60.",
    ),
    _user_id: str = Depends(get_current_user_id),
) -> MonitoringReport:
    """Devuelve volumen total, error_rate, p50/p95 de latencia por agente
    y acción, top errores y sesiones activas en la ventana indicada."""
    return MonitorAgent(window_minutes=window_minutes).evaluate()

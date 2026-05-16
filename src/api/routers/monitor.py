"""
Endpoints REST del agente Monitor.

Requieren autenticación: cualquier usuario logueado puede ver el snapshot
de salud (no expone datos sensibles, solo agregados sin PII).

  - GET /monitor/health-detailed?window_minutes=60
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

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


class EventOut(BaseModel):
    """Una fila de la tabla `events` para el analizador de logs."""
    ts: datetime
    agent: str
    action: str
    status: str
    latency_ms: Optional[int] = None
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    payload: Optional[dict] = None


@router.get(
    "/events",
    response_model=list[EventOut],
    summary="Stream de los últimos eventos de la tabla `events` (analizador de logs)",
)
def list_events(
    limit: int = Query(100, ge=1, le=500, description="Nº de eventos a devolver"),
    status_filter: Optional[str] = Query(
        None, alias="status",
        description="Filtra por status ('ok', 'error', 'warning', 'denied').",
    ),
    agent_filter: Optional[str] = Query(
        None, alias="agent",
        description="Filtra por agente ('orchestrator', 'security'...).",
    ),
    _user_id: str = Depends(get_current_user_id),
) -> list[EventOut]:
    """Lista los eventos más recientes (sin PII en claro — la tabla solo
    guarda IDs/hashes). Pensado para que el panel admin pueda hacer
    análisis ad-hoc de logs sin abrir Langfuse / ficheros."""
    from sqlalchemy import select, desc
    from src.data.database import get_session
    from src.data.schema import Event

    with get_session() as s:
        stmt = select(Event).order_by(desc(Event.ts)).limit(limit)
        if status_filter:
            stmt = stmt.where(Event.status == status_filter)
        if agent_filter:
            stmt = stmt.where(Event.agent == agent_filter)
        rows = s.execute(stmt).scalars().all()
        return [
            EventOut(
                ts=e.ts,
                agent=e.agent,
                action=e.action,
                status=e.status,
                latency_ms=e.latency_ms,
                user_id=str(e.user_id) if e.user_id else None,
                session_id=str(e.session_id) if e.session_id else None,
                payload=e.payload,
            )
            for e in rows
        ]

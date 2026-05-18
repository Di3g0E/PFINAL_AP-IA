"""Endpoint admin-only para generar y devolver el grafo agéntico a partir de `events`.

Ruta: GET /admin/langfuse/graph
Requiere JWT y `is_admin=True`.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query

from src.api.dependencies import require_admin
from src.api.routers.langfuse_utils import build_agent_graph
from src.data.database import get_session
from src.data.schema import Event


router = APIRouter(prefix="/admin/langfuse", tags=["admin"])


@router.get("/graph", summary="Genera y devuelve grafo agéntico desde la tabla events")
def get_agent_graph(window_hours: int = Query(24, ge=1, le=24*30), _admin: str = Depends(require_admin)):
    """Construye grafo dirigido entre agentes a partir de eventos en la ventana indicada.

    La lógica es: por cada `session_id` ordenamos eventos por `ts` y añadimos aristas
    entre agentes consecutivos. Se devuelven `nodes` (conteos) y `edges` (conteo, avg_latency_ms).
    """
    cutoff = datetime.utcnow() - timedelta(hours=window_hours)
    with get_session() as s:
        q = s.query(Event).filter(Event.ts >= cutoff).order_by(Event.ts.asc())
        events = q.all()

    graph = build_agent_graph(events)
    return {"generated_at": datetime.utcnow().isoformat(), "window_hours": window_hours, "graph": graph}

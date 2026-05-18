"""Endpoint de Langfuse para usuarios autenticados."""
from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from src.api.dependencies import get_current_user_id
from src.api.routers.langfuse_utils import build_agent_graph
from src.data.database import get_session
from src.data.schema import Event, User


router = APIRouter(prefix="/langfuse", tags=["langfuse"])


@router.get("/graph", summary="Genera y devuelve el grafo de Langfuse para el usuario autenticado")
def get_user_agent_graph(
    window_hours: int = Query(24, ge=1, le=24 * 30),
    user_id: str = Depends(get_current_user_id),
):
    """Devuelve el grafo de agentes creado solo a partir de los eventos del usuario actual."""
    try:
        uid = UUID(user_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token con sub inválido")

    cutoff = datetime.utcnow() - timedelta(hours=window_hours)
    with get_session() as s:
        user = s.execute(select(User).where(User.id == uid)).scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuario no encontrado")

        events = (
            s.query(Event)
            .filter(Event.user_id == uid)
            .filter(Event.ts >= cutoff)
            .order_by(Event.ts.asc())
            .all()
        )

    graph = build_agent_graph(events)
    return {
        "generated_at": datetime.utcnow().isoformat(),
        "window_hours": window_hours,
        "graph": graph,
    }

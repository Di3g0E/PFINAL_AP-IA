"""Grafo agéntico para el usuario autenticado.

Endpoint público (cualquier usuario logueado puede ver SU propio grafo):

  GET /me/agent-graph?window_hours=24&format=json|dot|png

Filtra la tabla `events` por `user_id` y construye nodos diferenciados
por rol (`basic` / `advanced`) — eso refleja cómo el sistema selecciona
distintos prompts según la preferencia que tenga el usuario activada en
ese momento (ver `src/agents/orchestrator/prompts.py:_ROLE_STYLE_BLOCKS`).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select

from src.api.dependencies import get_current_user_id
from src.data.database import get_session
from src.data.schema import Event, User
from src.utils.agent_graph_builder import (
    build_user_agent_graph, graph_to_dot, graph_to_png,
)


router = APIRouter(prefix="/me", tags=["agent-graph"])


@router.get(
    "/agent-graph",
    summary="Grafo agéntico del usuario autenticado (JSON | DOT | PNG)",
)
def get_user_agent_graph(
    window_hours: int = Query(24, ge=1, le=24 * 30),
    format: str = Query("json", pattern="^(json|dot|png)$"),
    user_id: str = Depends(get_current_user_id),
):
    """Construye el grafo de agentes con los eventos del usuario actual.

    El `format` controla la representación:
      - `json` (default): `{generated_at, window_hours, role, graph}`
      - `dot`:  texto Graphviz DOT (Content-Type: text/vnd.graphviz).
      - `png`:  PNG generado por el binario `dot` (requiere graphviz
                instalado en el sistema; si falta, 503 con explicación).
    """
    try:
        uid = UUID(user_id)
    except (ValueError, TypeError) as exc:
        # En la práctica esto no ocurre: si el JWT validó, el sub es un UUID.
        # Lo dejamos por simetría con el resto de la API.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token con sub inválido",
        ) from exc

    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    with get_session() as s:
        user_role = s.execute(
            select(User.role).where(User.id == uid)
        ).scalar_one_or_none()
        events = (
            s.query(Event)
            .filter(Event.user_id == uid)
            .filter(Event.ts >= cutoff)
            .order_by(Event.ts.asc())
            .all()
        )

    graph = build_user_agent_graph(events, fallback_role=user_role)

    if format == "dot":
        dot = graph_to_dot(graph, title=f"User {uid} — last {window_hours}h")
        return Response(content=dot, media_type="text/vnd.graphviz")
    if format == "png":
        try:
            png = graph_to_png(graph, title=f"User {uid} — last {window_hours}h")
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
        return Response(content=png, media_type="image/png")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_hours": window_hours,
        "role": user_role,
        "graph": graph,
    }

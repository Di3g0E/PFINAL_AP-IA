"""Grafo agéntico system-wide para administradores.

Endpoint:
  GET /admin/agent-graph?window_hours=24&format=json|dot|png

Devuelve **dos grafos** lado a lado:

  - `graph_app`: solo agentes de aplicación (orchestrator, analyst,
    registrar, security, conversational, api) — vista "¿qué hacen los
    usuarios?".
  - `graph_ops`: solo agentes del propio admin chat (`admin_orchestrator`,
    `observability`) — vista "¿qué hago yo (admin)?".

Plus `meta` con resúmenes de Langfuse SaaS, MonitorAgent y log file.

Formatos:
  - `json` (default): JSON con ambos grafos + meta.
  - `dot` y `png` siguen exportando solo el grafo de aplicación, que es
    el caso de uso primario para una memoria académica.

Si Langfuse no está configurado, el campo `meta.langfuse.enabled` será
false y los grafos se construyen solo desde `events`. No es un error.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from src.agents.monitor import MonitorAgent
from src.api.dependencies import require_admin
from src.data.database import get_session
from src.data.schema import Event
from src.utils.agent_graph_builder import (
    build_admin_agent_graph, graph_to_dot, graph_to_png,
)
from src.utils.langfuse_fetch import fetch_summary
from src.utils.log_analyzer import analyze_log_file


router = APIRouter(prefix="/admin", tags=["admin"])


@router.get(
    "/agent-graph",
    summary="Grafos agénticos system-wide (app + ops) para admin",
)
def get_admin_agent_graph(
    window_hours: int = Query(24, ge=1, le=24 * 30),
    format: str = Query("json", pattern="^(json|dot|png)$"),
    include_langfuse: bool = Query(True, description="Enriquecer con resumen de Langfuse SaaS."),
    include_logs: bool = Query(True, description="Adjuntar análisis de logs/app.log."),
    include_monitor: bool = Query(True, description="Adjuntar snapshot de MonitorAgent."),
    _admin: str = Depends(require_admin),
):
    """Solo para `is_admin=True`. Devuelve los dos grafos + telemetría."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=window_hours)

    with get_session() as s:
        events = (
            s.query(Event)
            .filter(Event.ts >= cutoff)
            .order_by(Event.ts.asc())
            .all()
        )

    # Particionamos UNA vez la lista de eventos en {app, ops}. El builder
    # también sabe filtrar, pero pasarle ya filtrado evita iterar dos veces.
    graph_app = build_admin_agent_graph(events, kind="app")
    graph_ops = build_admin_agent_graph(events, kind="ops")

    if format == "dot":
        # DOT/PNG solo del grafo de aplicación — el ops es secundario y
        # se renderiza desde el JSON en el frontend.
        dot = graph_to_dot(graph_app, title=f"System (app) — last {window_hours}h")
        return Response(content=dot, media_type="text/vnd.graphviz")
    if format == "png":
        try:
            png = graph_to_png(graph_app, title=f"System (app) — last {window_hours}h")
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
        return Response(content=png, media_type="image/png")

    langfuse_payload = None
    if include_langfuse:
        langfuse_payload = fetch_summary(from_time=cutoff, to_time=now, limit=500)

    monitor_payload = None
    if include_monitor:
        # MonitorAgent usa minutos en su ventana; mapeamos horas → minutos
        # respetando el límite de la API (5–1440 min ≈ 24h).
        win_min = max(5, min(1440, window_hours * 60))
        monitor_payload = MonitorAgent(window_minutes=win_min).evaluate().model_dump()

    logs_payload = None
    if include_logs:
        logs_payload = analyze_log_file(window_hours=window_hours).to_dict()

    return {
        "generated_at": now.isoformat(),
        "window_hours": window_hours,
        "graph_app": graph_app,
        "graph_ops": graph_ops,
        "meta": {
            "langfuse": langfuse_payload,
            "monitor": monitor_payload,
            "logs": logs_payload,
        },
    }

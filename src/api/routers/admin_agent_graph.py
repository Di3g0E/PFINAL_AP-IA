"""Grafo agéntico system-wide para administradores.

Endpoint:
  GET /admin/agent-graph?window_hours=24&format=json|dot|png

Combina tres fuentes:

  1. **Tabla `events`** (todos los usuarios): aristas por encadenamiento
     dentro de cada sesión, count y latencia por nodo/arista.
  2. **Langfuse SaaS** (si configurado): observaciones (`SPAN` + `GENERATION`)
     en la ventana, enriquece nodos con tokens y coste.
  3. **MonitorAgent** + **analyzer del log file** (`logs/app.log`): se
     incluyen en `meta.monitor` y `meta.logs` del payload para que el
     frontend pinte KPIs + anomalías al lado del grafo.

Si Langfuse no está configurado, el campo `meta.langfuse.enabled` será
false y el grafo se construye solo desde `events`. No es un error.
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
    summary="Grafo agéntico system-wide (admin)",
)
def get_admin_agent_graph(
    window_hours: int = Query(24, ge=1, le=24 * 30),
    format: str = Query("json", pattern="^(json|dot|png)$"),
    include_langfuse: bool = Query(True, description="Enriquecer con observaciones de Langfuse SaaS."),
    include_logs: bool = Query(True, description="Adjuntar análisis de logs/app.log."),
    include_monitor: bool = Query(True, description="Adjuntar snapshot de MonitorAgent."),
    _admin: str = Depends(require_admin),
):
    """Solo para `is_admin=True`. Devuelve el grafo system-wide enriquecido."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=window_hours)

    with get_session() as s:
        events = (
            s.query(Event)
            .filter(Event.ts >= cutoff)
            .order_by(Event.ts.asc())
            .all()
        )

    langfuse_payload = None
    if include_langfuse:
        langfuse_payload = fetch_summary(from_time=cutoff, to_time=now, limit=500)

    # El grafo se construye SOLO desde `events` porque las observaciones de
    # Langfuse en este proyecto no llevan `name` por observación (la
    # instrumentación nombra el trace raíz como `chat.request` pero no las
    # sub-observaciones). Mezclarlas en el grafo introducía nodos
    # `langfuse:None` sin valor. El resumen Langfuse (coste, tokens,
    # modelos, traces por nombre) va en `meta.langfuse`.
    graph = build_admin_agent_graph(events)

    if format == "dot":
        dot = graph_to_dot(graph, title=f"System — last {window_hours}h")
        return Response(content=dot, media_type="text/vnd.graphviz")
    if format == "png":
        try:
            png = graph_to_png(graph, title=f"System — last {window_hours}h")
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
        return Response(content=png, media_type="image/png")

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
        "graph": graph,
        "meta": {
            "langfuse": langfuse_payload,
            "monitor": monitor_payload,
            "logs": logs_payload,
        },
    }

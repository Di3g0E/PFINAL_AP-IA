"""Endpoint admin-only para generar y devolver el grafo agéntico a partir de `events`.

Ruta: GET /admin/langfuse/graph
Requiere JWT y `is_admin=True`.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query

from src.api.dependencies import require_admin
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

    # Construcción simple de grafo (igual que scripts/build_agent_graph.py)
    sessions = {}
    for e in events:
        key = str(e.session_id) if e.session_id else f"no-session-{e.user_id}"
        sessions.setdefault(key, []).append(e)

    nodes = {}
    edges = {}
    for sess_id, evs in sessions.items():
        evs.sort(key=lambda x: x.ts)
        prev = None
        for ev in evs:
            nodes.setdefault(ev.agent, {"count": 0})["count"] += 1
            if prev is not None:
                k = (prev, ev.agent)
                ent = edges.setdefault(k, {"count": 0, "latencies": []})
                ent["count"] += 1
                if ev.latency_ms is not None:
                    ent["latencies"].append(ev.latency_ms)
            prev = ev.agent

    nodes_out = [{"agent": k, "count": v["count"]} for k, v in nodes.items()]
    edges_out = []
    for (src, dst), v in edges.items():
        lat = v["latencies"]
        avg = int(sum(lat) / len(lat)) if lat else None
        edges_out.append({"source": src, "target": dst, "count": v["count"], "avg_latency_ms": avg})

    return {"generated_at": datetime.utcnow().isoformat(), "window_hours": window_hours, "graph": {"nodes": nodes_out, "edges": edges_out}}

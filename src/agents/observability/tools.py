"""Tools del agente Observability — el "sub-agente" que consulta el sistema.

Cada tool va instrumentada con `Stopwatch(agent="observability", action=<tool>)`
para que las llamadas queden grabadas en la tabla `events` con un agente
distinto al de los agentes "de aplicación" (orchestrator/analyst/...). Eso
permite que `build_admin_agent_graph(events, kind="ops")` las separe del
flujo financiero.

Filosofía de diseño:
  - Las tools devuelven dicts simples (no Pydantic models) para que el
    LLM las pueda leer directamente sin perder información en el render.
  - Cada tool ya existe a más alto nivel en el proyecto (MonitorAgent,
    log_analyzer, langfuse_fetch, agent_graph_builder); aquí solo las
    envolvemos con `@tool` y filtros razonables.
  - Nada de PII en claro: las queries a `events` no exponen `payload`
    en bruto, solo agregan/cuentan. El admin si quiere ver detalle puede
    usar `query_recent_events` que devuelve campos no-sensibles.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from langchain_core.tools import tool
from loguru import logger

from src.agents.monitor import MonitorAgent
from src.utils.agent_graph_builder import build_admin_agent_graph
from src.utils.langfuse_fetch import fetch_summary
from src.utils.log_analyzer import analyze_log_file
from src.utils.logging_config import Stopwatch


_AGENT_NAME = "observability"


@tool
def get_system_health(window_minutes: int = 60) -> dict[str, Any]:
    """Snapshot de salud del sistema en la ventana indicada.

    Devuelve métricas agregadas de la tabla `events`: volumen total,
    error_rate, health ∈ {ok, warning, critical}, p50/p95 de latencia por
    (agente, acción) y top errores. Útil para responder "¿cómo está el
    sistema?" / "¿hay incidentes?".

    Args:
        window_minutes: ventana en minutos a evaluar. 5-1440.
    """
    with Stopwatch(agent=_AGENT_NAME, action="get_system_health") as sw:
        win = max(5, min(1440, int(window_minutes)))
        report = MonitorAgent(window_minutes=win).evaluate()
        sw.payload["window_minutes"] = win
        sw.payload["total_events"] = report.total_events
        return report.model_dump(mode="json")


@tool
def query_recent_events(
    agent: Optional[str] = None,
    status: Optional[str] = None,
    action: Optional[str] = None,
    since_hours: int = 24,
    limit: int = 50,
) -> dict[str, Any]:
    """Lista eventos recientes de la tabla `events` con filtros opcionales.

    Pensada para "¿qué hizo el agente X anoche?" o "muéstrame los últimos
    errores del Registrar". No expone `payload` en bruto (puede tener
    detalle por turno) — solo metadatos (timestamp, agente, acción, status,
    latencia, IDs).

    Args:
        agent: filtra por nombre exacto: 'orchestrator', 'analyst',
            'registrar', 'security', 'conversational', 'admin_orchestrator',
            'observability', 'api'.
        status: 'ok' | 'error' | 'warning' | 'denied'.
        action: nombre exacto de la acción.
        since_hours: ventana en horas (default 24).
        limit: máximo de eventos a devolver (1-200).
    """
    with Stopwatch(agent=_AGENT_NAME, action="query_recent_events") as sw:
        from sqlalchemy import desc, select
        from src.data.database import get_session, is_database_configured
        from src.data.schema import Event

        sw.payload["filters"] = {"agent": agent, "status": status,
                                 "action": action, "since_hours": since_hours}

        if not is_database_configured():
            return {"events": [], "count": 0,
                    "note": "DATABASE_URL no configurada."}

        cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, since_hours))
        limit = max(1, min(200, int(limit)))

        with get_session() as s:
            stmt = (select(Event).where(Event.ts >= cutoff)
                    .order_by(desc(Event.ts)).limit(limit))
            if agent:
                stmt = stmt.where(Event.agent == agent)
            if status:
                stmt = stmt.where(Event.status == status)
            if action:
                stmt = stmt.where(Event.action == action)
            rows = s.execute(stmt).scalars().all()

        events_out = [{
            "ts": e.ts.isoformat() if e.ts else None,
            "agent": e.agent, "action": e.action, "status": e.status,
            "latency_ms": e.latency_ms,
            "session_id": str(e.session_id) if e.session_id else None,
            "user_id": str(e.user_id) if e.user_id else None,
        } for e in rows]
        sw.payload["returned"] = len(events_out)
        return {"events": events_out, "count": len(events_out)}


@tool
def analyze_log_anomalies(window_hours: int = 24) -> dict[str, Any]:
    """Análisis del fichero `logs/app.log` (loguru JSONL).

    Detecta:
      - Volumen alto de ERROR/CRITICAL en la ventana.
      - Spikes (≥3 errores del mismo módulo en <60s).
      - Mensajes repetidos (≥5 veces).
      - CRITICALs individuales.

    Devuelve además counts por nivel y top módulos con errores. Útil para
    "¿qué pasó en el último crash?" o "resumen del log de hoy".

    Args:
        window_hours: ventana en horas (default 24).
    """
    with Stopwatch(agent=_AGENT_NAME, action="analyze_log_anomalies") as sw:
        report = analyze_log_file(window_hours=max(1, int(window_hours)))
        d = report.to_dict()
        sw.payload["anomalies"] = len(d["anomalies"])
        sw.payload["lines_parsed"] = d["lines_parsed"]
        return d


@tool
def get_llm_usage(window_hours: int = 24) -> dict[str, Any]:
    """Resumen de uso del LLM via Langfuse: tokens, coste, modelos, endpoints.

    Si Langfuse no está configurado o falla, devuelve `enabled=False` y
    `note` explicativo — NO levanta excepción. Útil para "¿cuánto hemos
    gastado esta semana?" o "¿qué modelos se están usando?".

    Args:
        window_hours: ventana en horas (default 24).
    """
    with Stopwatch(agent=_AGENT_NAME, action="get_llm_usage") as sw:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=max(1, int(window_hours)))
        summary = fetch_summary(from_time=cutoff, to_time=now, limit=500)
        sw.payload["traces"] = summary["traces_count"]
        sw.payload["ok"] = summary["ok"]
        return summary


@tool
def get_agent_flow_summary(
    window_hours: int = 24,
    kind: str = "app",
) -> dict[str, Any]:
    """Resumen agregado del grafo agéntico system-wide.

    Devuelve un dict con `nodes` (count, error_rate, users_distinct,
    avg_latency_ms por agente) y `edges` (count, avg_latency por transición).
    Útil para "¿quién es el cuello de botella?" o "¿cuál es el flujo más
    frecuente?".

    Args:
        window_hours: ventana en horas (default 24).
        kind: 'app' (agentes de aplicación), 'ops' (agentes del propio
            admin chat) o 'all' (todo mezclado).
    """
    with Stopwatch(agent=_AGENT_NAME, action="get_agent_flow_summary") as sw:
        from sqlalchemy import select
        from src.data.database import get_session, is_database_configured
        from src.data.schema import Event

        if not is_database_configured():
            return {"graph": {"nodes": [], "edges": []},
                    "note": "DATABASE_URL no configurada."}

        cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, int(window_hours)))
        with get_session() as s:
            events = (s.query(Event)
                      .filter(Event.ts >= cutoff)
                      .order_by(Event.ts.asc()).all())

        graph = build_admin_agent_graph(events, kind=kind)
        sw.payload["kind"] = kind
        sw.payload["nodes"] = len(graph["nodes"])
        return {"window_hours": window_hours, "kind": kind, "graph": graph}


# Registro público — el agente lo importa de aquí.
OBSERVABILITY_TOOLS = [
    get_system_health,
    query_recent_events,
    analyze_log_anomalies,
    get_llm_usage,
    get_agent_flow_summary,
]

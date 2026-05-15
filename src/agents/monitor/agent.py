"""
MonitorAgent: lee la tabla `events` y produce un `MonitoringReport`.

Diseño:
  - Sin estado: el agente es stateless, cada `evaluate()` consulta la BD.
  - Una sola tabla (`events`): poblada por `log_event` desde cada
    `Stopwatch` y desde código de error. Inserción best-effort.
  - Sin LLM: la "evaluación" es estadística clásica (count + percentiles).
    El LLM no aporta valor aquí — cifras son cifras.

Métricas calculadas en una ventana móvil (default 60 min):
  - total / ok / error / warning / denied (count por status)
  - error_rate y health ∈ {ok, warning, critical}
  - p50 / p95 / max de latencia agrupado por (agent, action)
  - top 5 errores por (agent, action)
  - sesiones y usuarios distintos
"""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone
from typing import Optional

from loguru import logger

from src.agents.monitor.report import LatencyStats, MonitoringReport, TopError


class MonitorAgent:
    """Evalúa la salud del sistema a partir de la tabla `events`."""

    def __init__(self, window_minutes: int = 60):
        self.window_minutes = window_minutes

    def evaluate(self) -> MonitoringReport:
        """Genera un snapshot del estado del sistema.

        Es robusto: si la BD no está disponible o algún query falla,
        devuelve un report con `db_available=False` y `note` explicativo
        en lugar de propagar la excepción.
        """
        generated_at = datetime.now(timezone.utc)
        cutoff = generated_at - timedelta(minutes=self.window_minutes)
        report = MonitoringReport(
            generated_at=generated_at, window_minutes=self.window_minutes,
        )

        try:
            from sqlalchemy import func, select

            from src.data.database import get_session, is_database_configured
            from src.data.schema import Event
        except Exception as e:
            report.db_available = False
            report.note = f"Imports BD fallaron: {type(e).__name__}: {e}"
            return report

        if not is_database_configured():
            report.db_available = False
            report.note = "DATABASE_URL no configurada — sin datos de eventos."
            return report

        try:
            with get_session() as session:
                # 1) Counts por status
                rows = session.execute(
                    select(Event.status, func.count(Event.id))
                    .where(Event.ts >= cutoff)
                    .group_by(Event.status)
                ).all()
                counts_by_status = {status: int(c) for status, c in rows}
                report.total_events = sum(counts_by_status.values())
                report.ok_count = counts_by_status.get("ok", 0)
                report.error_count = counts_by_status.get("error", 0)
                report.warning_count = counts_by_status.get("warning", 0)
                report.denied_count = counts_by_status.get("denied", 0)

                if report.total_events:
                    report.error_rate = round(report.error_count / report.total_events, 4)
                report.health = _classify_health(report.error_rate)

                # 2) Latencias agrupadas por (agent, action) — calculamos
                #    p50/p95 en Python (Postgres y SQLite difieren mucho en
                #    sus funciones de percentil; statistics es portátil).
                latency_rows = session.execute(
                    select(Event.agent, Event.action, Event.latency_ms)
                    .where(Event.ts >= cutoff)
                    .where(Event.latency_ms.isnot(None))
                ).all()
                grouped: dict[tuple[str, str], list[int]] = {}
                for agent, action, lat in latency_rows:
                    grouped.setdefault((agent, action), []).append(int(lat))
                report.latencies = sorted(
                    [_stats(agent, action, vals)
                     for (agent, action), vals in grouped.items()],
                    key=lambda s: s.count, reverse=True,
                )[:15]  # top 15 (más volumen primero)

                # 3) Top errores (count desc)
                error_rows = session.execute(
                    select(Event.agent, Event.action, func.count(Event.id))
                    .where(Event.ts >= cutoff)
                    .where(Event.status == "error")
                    .group_by(Event.agent, Event.action)
                    .order_by(func.count(Event.id).desc())
                    .limit(5)
                ).all()
                report.top_errors = [
                    TopError(agent=a, action=ac, error_count=int(c))
                    for a, ac, c in error_rows
                ]

                # 4) Sesiones y usuarios activos
                report.active_sessions = int(session.execute(
                    select(func.count(func.distinct(Event.session_id)))
                    .where(Event.ts >= cutoff)
                    .where(Event.session_id.isnot(None))
                ).scalar_one() or 0)
                report.active_users = int(session.execute(
                    select(func.count(func.distinct(Event.user_id)))
                    .where(Event.ts >= cutoff)
                    .where(Event.user_id.isnot(None))
                ).scalar_one() or 0)
        except Exception as e:
            logger.exception(f"MonitorAgent.evaluate falló: {e}")
            report.db_available = False
            report.note = f"Query falló: {type(e).__name__}: {e}"

        return report


def _classify_health(error_rate: float) -> str:
    if error_rate < 0.05:
        return "ok"
    if error_rate < 0.20:
        return "warning"
    return "critical"


def _stats(agent: str, action: str, values: list[int]) -> LatencyStats:
    """Calcula p50/p95/max de una lista de latencias en ms."""
    if not values:
        return LatencyStats(agent=agent, action=action, count=0)
    values_sorted = sorted(values)
    p50 = float(statistics.median(values_sorted))
    # p95 con quantiles (Python 3.8+). Si solo hay 1 valor, fallback.
    if len(values_sorted) >= 2:
        try:
            p95 = float(statistics.quantiles(values_sorted, n=20)[18])
        except statistics.StatisticsError:
            p95 = float(values_sorted[-1])
    else:
        p95 = float(values_sorted[-1])
    return LatencyStats(
        agent=agent, action=action, count=len(values_sorted),
        p50_ms=round(p50, 1), p95_ms=round(p95, 1), max_ms=float(values_sorted[-1]),
    )

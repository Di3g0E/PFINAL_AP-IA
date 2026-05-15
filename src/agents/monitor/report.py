"""
Contratos del agente Monitor: salida estructurada que el endpoint y el
frontend consumen.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class LatencyStats(BaseModel):
    """Estadísticas de latencia por (agent, action)."""
    agent: str
    action: str
    count: int
    p50_ms: Optional[float] = None
    p95_ms: Optional[float] = None
    max_ms: Optional[float] = None


class TopError(BaseModel):
    """Top N actions con más errores en la ventana evaluada."""
    agent: str
    action: str
    error_count: int


class MonitoringReport(BaseModel):
    """Snapshot agregado del estado del sistema en una ventana de tiempo."""
    generated_at: datetime
    window_minutes: int = Field(
        ..., description="Ventana de tiempo (en minutos) que evalúa este informe.",
    )

    # Volumen total y reparto por status
    total_events: int = 0
    ok_count: int = 0
    error_count: int = 0
    warning_count: int = 0
    denied_count: int = 0

    # Salud
    error_rate: float = Field(
        0.0,
        description="error_count / total_events (0.0–1.0). Indicador de salud principal.",
    )
    health: str = Field(
        "ok",
        description=("'ok' si error_rate < 0.05; 'warning' si < 0.20; "
                     "'critical' si ≥ 0.20."),
    )

    # Latencias por (agent, action)
    latencies: list[LatencyStats] = Field(default_factory=list)

    # Top errores
    top_errors: list[TopError] = Field(default_factory=list)

    # Actividad
    active_sessions: int = Field(
        0, description="Sesiones distintas con actividad en la ventana.",
    )
    active_users: int = Field(
        0, description="Usuarios distintos con actividad en la ventana.",
    )

    # Diagnóstico
    db_available: bool = True
    note: Optional[str] = None

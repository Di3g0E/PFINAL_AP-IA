"""
Agente Monitor: evalúa automáticamente el estado del sistema.

Cumple el punto 4 del enunciado:
  > "se debe incluir algún agente que evalúe los eventos relevantes,
  > errores, tiempos de respuesta o acciones ejecutadas por el sistema"

Lee la tabla `events` (populada por `log_event` desde cada Stopwatch) y
calcula métricas agregadas: error_rate, p50/p95 de latencia por agente y
acción, top errores, sesiones activas. Se ejecuta:

  - On-demand vía `GET /monitor/health-detailed`.
  - Periódicamente desde el lifespan de FastAPI (background task cada N s).
"""

from src.agents.monitor.agent import MonitorAgent  # noqa: F401
from src.agents.monitor.report import (  # noqa: F401
    LatencyStats, MonitoringReport, TopError,
)

"""
Configuración de logging estructurado.

  - Si LOG_FORMAT=json → cada línea es un objeto JSON con campos fijos
    (ts, level, agent, action, status, latency_ms, msg, payload).
    Esto facilita la futura ingesta en una tabla `events` o en
    Loki/Grafana en v2.
  - Si LOG_FORMAT=human → formato legible por humanos para desarrollo.

Uso:
    from src.utils.logging_config import configure_logging, log_event
    configure_logging()
    log_event(agent='security', action='login_attempt', status='ok',
              latency_ms=123, user_id='...', payload={'similarity': 0.92})

Reglas:
  - Nunca incluir PII en claro en `payload` (no description, no amount).
  - Solo IDs (user_id, transaction_id) o hashes.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from src.utils.config import settings


def _record_to_json(record: dict) -> str:
    """Serializa un registro de loguru como una línea JSON."""
    payload = {
        "ts": record["time"].isoformat(),
        "level": record["level"].name,
        "msg": record["message"],
    }
    extra = record.get("extra") or {}
    for key in ("agent", "action", "status", "latency_ms",
                "user_id", "session_id", "payload"):
        if key in extra and extra[key] is not None:
            payload[key] = extra[key]
    return json.dumps(payload, ensure_ascii=False, default=str)


def _json_stderr_sink(message) -> None:
    """Sink: escribe la línea JSON en stderr."""
    sys.stderr.write(_record_to_json(message.record) + "\n")


def _json_file_sink_factory(log_path: Path):
    """Devuelve un sink que escribe en fichero (sin rotación gestionada aquí)."""
    handle = open(log_path, "a", encoding="utf-8")

    def sink(message) -> None:
        handle.write(_record_to_json(message.record) + "\n")
        handle.flush()
    return sink


def configure_logging() -> None:
    """
    Configura loguru según settings. Idempotente.

    Sinks:
      - stderr (humano coloreado para dev, JSON para prod)
      - fichero rotado (siempre JSON, para auditoría)
    """
    logger.remove()

    log_path = Path(settings.log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    if settings.log_format == "json":
        logger.add(_json_stderr_sink, level=settings.log_level)
    else:
        logger.add(sys.stderr, level=settings.log_level,
                   format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | "
                          "<cyan>{name}</cyan> - <level>{message}</level>")

    # Fichero rotado, siempre JSON (para futura tabla events)
    logger.add(str(log_path), level="INFO", rotation="10 MB", retention="90 days",
               serialize=True, enqueue=True)

    logger.debug(f"Logging configurado: level={settings.log_level} format={settings.log_format}")


def log_event(
    *,
    agent: str,
    action: str,
    status: str = "ok",
    latency_ms: Optional[int] = None,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    payload: Optional[dict[str, Any]] = None,
    msg: Optional[str] = None,
) -> None:
    """
    Emite un evento estructurado al log.

    Args:
        agent: 'orchestrator'|'security'|'registrar'|'analyst'|'api'
        action: identificador corto del evento (snake_case)
        status: 'ok'|'error'|'denied'|'warning'
        payload: metadatos sin PII en claro
    """
    level = "INFO" if status == "ok" else ("ERROR" if status == "error" else "WARNING")
    logger.bind(
        agent=agent, action=action, status=status,
        latency_ms=latency_ms, user_id=user_id, session_id=session_id,
        payload=payload,
    ).log(level, msg or f"{agent}.{action} {status}")


class Stopwatch:
    """Context manager para medir latencia y emitir un evento al salir."""

    def __init__(self, *, agent: str, action: str,
                 user_id: Optional[str] = None, session_id: Optional[str] = None):
        self.agent = agent
        self.action = action
        self.user_id = user_id
        self.session_id = session_id
        self.start: float = 0.0
        self.payload: dict[str, Any] = {}
        self.status: str = "ok"

    def __enter__(self) -> "Stopwatch":
        self.start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        latency_ms = int((time.perf_counter() - self.start) * 1000)
        if exc_type is not None:
            self.status = "error"
            self.payload["error"] = str(exc_val)
        log_event(agent=self.agent, action=self.action, status=self.status,
                  latency_ms=latency_ms, user_id=self.user_id,
                  session_id=self.session_id, payload=self.payload or None)

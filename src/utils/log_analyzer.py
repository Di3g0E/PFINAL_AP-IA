"""Analizador del fichero `logs/app.log` (formato JSONL de loguru).

La tabla `events` ya cubre el eje "qué hicieron los agentes". El fichero
de log adicionalmente contiene:

  - WARNINGs internos que no llegan a `events` (timeouts puntuales,
    deserializaciones fallidas, llamadas a recursos opcionales).
  - Excepciones con stacktrace completo.
  - Detalle por módulo:función:línea que ayuda al admin a ubicar
    rápidamente el origen de un incidente.

Esta función no pretende sustituir un APM real — produce un informe
agregado pensado para el panel de admin.

Anomalías detectadas:
  - Volumen alto de ERROR/CRITICAL en la ventana.
  - Módulos con tasa anómala de errores (≥ 3 ERROR del mismo módulo en
    menos de 1 minuto → spike).
  - Mensajes repetidos (mismo `message` aparece ≥ 5 veces).
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional


LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
DEFAULT_LOG_FILE = LOG_DIR / "app.log"


@dataclass
class LogAnomaly:
    kind: str               # 'volume', 'spike', 'repeated', 'critical'
    severity: str           # 'info', 'warning', 'critical'
    description: str
    sample_message: Optional[str] = None
    occurrences: int = 1


@dataclass
class LogAnalysisReport:
    window_hours: int
    lines_scanned: int = 0
    lines_parsed: int = 0
    counts_by_level: dict[str, int] = field(default_factory=dict)
    top_modules_with_errors: list[dict[str, Any]] = field(default_factory=list)
    top_repeated_messages: list[dict[str, Any]] = field(default_factory=list)
    anomalies: list[LogAnomaly] = field(default_factory=list)
    note: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "window_hours": self.window_hours,
            "lines_scanned": self.lines_scanned,
            "lines_parsed": self.lines_parsed,
            "counts_by_level": self.counts_by_level,
            "top_modules_with_errors": self.top_modules_with_errors,
            "top_repeated_messages": self.top_repeated_messages,
            "anomalies": [a.__dict__ for a in self.anomalies],
            "note": self.note,
        }


def analyze_log_file(
    path: Path = DEFAULT_LOG_FILE,
    window_hours: int = 24,
    *,
    max_lines: int = 50_000,
) -> LogAnalysisReport:
    """Analiza el fichero loguru JSONL `path` y devuelve un report.

    El parámetro `max_lines` limita cuántas líneas leemos (desde el final)
    para no bloquear el endpoint con logs de varios MB. Suficiente para
    una ventana de 24h en un entorno académico.
    """
    report = LogAnalysisReport(window_hours=window_hours)

    if not path.exists():
        report.note = f"No existe el fichero de log: {path}"
        return report

    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)

    try:
        raw_lines = _tail_lines(path, max_lines)
    except OSError as exc:
        report.note = f"Error leyendo log: {exc}"
        return report

    report.lines_scanned = len(raw_lines)

    levels = Counter()
    modules_with_errors: Counter = Counter()
    repeated_messages: Counter = Counter()
    minute_buckets: dict[tuple[str, str], list[datetime]] = defaultdict(list)
    critical_samples: list[tuple[str, str]] = []

    for line in raw_lines:
        record = _parse_loguru_line(line)
        if record is None:
            continue

        ts = record["ts"]
        if ts < cutoff:
            continue

        report.lines_parsed += 1
        level = record["level"]
        levels[level] += 1
        message = record["message"]
        module = record["module"]

        if level in ("ERROR", "CRITICAL"):
            modules_with_errors[module] += 1
            minute_buckets[(module, level)].append(ts)
            if level == "CRITICAL":
                critical_samples.append((module, message))

        if level in ("WARNING", "ERROR", "CRITICAL"):
            # Las repeticiones se cuentan por el mensaje literal (sin el
            # ts ni el detalle) — recortamos a 200 chars para agrupar.
            repeated_messages[message[:200]] += 1

    report.counts_by_level = dict(levels)
    report.top_modules_with_errors = [
        {"module": m, "count": c}
        for m, c in modules_with_errors.most_common(10)
    ]
    report.top_repeated_messages = [
        {"message": m, "count": c}
        for m, c in repeated_messages.most_common(10)
        if c >= 2
    ]

    # ----- Anomalías -----
    # 1) Volumen alto de errores
    total_errors = levels.get("ERROR", 0) + levels.get("CRITICAL", 0)
    if total_errors >= 20:
        report.anomalies.append(LogAnomaly(
            kind="volume",
            severity="critical" if total_errors >= 50 else "warning",
            description=f"{total_errors} errores en {window_hours}h",
            occurrences=total_errors,
        ))

    # 2) Spikes (≥ 3 errores del mismo módulo en < 60s)
    for (module, level), timestamps in minute_buckets.items():
        timestamps.sort()
        for i in range(len(timestamps) - 2):
            if (timestamps[i + 2] - timestamps[i]).total_seconds() <= 60:
                report.anomalies.append(LogAnomaly(
                    kind="spike",
                    severity="warning",
                    description=f"3+ {level} en <60s en módulo {module}",
                    occurrences=len([t for t in timestamps
                                     if (timestamps[i + 2] - t).total_seconds() <= 60]),
                ))
                break

    # 3) Mensajes muy repetidos (≥ 5 veces el mismo)
    for msg, count in repeated_messages.most_common():
        if count >= 5:
            report.anomalies.append(LogAnomaly(
                kind="repeated",
                severity="warning" if count < 20 else "critical",
                description=f"Mensaje repetido {count} veces",
                sample_message=msg,
                occurrences=count,
            ))

    # 4) CRITICALs (siempre anómalos)
    for module, sample in critical_samples[:5]:
        report.anomalies.append(LogAnomaly(
            kind="critical",
            severity="critical",
            description=f"CRITICAL en {module}",
            sample_message=sample,
        ))

    return report


def _tail_lines(path: Path, max_lines: int) -> list[str]:
    """Lee las últimas `max_lines` líneas del fichero sin cargar todo en RAM.

    Implementación simple: para los tamaños esperados (≤ 50MB) basta con
    abrir y leer todo. Si el fichero crece, sustituir por un seek desde
    el final.
    """
    with path.open("r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    return lines[-max_lines:]


def _parse_loguru_line(line: str) -> Optional[dict[str, Any]]:
    """Parse una línea JSON de loguru. Devuelve None si no es parseable.

    Loguru con `serialize=True` produce un dict con esta forma::

        {
          "text": "...",
          "record": {
            "level": {"name": "INFO", "no": 20, "icon": "..."},
            "message": "...",
            "module": "database",
            "time": {"repr": "...", "timestamp": 1778567001.739277},
            "name": "src.data.database",
            "function": "get_engine",
            "line": 72,
            ...
          }
        }
    """
    line = line.strip()
    if not line:
        return None
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None

    record = data.get("record") or {}
    level_info = record.get("level") or {}
    time_info = record.get("time") or {}

    ts_value = time_info.get("timestamp")
    if ts_value is None:
        return None
    try:
        ts = datetime.fromtimestamp(float(ts_value), tz=timezone.utc)
    except (TypeError, ValueError):
        return None

    return {
        "ts": ts,
        "level": level_info.get("name") or "INFO",
        "message": record.get("message") or "",
        "module": record.get("name") or record.get("module") or "unknown",
        "function": record.get("function") or "",
        "line": record.get("line") or 0,
    }

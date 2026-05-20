"""
Tests del builder de grafo agéntico y del analizador de logs.

Lógica pura: sin BD ni FastAPI. Construimos Events en memoria y verificamos
la salida del builder.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.data.schema import Event
from src.utils.agent_graph_builder import (
    build_admin_agent_graph, build_user_agent_graph,
)
from src.utils.log_analyzer import analyze_log_file


def _evt(*, agent, ts, session_id=None, user_id=None, role=None, status="ok"):
    """Construye un Event sin tocar BD (SQLAlchemy lo permite)."""
    payload = {"role": role} if role else None
    return Event(ts=ts, agent=agent, action="test", status=status,
                 latency_ms=100, session_id=session_id, user_id=user_id,
                 payload=payload)


def test_user_graph_node_includes_role():
    """En el grafo de usuario el rol forma parte del id del nodo."""
    sess = uuid.uuid4()
    base = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    events = [
        _evt(agent="orchestrator", ts=base, session_id=sess, role="advanced"),
        _evt(agent="analyst", ts=base + timedelta(seconds=1),
             session_id=sess, role="advanced"),
    ]
    g = build_user_agent_graph(events)

    ids = {n["id"] for n in g["nodes"]}
    assert ids == {"orchestrator[advanced]", "analyst[advanced]"}
    assert len(g["edges"]) == 1


def test_admin_graph_kind_app_excludes_ops_agents():
    """kind='app' filtra los agentes admin_orchestrator / observability."""
    base = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    events = [
        _evt(agent="orchestrator", ts=base),
        _evt(agent="admin_orchestrator", ts=base + timedelta(seconds=1)),
        _evt(agent="observability", ts=base + timedelta(seconds=2)),
    ]
    g = build_admin_agent_graph(events, kind="app")

    agents = {n["agent"] for n in g["nodes"]}
    assert agents == {"orchestrator"}


def test_admin_graph_kind_ops_keeps_only_ops_agents():
    """kind='ops' deja solo los agentes del chat de administración."""
    base = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    events = [
        _evt(agent="orchestrator", ts=base),
        _evt(agent="observability", ts=base + timedelta(seconds=1)),
    ]
    g = build_admin_agent_graph(events, kind="ops")

    agents = {n["agent"] for n in g["nodes"]}
    assert agents == {"observability"}
    assert all(n["is_ops"] for n in g["nodes"])


# Tests del log analyzer

def _make_log_line(level, message, ts, module="src.test"):
    """Genera una línea loguru JSONL como las que produce logging_config."""
    return json.dumps({
        "record": {
            "level": {"name": level},
            "message": message,
            "module": module.split(".")[-1],
            "name": module,
            "function": "fn",
            "line": 1,
            "time": {"timestamp": ts.timestamp()},
        },
    })


def test_log_analyzer_counts_levels(tmp_path: Path):
    """Cuenta correctamente los niveles dentro de la ventana."""
    log_file = tmp_path / "app.log"
    now = datetime.now(timezone.utc)
    log_file.write_text("\n".join([
        _make_log_line("INFO", "hola", now - timedelta(hours=1)),
        _make_log_line("WARNING", "ojo", now - timedelta(hours=2)),
        _make_log_line("ERROR", "boom", now - timedelta(hours=3)),
    ]), encoding="utf-8")

    report = analyze_log_file(log_file, window_hours=24)
    assert report.counts_by_level == {"INFO": 1, "WARNING": 1, "ERROR": 1}


def test_log_analyzer_detects_spike(tmp_path: Path):
    """Tres errores en menos de 60s en el mismo módulo → anomalía 'spike'."""
    log_file = tmp_path / "app.log"
    base = datetime.now(timezone.utc) - timedelta(hours=1)
    lines = [
        _make_log_line("ERROR", f"err {i}", base + timedelta(seconds=i * 10))
        for i in range(3)
    ]
    log_file.write_text("\n".join(lines), encoding="utf-8")

    report = analyze_log_file(log_file, window_hours=24)
    spikes = [a for a in report.anomalies if a.kind == "spike"]
    assert len(spikes) == 1

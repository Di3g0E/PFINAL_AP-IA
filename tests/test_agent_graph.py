"""
Tests del builder de grafo agéntico y del analizador de logs.

Cubre la lógica pura (sin tocar BD ni FastAPI):
  - build_user_agent_graph: nodos diferenciados por rol, fallback,
    aristas entre agentes consecutivos en una misma sesión.
  - build_admin_agent_graph: agregación system-wide, users_distinct.
  - graph_to_dot: salida estable parseable.
  - analyze_log_file: parseo de loguru JSONL, detección de anomalías
    (spike, volumen alto, mensaje repetido).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.data.schema import Event
from src.utils.agent_graph_builder import (
    build_admin_agent_graph,
    build_user_agent_graph,
    graph_to_dot,
)
from src.utils.log_analyzer import analyze_log_file


# Helpers ---------------------------------------------------------------------


def _evt(
    *, agent: str, ts: datetime, session_id=None, user_id=None,
    role=None, status="ok", latency_ms=100, action="test",
) -> Event:
    """Construye un Event sin tocar BD. SQLAlchemy permite instanciar
    objetos sin sesión adjunta, sirve para tests unitarios del builder."""
    payload = {"role": role} if role else None
    return Event(
        ts=ts, agent=agent, action=action, status=status,
        latency_ms=latency_ms, session_id=session_id, user_id=user_id,
        payload=payload,
    )


# build_user_agent_graph ------------------------------------------------------


def test_user_graph_empty_returns_empty_structure():
    g = build_user_agent_graph([], fallback_role="basic")
    assert g == {
        "nodes": [], "edges": [],
        "meta": {"total_events": 0, "total_sessions": 0,
                 "fallback_role": "basic"},
    }


def test_user_graph_node_id_includes_role_from_payload():
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
    # Una arista orchestrator → analyst
    assert g["edges"] == [{
        "source": "orchestrator[advanced]",
        "target": "analyst[advanced]",
        "count": 1, "avg_latency_ms": 100,
    }]


def test_user_graph_fallback_role_applied_when_payload_missing():
    sess = uuid.uuid4()
    base = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    events = [
        # Sin role en payload — el fallback ("basic") debería etiquetarlo.
        _evt(agent="security", ts=base, session_id=sess),
        _evt(agent="registrar", ts=base + timedelta(seconds=1),
             session_id=sess),
    ]
    g = build_user_agent_graph(events, fallback_role="basic")
    ids = {n["id"] for n in g["nodes"]}
    assert ids == {"security[basic]", "registrar[basic]"}


def test_user_graph_no_role_when_no_fallback_and_no_payload():
    sess = uuid.uuid4()
    base = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    events = [_evt(agent="api", ts=base, session_id=sess)]
    g = build_user_agent_graph(events)
    # Sin role conocido, el id no debería incluir corchetes.
    assert g["nodes"][0]["id"] == "api"
    assert g["nodes"][0]["role"] is None


def test_user_graph_error_rate_calculated():
    sess = uuid.uuid4()
    base = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    events = [
        _evt(agent="security", ts=base, session_id=sess, status="ok"),
        _evt(agent="security", ts=base + timedelta(seconds=1),
             session_id=sess, status="error"),
        _evt(agent="security", ts=base + timedelta(seconds=2),
             session_id=sess, status="ok"),
    ]
    g = build_user_agent_graph(events, fallback_role="basic")
    node = g["nodes"][0]
    assert node["count"] == 3
    assert node["error_count"] == 1
    assert node["error_rate"] == round(1 / 3, 4)


def test_user_graph_edges_only_within_same_session():
    s1, s2 = uuid.uuid4(), uuid.uuid4()
    base = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    # Sesión 1: orchestrator → analyst. Sesión 2: orchestrator → security.
    # No debe aparecer arista entre nodos de sesiones distintas.
    events = [
        _evt(agent="orchestrator", ts=base, session_id=s1, role="basic"),
        _evt(agent="analyst", ts=base + timedelta(seconds=1),
             session_id=s1, role="basic"),
        _evt(agent="orchestrator", ts=base + timedelta(seconds=2),
             session_id=s2, role="basic"),
        _evt(agent="security", ts=base + timedelta(seconds=3),
             session_id=s2, role="basic"),
    ]
    g = build_user_agent_graph(events)
    edge_keys = {(e["source"], e["target"]) for e in g["edges"]}
    assert edge_keys == {
        ("orchestrator[basic]", "analyst[basic]"),
        ("orchestrator[basic]", "security[basic]"),
    }


# build_admin_agent_graph -----------------------------------------------------


def test_admin_graph_counts_users_distinct_per_agent():
    u1, u2 = uuid.uuid4(), uuid.uuid4()
    s1, s2 = uuid.uuid4(), uuid.uuid4()
    base = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    events = [
        _evt(agent="analyst", ts=base, session_id=s1, user_id=u1),
        _evt(agent="analyst", ts=base + timedelta(seconds=1),
             session_id=s2, user_id=u2),
        _evt(agent="security", ts=base + timedelta(seconds=2),
             session_id=s2, user_id=u2),
    ]
    g = build_admin_agent_graph(events)
    nodes = {n["agent"]: n for n in g["nodes"]}
    assert nodes["analyst"]["users_distinct"] == 2
    assert nodes["security"]["users_distinct"] == 1


def test_admin_graph_does_not_label_role():
    base = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    events = [_evt(agent="orchestrator", ts=base, role="advanced")]
    g = build_admin_agent_graph(events)
    # En la vista admin no diferenciamos por rol.
    assert g["nodes"][0]["id"] == "orchestrator"
    assert g["nodes"][0]["role"] is None


# graph_to_dot ----------------------------------------------------------------


def test_graph_to_dot_emits_valid_digraph():
    g = {
        "nodes": [
            {"id": "orchestrator[basic]", "agent": "orchestrator",
             "role": "basic", "count": 5, "error_count": 0,
             "error_rate": 0.0, "avg_latency_ms": 120},
        ],
        "edges": [],
    }
    dot = graph_to_dot(g, title="test")
    assert dot.startswith("digraph")
    assert "orchestrator[basic]" in dot
    assert "count=5" in dot


# analyze_log_file ------------------------------------------------------------


def _make_log_line(level: str, message: str, ts: datetime, module: str = "src.test") -> str:
    """Genera una línea loguru JSONL como las que produce logging_config."""
    return json.dumps({
        "text": f"... | {level} | ...",
        "record": {
            "level": {"name": level, "no": 40, "icon": ""},
            "message": message,
            "module": module.split(".")[-1],
            "name": module,
            "function": "fn",
            "line": 1,
            "time": {"repr": ts.isoformat(), "timestamp": ts.timestamp()},
        },
    })


def test_log_analyzer_returns_note_when_file_missing(tmp_path: Path):
    report = analyze_log_file(tmp_path / "no-existe.log")
    assert report.note is not None
    assert "No existe" in report.note
    assert report.lines_scanned == 0


def test_log_analyzer_counts_levels_within_window(tmp_path: Path):
    log_file = tmp_path / "app.log"
    now = datetime.now(timezone.utc)
    inside = now - timedelta(hours=2)
    outside = now - timedelta(hours=48)
    log_file.write_text(
        "\n".join([
            _make_log_line("INFO", "hola", inside),
            _make_log_line("WARNING", "ojo", inside),
            _make_log_line("ERROR", "boom", inside),
            _make_log_line("ERROR", "fuera de ventana", outside),
        ]),
        encoding="utf-8",
    )
    report = analyze_log_file(log_file, window_hours=24)
    assert report.lines_scanned == 4
    assert report.counts_by_level == {"INFO": 1, "WARNING": 1, "ERROR": 1}


def test_log_analyzer_detects_spike(tmp_path: Path):
    log_file = tmp_path / "app.log"
    base = datetime.now(timezone.utc) - timedelta(hours=1)
    lines = [
        _make_log_line("ERROR", f"err {i}", base + timedelta(seconds=i * 10),
                       module="src.api.main")
        for i in range(3)  # 3 errores en 30s → spike
    ]
    log_file.write_text("\n".join(lines), encoding="utf-8")
    report = analyze_log_file(log_file, window_hours=24)
    spike_anomalies = [a for a in report.anomalies if a.kind == "spike"]
    assert len(spike_anomalies) == 1
    assert "src.api.main" in spike_anomalies[0].description


def test_log_analyzer_detects_repeated_messages(tmp_path: Path):
    log_file = tmp_path / "app.log"
    base = datetime.now(timezone.utc) - timedelta(hours=1)
    lines = [
        _make_log_line("ERROR", "mismo mensaje repetido",
                       base + timedelta(seconds=i * 30))
        for i in range(6)
    ]
    log_file.write_text("\n".join(lines), encoding="utf-8")
    report = analyze_log_file(log_file, window_hours=24)
    repeated = [a for a in report.anomalies if a.kind == "repeated"]
    assert len(repeated) >= 1
    assert repeated[0].occurrences == 6


def test_log_analyzer_skips_malformed_lines(tmp_path: Path):
    log_file = tmp_path / "app.log"
    log_file.write_text(
        "esto no es json\n{}\n" + _make_log_line(
            "INFO", "ok", datetime.now(timezone.utc),
        ),
        encoding="utf-8",
    )
    report = analyze_log_file(log_file, window_hours=24)
    # 3 líneas escaneadas, solo 1 parsea correctamente
    assert report.lines_scanned == 3
    assert report.lines_parsed == 1

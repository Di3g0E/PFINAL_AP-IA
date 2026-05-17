"""Construye un grafo agéntico a partir de la tabla `events` local.

Uso:
    .venv\Scripts\python.exe scripts\build_agent_graph.py --output artifacts/agent_graph.json --window-hours 24

Genera JSON con `nodes` y `edges` y lo escribe en disco. No requiere claves externas.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

import sys
from pathlib import Path

# Asegurar que el paquete `src` es importable cuando se ejecuta el script
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

from src.data.database import get_session
from src.data.schema import Event


def build_graph(events: list[Event]):
    # Agrupar por session_id y ordenar por timestamp
    sessions = defaultdict(list)
    for e in events:
        key = str(e.session_id) if e.session_id else f"no-session-{e.user_id}"
        sessions[key].append(e)

    nodes = defaultdict(lambda: {"count": 0})
    edges = defaultdict(lambda: {"count": 0, "latencies": []})

    for sess_id, evs in sessions.items():
        evs.sort(key=lambda x: x.ts)
        prev_agent: Optional[str] = None
        for ev in evs:
            nodes[ev.agent]["count"] += 1
            if prev_agent is not None:
                key = (prev_agent, ev.agent)
                edges[key]["count"] += 1
                if ev.latency_ms is not None:
                    edges[key]["latencies"].append(ev.latency_ms)
            prev_agent = ev.agent

    nodes_out = [
        {"agent": k, "count": v["count"]}
        for k, v in nodes.items()
    ]
    edges_out = []
    for (src, dst), v in edges.items():
        latencies = v["latencies"]
        avg_latency = int(sum(latencies) / len(latencies)) if latencies else None
        edges_out.append({"source": src, "target": dst, "count": v["count"], "avg_latency_ms": avg_latency})

    return {"nodes": nodes_out, "edges": edges_out}


def main(output: str, window_hours: Optional[int]):
    cutoff = None
    if window_hours:
        cutoff = datetime.utcnow() - timedelta(hours=window_hours)

    with get_session() as s:
        q = s.query(Event).order_by(Event.ts.asc())
        if cutoff:
            q = q.filter(Event.ts >= cutoff)
        events = q.all()

    graph = build_graph(events)
    with open(output, "w", encoding="utf8") as f:
        json.dump({"generated_at": datetime.utcnow().isoformat(), "graph": graph}, f, indent=2)
    print(f"Wrote graph to {output} (sessions={len(events)})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="artifacts/agent_graph.json")
    parser.add_argument("--window-hours", type=int, default=24, help="Ventana en horas (None -> todo)")
    args = parser.parse_args()
    main(args.output, args.window_hours)

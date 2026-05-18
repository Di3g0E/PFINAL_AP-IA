from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from src.data.schema import Event


def build_agent_graph(events: Iterable[Event]) -> dict:
    nodes = defaultdict(lambda: {"count": 0})
    edges = defaultdict(lambda: {"count": 0, "latencies": []})

    for event in events:
        nodes[event.agent]["count"] += 1

    sessions: dict[str, list[Event]] = {}
    for event in events:
        session_key = str(event.session_id) if event.session_id else f"no-session-{event.user_id}"
        sessions.setdefault(session_key, []).append(event)

    for session_events in sessions.values():
        session_events.sort(key=lambda e: e.ts)
        prev_agent = None
        for event in session_events:
            if prev_agent is not None:
                key = (prev_agent, event.agent)
                ent = edges[key]
                ent["count"] += 1
                if event.latency_ms is not None:
                    ent["latencies"].append(event.latency_ms)
            prev_agent = event.agent

    nodes_out = [{"agent": agent, "count": info["count"]} for agent, info in nodes.items()]
    edges_out = []
    for (source, target), info in edges.items():
        latencies = info["latencies"]
        avg_latency = int(sum(latencies) / len(latencies)) if latencies else None
        edges_out.append({"source": source, "target": target, "count": info["count"], "avg_latency_ms": avg_latency})

    return {"nodes": nodes_out, "edges": edges_out}

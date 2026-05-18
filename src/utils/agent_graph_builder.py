"""Constructores del grafo agéntico.

Existen DOS vistas conceptualmente distintas:

  1. **Grafo de usuario** (`build_user_agent_graph`):
     Construido únicamente desde la tabla `events` filtrada por `user_id`.
     Cada nodo se etiqueta con el rol activo (`basic` / `advanced`) que
     condiciona el estilo de respuesta de los agentes orquestador/narrador.
     El identificador del nodo es `f"{agent}[{role}]"` cuando el rol está
     presente en `event.payload["role"]`. Eso permite que un mismo usuario
     vea en su grafo cómo cambia el flujo si alterna entre rol básico y
     avanzado a lo largo de las sesiones.

  2. **Grafo de admin** (`build_admin_agent_graph`):
     System-wide. Combina la tabla `events` (todos los usuarios) con
     observaciones traídas desde Langfuse SaaS (si está configurado),
     enriqueciendo cada nodo con métricas operacionales (latencia media,
     error rate, usuarios distintos, tokens, coste).

Las dos funciones devuelven un dict serializable con la forma::

    {
      "nodes": [{"id": str, "agent": str, "role": Optional[str],
                 "count": int, ...}],
      "edges": [{"source": str, "target": str, "count": int,
                 "avg_latency_ms": Optional[int], ...}],
      "meta":  {...info adicional...}
    }

El módulo NO depende de FastAPI ni de SQLAlchemy a nivel de import — solo
necesita los modelos del schema para tipar. Eso lo hace fácil de testear y
de reutilizar desde scripts CLI.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Optional

from src.data.schema import Event


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------


def _node_id(agent: str, role: Optional[str]) -> str:
    """Identificador estable de un nodo del grafo.

    Si hay rol → `agent[role]`. Si no hay rol (eventos de capas API que no
    pasan por el orquestador) → solo `agent`. Esto evita que aparezca el
    feo `agent[None]`.
    """
    return f"{agent}[{role}]" if role else agent


def _extract_role(event: Event) -> Optional[str]:
    """Saca el rol del payload del evento si está presente.

    El payload lo escribe `Stopwatch` en `src/utils/logging_config.py` para
    los nodos `narrate_response_node` y `rephrase_node` del orquestador.
    Para eventos de otros agentes (security, registrar, analyst, api) será
    None y se renderizarán sin variante.
    """
    payload = event.payload or {}
    role = payload.get("role")
    if role in ("basic", "advanced"):
        return role
    return None


# ---------------------------------------------------------------------------
# Constructor: vista usuario
# ---------------------------------------------------------------------------


def build_user_agent_graph(
    events: Iterable[Event],
    *,
    fallback_role: Optional[str] = None,
) -> dict[str, Any]:
    """Grafo agéntico de un único usuario.

    Args:
        events: iterable de filas `Event` ordenadas o no (la función
            ordena por `ts` internamente al construir las aristas).
        fallback_role: rol actual del usuario (`basic` / `advanced`). Se
            usa cuando un evento no tiene `payload.role` registrado, lo
            cual ocurre con los agentes no-narrador. Permite que el
            grafo refleje el rol vigente aunque solo unos pocos eventos
            lo tengan explícito.

    Returns:
        Dict `{nodes, edges, meta}` apto para serializar a JSON.
    """
    events = list(events)

    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[tuple[str, str], dict[str, Any]] = {}

    # Agrupamos por sesión para construir aristas entre agentes consecutivos
    sessions: dict[str, list[Event]] = defaultdict(list)
    for ev in events:
        key = str(ev.session_id) if ev.session_id else f"no-session-{ev.user_id}"
        sessions[key].append(ev)

    for session_events in sessions.values():
        session_events.sort(key=lambda e: e.ts)
        prev_node_id: Optional[str] = None
        for ev in session_events:
            role = _extract_role(ev) or fallback_role
            nid = _node_id(ev.agent, role)

            node = nodes.setdefault(nid, {
                "id": nid, "agent": ev.agent, "role": role,
                "count": 0, "error_count": 0,
                "latencies": [],
            })
            node["count"] += 1
            if ev.status == "error":
                node["error_count"] += 1
            if ev.latency_ms is not None:
                node["latencies"].append(ev.latency_ms)

            if prev_node_id is not None:
                ekey = (prev_node_id, nid)
                edge = edges.setdefault(ekey, {
                    "source": prev_node_id, "target": nid,
                    "count": 0, "latencies": [],
                })
                edge["count"] += 1
                if ev.latency_ms is not None:
                    edge["latencies"].append(ev.latency_ms)
            prev_node_id = nid

    return {
        "nodes": [_finalize_node(n) for n in nodes.values()],
        "edges": [_finalize_edge(e) for e in edges.values()],
        "meta": {
            "total_events": len(events),
            "total_sessions": len(sessions),
            "fallback_role": fallback_role,
        },
    }


# ---------------------------------------------------------------------------
# Constructor: vista admin
# ---------------------------------------------------------------------------


def build_admin_agent_graph(events: Iterable[Event]) -> dict[str, Any]:
    """Grafo system-wide. No diferencia por rol.

    Args:
        events: eventos de TODOS los usuarios en la ventana.

    Returns:
        Dict `{nodes, edges, meta}`. La estructura es la misma que la del
        grafo de usuario, con dos diferencias semánticas:
          - Los nodos no llevan etiqueta de rol.
          - Cada nodo trae `users_distinct` (cuántos usuarios distintos
            han pasado por ese agente en la ventana).

    El enriquecimiento con métricas de Langfuse (coste, tokens, modelos)
    se hace al margen del grafo, en `meta.langfuse` del endpoint admin
    — las observaciones de Langfuse del proyecto no traen `name` por
    observación, así que mezclarlas en el grafo introducía nodos
    fantasma. Para evolucionar a futuro: instrumentar cada `@observe`
    con un `name` mapeable a un agente y reintroducir el merge aquí.
    """
    events = list(events)

    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[tuple[str, str], dict[str, Any]] = {}

    # --- 1) Eventos locales (events table) ---
    sessions: dict[str, list[Event]] = defaultdict(list)
    user_sets: dict[str, set] = defaultdict(set)
    for ev in events:
        key = str(ev.session_id) if ev.session_id else f"no-session-{ev.user_id}"
        sessions[key].append(ev)
        if ev.user_id is not None:
            user_sets[ev.agent].add(str(ev.user_id))

    for session_events in sessions.values():
        session_events.sort(key=lambda e: e.ts)
        prev_agent: Optional[str] = None
        for ev in session_events:
            nid = ev.agent
            node = nodes.setdefault(nid, {
                "id": nid, "agent": ev.agent, "role": None,
                "count": 0, "error_count": 0, "latencies": [],
                "users_distinct": 0,
            })
            node["count"] += 1
            if ev.status == "error":
                node["error_count"] += 1
            if ev.latency_ms is not None:
                node["latencies"].append(ev.latency_ms)

            if prev_agent is not None:
                ekey = (prev_agent, nid)
                edge = edges.setdefault(ekey, {
                    "source": prev_agent, "target": nid,
                    "count": 0, "latencies": [],
                })
                edge["count"] += 1
                if ev.latency_ms is not None:
                    edge["latencies"].append(ev.latency_ms)
            prev_agent = nid

    for agent_name, users in user_sets.items():
        if agent_name in nodes:
            nodes[agent_name]["users_distinct"] = len(users)

    return {
        "nodes": [_finalize_node(n) for n in nodes.values()],
        "edges": [_finalize_edge(e) for e in edges.values()],
        "meta": {
            "total_events": len(events),
            "total_sessions": len(sessions),
        },
    }


# ---------------------------------------------------------------------------
# Finalización de nodos / aristas
# ---------------------------------------------------------------------------


def _finalize_node(node: dict[str, Any]) -> dict[str, Any]:
    latencies = node.pop("latencies", [])
    avg = int(sum(latencies) / len(latencies)) if latencies else None
    count = node["count"]
    error_count = node["error_count"]
    error_rate = round(error_count / count, 4) if count else 0.0
    out = {
        "id": node["id"],
        "agent": node["agent"],
        "role": node.get("role"),
        "count": count,
        "error_count": error_count,
        "error_rate": error_rate,
        "avg_latency_ms": avg,
    }
    # Campos opcionales (vista admin)
    for k in ("tokens_in", "tokens_out", "cost_usd", "users_distinct"):
        if k in node:
            out[k] = node[k]
    if "cost_usd" in out:
        out["cost_usd"] = round(out["cost_usd"], 6)
    return out


def _finalize_edge(edge: dict[str, Any]) -> dict[str, Any]:
    latencies = edge.pop("latencies", [])
    avg = int(sum(latencies) / len(latencies)) if latencies else None
    return {
        "source": edge["source"],
        "target": edge["target"],
        "count": edge["count"],
        "avg_latency_ms": avg,
    }


# ---------------------------------------------------------------------------
# Exporters: DOT y PNG
# ---------------------------------------------------------------------------


def graph_to_dot(graph: dict[str, Any], *, title: str = "Agent Graph") -> str:
    """Renderiza el grafo a notación DOT (graphviz). Texto plano, fácil
    de incrustar en informes LaTeX o renderizar con `viz-js` en el
    frontend.
    """
    lines: list[str] = [
        f'digraph "{title}" {{',
        '  rankdir=LR;',
        '  node [shape=box, style="rounded,filled", fontname="Helvetica"];',
        '  edge [fontname="Helvetica", fontsize=10];',
    ]
    for n in graph["nodes"]:
        label_parts = [n["id"]]
        label_parts.append(f"count={n['count']}")
        if n.get("avg_latency_ms") is not None:
            label_parts.append(f"avg={n['avg_latency_ms']}ms")
        if n.get("error_rate"):
            label_parts.append(f"err={n['error_rate']*100:.1f}%")
        if n.get("cost_usd"):
            label_parts.append(f"cost=${n['cost_usd']:.4f}")
        fillcolor = _node_color(n)
        label = "\\n".join(label_parts)
        nid = n["id"].replace('"', '\\"')
        lines.append(f'  "{nid}" [label="{label}", fillcolor="{fillcolor}"];')
    for e in graph["edges"]:
        elabel_parts = [f"n={e['count']}"]
        if e.get("avg_latency_ms") is not None:
            elabel_parts.append(f"{e['avg_latency_ms']}ms")
        elabel = " · ".join(elabel_parts)
        src = e["source"].replace('"', '\\"')
        tgt = e["target"].replace('"', '\\"')
        lines.append(f'  "{src}" -> "{tgt}" [label="{elabel}"];')
    lines.append("}")
    return "\n".join(lines)


def _node_color(node: dict[str, Any]) -> str:
    err = node.get("error_rate") or 0.0
    if err >= 0.20:
        return "#fca5a5"  # rojo
    if err >= 0.05:
        return "#fde68a"  # amarillo
    role = node.get("role")
    if role == "advanced":
        return "#bfdbfe"  # azul
    if role == "basic":
        return "#bbf7d0"  # verde
    return "#e5e7eb"      # gris


def graph_to_png(graph: dict[str, Any], *, title: str = "Agent Graph") -> bytes:
    """Renderiza el grafo a PNG usando el binario graphviz.

    Levanta `RuntimeError` si el binario `dot` no está disponible en PATH.
    El llamador (router FastAPI) lo traduce a HTTP 503 con mensaje claro.
    """
    try:
        import graphviz
    except ImportError as exc:
        raise RuntimeError(
            "El paquete python `graphviz` no está instalado. "
            "Añadir `graphviz>=0.20` a requirements.txt."
        ) from exc

    dot_source = graph_to_dot(graph, title=title)
    src = graphviz.Source(dot_source, format="png")
    try:
        return src.pipe()
    except graphviz.ExecutableNotFound as exc:
        raise RuntimeError(
            "El binario `dot` (Graphviz) no está instalado en el sistema. "
            "Instala https://graphviz.org/download/ o usa el endpoint "
            "`?format=dot` para obtener el texto DOT y renderizarlo en "
            "el frontend con viz-js."
        ) from exc

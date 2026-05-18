"""Construye un grafo agéntico a partir de la tabla `events` local.

Uso:
    .venv\\Scripts\\python.exe scripts\\build_agent_graph.py \\
        --output artifacts/agent_graph.json --window-hours 24

Reutiliza `src.utils.agent_graph_builder.build_admin_agent_graph` para
no duplicar la lógica con el endpoint. Pensado para auditorías offline
o para regenerar el grafo en un cron sin levantar la API.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

from src.data.database import get_session  # noqa: E402
from src.data.schema import Event  # noqa: E402
from src.utils.agent_graph_builder import build_admin_agent_graph  # noqa: E402


def main(output: str, window_hours: Optional[int]) -> int:
    cutoff = None
    if window_hours:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)

    with get_session() as s:
        q = s.query(Event).order_by(Event.ts.asc())
        if cutoff is not None:
            q = q.filter(Event.ts >= cutoff)
        events = q.all()

    graph = build_admin_agent_graph(events)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_hours": window_hours,
        "graph": graph,
    }
    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"Wrote graph to {out_path} (events={len(events)})")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="artifacts/agent_graph.json")
    parser.add_argument("--window-hours", type=int, default=24,
                        help="Ventana en horas (0/None → todo el histórico).")
    args = parser.parse_args()
    sys.exit(main(args.output, args.window_hours or None))

"""Render del grafo agéntico real (LangGraph) a PNG.

Uso (desde PFINAL_AP-IA/):
    .venv/Scripts/python.exe scripts/render_agent_graph.py
Salida: doc/figures/agent_graph.png
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.agents.orchestrator.graph import build_graph

OUT = ROOT / "doc" / "figures" / "agent_graph.png"


def main() -> None:
    graph = build_graph()
    png_bytes = graph.get_graph().draw_mermaid_png()
    OUT.write_bytes(png_bytes)
    print(f"Escrito: {OUT}")


if __name__ == "__main__":
    main()

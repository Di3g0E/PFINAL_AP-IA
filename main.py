"""
Demo CLI del sistema multiagente P6.

Levanta el grafo LangGraph con `MemorySaver` y abre una sesión interactiva
contra el agente Orquestador. Usa el LLM compartido (Groq) leído del .env.

Uso:
    python main.py

Requisitos:
    - GROQ_API_KEY configurada en .env (https://console.groq.com)
    - data/raw/db_mod_descript.csv presente (copiado en el setup)

Salir: escribe 'salir', 'exit' o pulsa Ctrl+C.
"""

from __future__ import annotations

import sys
import uuid

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage

from src.agents.orchestrator.graph import build_graph
from src.utils.config import DEMO_USER_ID, settings
from src.utils.logging_config import configure_logging


BANNER = r"""
============================================================
  P6_AP-IA — Sistema multiagente financiero (demo)
  Agentes activos: Orchestrator + Analyst + Registrar
                   + Security (anti-anomalías)
  Pendiente (v2): Security biométrico (face login)
============================================================
"""

EXIT_WORDS = {"salir", "exit", "quit", "q"}


def main() -> int:
    load_dotenv()
    configure_logging()

    print(BANNER)

    if not settings.groq_api_key:
        print("[ERROR] GROQ_API_KEY no configurada en .env. ")
        print("  → Crea cuenta gratuita en https://console.groq.com y añádela al .env.")
        return 1

    graph = build_graph()
    user_id = DEMO_USER_ID            # UUID fijo del usuario demo (creado por scripts/init_db.py)
    session_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": f"{user_id}:{session_id}"}}

    print(f"Sesión: {session_id[:8]}…  |  modelo: {settings.groq_default_model}\n")
    print("Escribe tu consulta (ej: 'resume mis gastos del último mes'). 'salir' para terminar.\n")

    try:
        while True:
            try:
                user_input = input(">>> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if not user_input:
                continue
            if user_input.lower() in EXIT_WORDS:
                break

            try:
                final = graph.invoke(
                    {
                        "messages": [HumanMessage(content=user_input)],
                        "user_id": user_id,
                        "session_id": session_id,
                        # Reset por turno: limpia slots del turno anterior y
                        # contador de iteraciones.
                        "iterations": 0,
                        "analysis_report": None,
                        "security_verdict": None,
                        "registry_result": None,
                        "pending_action": None,
                        "last_decision": None,
                    },
                    config=config,
                )
            except Exception as e:
                print(f"[ERROR] {type(e).__name__}: {e}")
                continue

            last_msg = final["messages"][-1]
            print(f"\n{last_msg.content}\n")
    except KeyboardInterrupt:
        print()

    print("Hasta luego.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

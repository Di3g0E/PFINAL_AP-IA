"""Agente Observability — orquestador ReAct para el chat de admin.

Diseño:
  - Reutiliza `langgraph.prebuilt.create_react_agent` (loop estándar
    LLM → tool → LLM → … → final). No reinventamos el flujo, solo
    aportamos las tools y el system prompt.
  - La elección del LLM es la misma que para el chat de usuario
    (`get_llm`) — heredas el provider del propio admin si lo configuró
    en `user_settings`, o cae al Groq compartido.
  - El conjunto de tools es **disjunto** del orquestador financiero. El
    admin no puede ejecutar funciones de los usuarios (delegate_analyst,
    delegate_registrar, etc.) — su agente solo lee telemetría.
  - Eventos: la propia ejecución del grafo se envuelve en
    `Stopwatch(agent="admin_orchestrator", action="reply")` desde el
    router. Cada tool a su vez registra `agent="observability"`. Eso da
    al grafo de ops una forma natural (admin_orchestrator ↔ observability).
"""
from __future__ import annotations

from typing import Optional

from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

from src.agents.observability.prompts import ADMIN_OPS_SYSTEM_PROMPT
from src.agents.observability.tools import OBSERVABILITY_TOOLS


def build_observability_graph(
    llm: BaseChatModel,
    *,
    checkpointer: Optional[BaseCheckpointSaver] = None,
):
    """Construye el grafo del agente Observability listo para `.invoke()`.

    Args:
        llm: cliente de chat (resultado de `get_llm(user_id=admin_id)`).
        checkpointer: si se pasa, el grafo persiste el estado por
            `thread_id` (memoria conversacional). Por defecto usa
            `MemorySaver` en proceso — suficiente para el chat de ops.

    Returns:
        `CompiledStateGraph` con la interfaz estándar de LangGraph
        (`.invoke({"messages": [...]}, config={"configurable": {"thread_id": "..."}})`).
    """
    return create_react_agent(
        model=llm,
        tools=OBSERVABILITY_TOOLS,
        prompt=ADMIN_OPS_SYSTEM_PROMPT,
        checkpointer=checkpointer or MemorySaver(),
        name="admin_observability",
    )

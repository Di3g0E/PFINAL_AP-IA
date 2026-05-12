"""
Estado raíz del grafo LangGraph.

  - `messages` (lista anotada con `add_messages`): historia de la conversación.
  - Slots por sub-agente: cada uno escribe SU resultado, los demás los leen.
  - `last_decision`: lo que el LLM router decidió en su última iteración.
  - `iterations`: contador para limitar el bucle Orquestador↔Subagente.

El estado se persiste por `PostgresSaver` (en runtime) o `MemorySaver`
(en tests/demos), siempre con `thread_id = f"{user_id}:{session_id}"`.
"""

from __future__ import annotations

from typing import Annotated, Optional, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from src.agents.contracts import (
    AnalysisReport, OrchestratorDecision, PendingAction,
    RegistryResult, SecurityVerdict,
)


class OrchestratorState(TypedDict, total=False):
    # Identidad
    user_id: str
    session_id: str

    # Conversación
    messages: Annotated[list[BaseMessage], add_messages]

    # Slots de los sub-agentes (cada uno escribe el suyo)
    pending_action: Optional[PendingAction]
    security_verdict: Optional[SecurityVerdict]
    registry_result: Optional[RegistryResult]
    analysis_report: Optional[AnalysisReport]

    # Routing
    last_decision: Optional[OrchestratorDecision]
    iterations: int


MAX_ITERATIONS = 6
"""Máximo de saltos Orchestrator↔Subagente por turno antes de forzar respuesta."""

"""
Construcción del grafo LangGraph del sistema multiagente.

Topología:

    START → orchestrator
                 │
            (routing condicional según `last_decision.action`)
                 ├── delegate_analyst   → analyst   ─→ orchestrator
                 ├── delegate_security  → security  ─→ orchestrator
                 ├── delegate_registrar → registrar ─→ orchestrator
                 ├── ask_user           → END
                 └── respond_final      → END

Persistencia:
  - `build_graph()` por defecto usa `MemorySaver` (sin BD, ideal para demos/tests).
  - `build_graph(checkpointer=PostgresSaver(...))` para producción multiusuario.

Uso:
    graph = build_graph()
    config = {"configurable": {"thread_id": f"{user_id}:{session_id}"}}
    final_state = graph.invoke(
        {"messages": [HumanMessage("Resume mis gastos del último mes")],
         "user_id": user_id, "session_id": session_id, "iterations": 0},
        config=config,
    )
"""

from __future__ import annotations

from typing import Any, Optional

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph

from src.agents.contracts import (
    AnalysisReport, DataPoint, GoalAlert, OrchestratorDecision,
    PendingAction, RegistryResult, SecurityVerdict,
)
from src.agents.orchestrator.nodes import (
    analyst_node, conversational_node, orchestrator_node,
    registrar_node, security_node,
)
from src.agents.orchestrator.state import OrchestratorState


# Tipos Pydantic personalizados que viajan en el estado del grafo. Se registran
# explícitamente en el serializer para silenciar el warning "Deserializing
# unregistered type ... from checkpoint" sin desactivar la verificación.
_ALLOWED_CHECKPOINT_TYPES = [
    OrchestratorDecision, AnalysisReport, SecurityVerdict, RegistryResult,
    PendingAction, GoalAlert, DataPoint,
]


def _route_after_orchestrator(state: OrchestratorState) -> str:
    """Decide el siguiente nodo a partir de `last_decision.action`."""
    decision = state.get("last_decision")
    if decision is None:
        return END

    action = decision.action
    if action == "delegate_analyst":
        return "analyst"
    if action == "delegate_security":
        return "security"
    if action == "delegate_registrar":
        return "registrar"
    if action == "delegate_conversational":
        return "conversational"
    return END  # ask_user o respond_final


def build_graph(checkpointer: Optional[BaseCheckpointSaver] = None) -> Any:
    """Construye y compila el grafo. `checkpointer=None` → `MemorySaver`."""
    builder = StateGraph(OrchestratorState)

    builder.add_node("orchestrator", orchestrator_node)
    builder.add_node("analyst", analyst_node)
    builder.add_node("security", security_node)
    builder.add_node("registrar", registrar_node)
    builder.add_node("conversational", conversational_node)

    builder.add_edge(START, "orchestrator")
    builder.add_conditional_edges(
        "orchestrator",
        _route_after_orchestrator,
        {
            "analyst": "analyst",
            "security": "security",
            "registrar": "registrar",
            "conversational": "conversational",
            END: END,
        },
    )
    # Tras Analyst/Security/Registrar, vuelta al orquestador para narrar/encadenar.
    builder.add_edge("analyst", "orchestrator")
    builder.add_edge("security", "orchestrator")
    builder.add_edge("registrar", "orchestrator")
    # Conversational produce el texto final por sí mismo y termina la iteración:
    # va directo a END en vez de volver al orquestador (no hay datos que narrar).
    builder.add_edge("conversational", END)

    if checkpointer is None:
        serde = JsonPlusSerializer(allowed_msgpack_modules=_ALLOWED_CHECKPOINT_TYPES)
        checkpointer = MemorySaver(serde=serde)

    return builder.compile(checkpointer=checkpointer)

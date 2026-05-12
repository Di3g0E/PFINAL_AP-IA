"""
Endpoint principal de conversación con el orquestador multiagente.

`POST /chat` recibe un mensaje, lo enruta al grafo LangGraph del usuario y
devuelve la respuesta narrada del Orquestador. Cada usuario tiene su propio
`thread_id` (`user_id:session_id`) en el `MemorySaver` del grafo, así que las
conversaciones quedan aisladas.
"""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from langchain_core.messages import HumanMessage
from loguru import logger
from pydantic import BaseModel, Field

from src.agents.orchestrator.graph import build_graph
from src.api.dependencies import get_current_user_id


router = APIRouter(prefix="/chat", tags=["chat"])


# Singleton del grafo: evita reconstruirlo en cada request (PostgresSaver
# o MemorySaver mantienen el estado entre invocaciones por thread_id).
_GRAPH = None


def _get_graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_graph()
    return _GRAPH


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    session_id: Optional[str] = Field(
        None,
        description=("Identificador de la sesión de chat. Si se omite se genera "
                     "uno nuevo. Manda el mismo en peticiones siguientes para "
                     "mantener el contexto."),
    )


class ChatResponse(BaseModel):
    response: str
    session_id: str
    last_action: Optional[str] = Field(
        None,
        description=("Última `OrchestratorDecision.action` ejecutada: útil para que "
                     "el cliente sepa qué sub-agente se invocó "
                     "(`delegate_analyst`, `delegate_registrar`, etc.)."),
    )


@router.post("", response_model=ChatResponse, summary="Enviar mensaje al orquestador")
def chat(
    req: ChatRequest,
    user_id: str = Depends(get_current_user_id),
) -> ChatResponse:
    session_id = req.session_id or str(uuid.uuid4())
    config = {"configurable": {"thread_id": f"{user_id}:{session_id}"}}

    try:
        final = _get_graph().invoke(
            {
                "messages": [HumanMessage(content=req.message)],
                "user_id": user_id,
                "session_id": session_id,
                # Reset de slots por turno: evita que el narrador use datos
                # del turno anterior cuando la nueva pregunta no los necesita.
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
        logger.exception(f"chat invoke falló: {e}")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Error en el grafo: {type(e).__name__}",
        ) from e

    last_msg = final["messages"][-1]
    decision = final.get("last_decision")
    text = last_msg.content if hasattr(last_msg, "content") else str(last_msg)

    return ChatResponse(
        response=text,
        session_id=session_id,
        last_action=decision.action if decision else None,
    )

"""Chat de operaciones para administradores.

Endpoints (todos exigen `is_admin=True` via `require_admin`):

    POST   /admin/chat/sessions                 — crea sesión
    GET    /admin/chat/sessions                 — lista sesiones del admin
    GET    /admin/chat/sessions/{id}/messages   — historial
    POST   /admin/chat                          — envía mensaje y recibe respuesta
    DELETE /admin/chat/sessions/{id}            — borra sesión

Persistencia: reutiliza las tablas `chat_sessions` / `chat_messages`. Como un
admin nunca entra al `/chat` financiero (la UI no lo expone), sus sesiones
son todas de ops por naturaleza; no necesitamos columna discriminadora.

Agente: `build_observability_graph(llm)` con `MemorySaver`. El `thread_id`
del checkpointer es `admin:{session.id}` — así si un día un admin tuviera
sesiones financieras (uso indebido), no colisionarían.

Eventos: la propia ejecución del grafo se envuelve en
`Stopwatch(agent="admin_orchestrator", action="reply")`, y cada tool del
sub-agente Observability emite `agent="observability"`. Los grafos del
admin (`/admin/agent-graph?kind=ops`) lo reflejan automáticamente.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from langchain_core.messages import AIMessage, HumanMessage
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from src.agents.observability import build_observability_graph
from src.agents.orchestrator.llm_factory import get_llm
from src.api.dependencies import require_admin
from src.data.database import get_db
from src.data.schema import ChatMessage, ChatSession
from src.utils.langfuse_integration import start_observation
from src.utils.logging_config import Stopwatch


router = APIRouter(prefix="/admin/chat", tags=["admin"])

# Cuántos turnos del historial reenviamos al grafo en cada request. Para el
# chat de ops no necesitamos summary rodante — las preguntas son cortas y
# autocontenidas. Mantenemos 20 turnos como ventana razonable.
RECENT_MESSAGES_LIMIT = 20

# Singleton del grafo: reusamos checkpointer y LLM compartido (Groq del
# servidor) para todos los admins. El thread_id ya separa los hilos por
# sesión, así que el singleton es seguro.
_GRAPH = None


def _get_graph():
    global _GRAPH
    if _GRAPH is None:
        # `user_id=None` fuerza Groq compartido. Si en el futuro queremos
        # que el LLM lo configure el propio admin via /settings, pasar el
        # user_id aquí — el llm_factory ya soporta lookup en BD.
        _GRAPH = build_observability_graph(get_llm(user_id=None))
    return _GRAPH


# ----------------------------- Schemas Pydantic ------------------------------


class CreateSessionResponse(BaseModel):
    session_id: str
    title: str
    created_at: datetime


class SessionListItem(BaseModel):
    id: str
    title: str
    created_at: datetime
    last_message_at: datetime
    message_count: int


class MessageOut(BaseModel):
    role: str
    content: str
    created_at: datetime
    sequence: int


class AdminChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    session_id: Optional[str] = Field(
        None,
        description="Si se omite se crea una nueva sesión.",
    )


class AdminChatResponse(BaseModel):
    response: str
    session_id: str
    tools_used: list[str] = Field(
        default_factory=list,
        description="Nombres de las tools que el sub-agente Observability "
                    "invocó para producir esta respuesta.",
    )


# --------------------------------- Helpers -----------------------------------


def _get_or_create_session(
    db: Session, user_id: str, session_id_str: Optional[str],
) -> ChatSession:
    uid = uuid.UUID(user_id)
    if session_id_str:
        try:
            sid = uuid.UUID(session_id_str)
        except (ValueError, TypeError):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "session_id inválido (no UUID)",
            )
        sess = db.execute(
            select(ChatSession).where(
                ChatSession.id == sid, ChatSession.user_id == uid,
            ),
        ).scalar_one_or_none()
        if sess is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Sesión no encontrada")
        return sess

    sess = ChatSession(user_id=uid, title="Nueva conversación de ops")
    db.add(sess)
    db.flush()
    return sess


def _persist_message(db: Session, session: ChatSession, role: str, content: str) -> None:
    next_seq = (db.execute(
        select(func.coalesce(func.max(ChatMessage.sequence), 0))
        .where(ChatMessage.session_id == session.id),
    ).scalar_one() or 0) + 1
    db.add(ChatMessage(
        session_id=session.id, role=role, content=content, sequence=next_seq,
    ))
    session.last_message_at = datetime.now(timezone.utc)
    db.flush()


def _autotitle_if_first(session: ChatSession, message: str) -> None:
    if session.title == "Nueva conversación de ops":
        # Coge las 6 primeras palabras del primer mensaje. Suficiente como
        # encabezado en la lista; el admin puede renombrarla a mano si quiere.
        words = message.strip().split()
        session.title = "[ops] " + " ".join(words[:6]) + ("…" if len(words) > 6 else "")


def _load_history_for_graph(db: Session, session: ChatSession, max_turns: int):
    """Devuelve los últimos `max_turns` pares (user, assistant) como BaseMessage."""
    rows = db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session.id)
        .order_by(ChatMessage.sequence.desc())
        .limit(max_turns * 2),
    ).scalars().all()
    rows = list(reversed(rows))  # cronológico
    out = []
    for m in rows:
        if m.role == "user":
            out.append(HumanMessage(content=m.content))
        elif m.role == "assistant":
            out.append(AIMessage(content=m.content))
        # mensajes 'system' los ignoramos: el system prompt lo aplica el agente
    return out


# --------------------------------- Endpoints ---------------------------------


@router.post(
    "/sessions",
    response_model=CreateSessionResponse,
    summary="Crea una sesión de chat de ops vacía.",
)
def create_session(
    user_id: str = Depends(require_admin),
    db: Session = Depends(get_db),
) -> CreateSessionResponse:
    sess = ChatSession(
        user_id=uuid.UUID(user_id),
        title="Nueva conversación de ops",
    )
    db.add(sess)
    db.commit()
    db.refresh(sess)
    return CreateSessionResponse(
        session_id=str(sess.id), title=sess.title, created_at=sess.created_at,
    )


@router.get(
    "/sessions",
    response_model=list[SessionListItem],
    summary="Lista las sesiones de chat de ops del admin actual.",
)
def list_sessions(
    user_id: str = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[SessionListItem]:
    rows = db.execute(
        select(
            ChatSession,
            func.count(ChatMessage.id).label("msg_count"),
        )
        .outerjoin(ChatMessage, ChatMessage.session_id == ChatSession.id)
        .where(ChatSession.user_id == uuid.UUID(user_id))
        .where(ChatSession.archived.is_(False))
        .group_by(ChatSession.id)
        .order_by(desc(ChatSession.last_message_at)),
    ).all()
    return [
        SessionListItem(
            id=str(s.id), title=s.title,
            created_at=s.created_at, last_message_at=s.last_message_at,
            message_count=int(cnt),
        )
        for s, cnt in rows
    ]


@router.get(
    "/sessions/{session_id}/messages",
    response_model=list[MessageOut],
    summary="Devuelve los últimos N mensajes de la sesión indicada.",
)
def get_messages(
    session_id: str,
    user_id: str = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[MessageOut]:
    try:
        sid = uuid.UUID(session_id)
    except (ValueError, TypeError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "session_id inválido")
    # Comprueba que la sesión existe Y es del admin actual
    sess = db.execute(
        select(ChatSession).where(
            ChatSession.id == sid, ChatSession.user_id == uuid.UUID(user_id),
        ),
    ).scalar_one_or_none()
    if sess is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sesión no encontrada")

    rows = db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == sid)
        .order_by(ChatMessage.sequence.asc()),
    ).scalars().all()
    # Filtramos mensajes vacíos (un assistant message vacío del LLM rompe el
    # render del frontend; preferimos ocultarlos antes que mostrar burbujas
    # en blanco).
    return [
        MessageOut(role=m.role, content=m.content,
                   created_at=datetime.now(timezone.utc), sequence=m.sequence)
        for m in rows if m.content
    ]


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Borra una sesión de chat de ops y sus mensajes (cascade).",
)
def delete_session(
    session_id: str,
    user_id: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    try:
        sid = uuid.UUID(session_id)
    except (ValueError, TypeError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "session_id inválido")
    sess = db.execute(
        select(ChatSession).where(
            ChatSession.id == sid, ChatSession.user_id == uuid.UUID(user_id),
        ),
    ).scalar_one_or_none()
    if sess is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sesión no encontrada")
    db.delete(sess)
    db.commit()


@router.post(
    "",
    response_model=AdminChatResponse,
    summary="Envía un mensaje al agente Observability.",
)
def admin_chat(
    req: AdminChatRequest,
    user_id: str = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminChatResponse:
    """Pipeline:
      1. Resuelve/crea sesión.
      2. Persiste el mensaje del usuario.
      3. Auto-titula si es el primer turno.
      4. Carga últimos N turnos y se los pasa al grafo Observability.
      5. Persiste la respuesta del asistente.
      6. Devuelve respuesta + tools que se hayan invocado en este turno.
    """
    sess = _get_or_create_session(db, user_id, req.session_id)
    is_first_turn = sess.title == "Nueva conversación de ops"

    _persist_message(db, sess, "user", req.message)
    if is_first_turn:
        _autotitle_if_first(sess, req.message)

    history = _load_history_for_graph(db, sess, RECENT_MESSAGES_LIMIT)

    graph = _get_graph()
    response_text = ""
    tools_used: list[str] = []

    with Stopwatch(agent="admin_orchestrator", action="reply",
                   user_id=user_id, session_id=str(sess.id)) as sw:
        with start_observation(
            name="admin_chat.request",
            as_type="span",
            input={"message": req.message, "session_id": str(sess.id)},
            user_id=user_id,
            session_id=str(sess.id),
        ):
            try:
                state = graph.invoke(
                    {"messages": history},
                    config={
                        "configurable": {"thread_id": f"admin:{sess.id}"},
                    },
                )
                # Último AIMessage = respuesta final. Tool calls intermedios
                # los recolectamos para devolvérselos al frontend.
                for msg in state["messages"]:
                    if hasattr(msg, "tool_calls") and msg.tool_calls:
                        for tc in msg.tool_calls:
                            name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
                            if name:
                                tools_used.append(name)
                last = state["messages"][-1]
                response_text = (
                    last.content if hasattr(last, "content") else str(last)
                )
                if isinstance(response_text, list):
                    # Algunos providers devuelven content como lista de chunks
                    response_text = "".join(
                        c.get("text", "") if isinstance(c, dict) else str(c)
                        for c in response_text
                    )
                response_text = (response_text or "").strip()
            except Exception as e:
                sw.payload["error"] = f"{type(e).__name__}: {e}"
                logger.exception(f"admin_chat falló para session {sess.id}: {e}")
                response_text = (
                    "Se produjo un error al consultar las herramientas de "
                    f"observabilidad ({type(e).__name__}). Revisa los logs."
                )
        sw.payload["tools"] = tools_used
        sw.payload["chars"] = len(response_text)

    _persist_message(db, sess, "assistant", response_text or "(sin contenido)")
    db.commit()

    return AdminChatResponse(
        response=response_text or "(sin contenido)",
        session_id=str(sess.id),
        tools_used=tools_used,
    )

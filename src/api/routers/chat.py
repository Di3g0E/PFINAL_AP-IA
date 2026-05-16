"""
Endpoint principal de conversación con el orquestador multiagente + gestión
de sesiones persistentes.

`POST /chat` recibe un mensaje, lo enruta al grafo LangGraph del usuario y
devuelve la respuesta narrada del Orquestador. Cada usuario tiene su propio
`thread_id` (`user_id:session_id`) en el `MemorySaver` del grafo.

Sesiones (Fase 1 de persistencia conversacional):
  - POST   /chat/sessions       — crea sesión vacía.
  - GET    /chat/sessions       — lista sesiones del usuario.
  - GET    /chat/sessions/{id}  — sesión + últimos K mensajes + summary.
  - PATCH  /chat/sessions/{id}  — rename / archive.
  - DELETE /chat/sessions/{id}  — borra (cascada a mensajes).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from src.agents.contracts import AnalysisReport
from src.agents.orchestrator.graph import build_graph
from src.agents.orchestrator.llm_factory import get_llm
from src.api.dependencies import get_current_user_id
from src.data.database import get_db
from src.data.schema import ChatMessage, ChatSession, User
from src.utils.langfuse_integration import propagate_user_context, start_observation


router = APIRouter(prefix="/chat", tags=["chat"])

# Horizonte de mensajes a devolver al rehidratar. Mensajes más antiguos
# se condensan en `ChatSession.summary` (cf. C.3).
RECENT_MESSAGES_LIMIT = 20

# Cuántos mensajes adicionales (por encima del horizonte) se acumulan antes
# de regenerar el resumen. Cuanto más alto, menos llamadas LLM pero más
# tokens en la ventana de contexto del orquestador.
SUMMARY_BATCH_SIZE = 8

# Prompt corto para la generación del resumen rodante. Estructurado en
# ASPECCT (Audiencia, Style, Propósito, Especificidad, Contexto, Constraints,
# Tono) — se cumple así parte del requisito de Fase 7 ya en Fase 1.
_SUMMARY_PROMPT = """[A] Audiencia: el orquestador multiagente del sistema, que retomará la conversación.
[S] Estilo: telegráfico, en tercera persona, máximo 5 frases.
[P] Propósito: condensar la conversación previa para que el agente recuerde decisiones, datos clave y acciones pendientes.
[E] Especificidad: incluye importes, fechas, categorías y resultados concretos si aparecen.
[C] Contexto: resumen previo (puede estar vacío) + nuevos mensajes a integrar.
[C] Constraints: NO inventes datos; si una info no aparece en el texto, ignórala. Devuelve solo el resumen actualizado, sin preámbulos.
[T] Tono: neutral, factual, sin opiniones.

Resumen previo:
{previous_summary}

Mensajes a integrar:
{new_block}

Resumen actualizado:"""


# Helpers de persistencia del chat

def _get_or_create_session(
    db: Session, user_id: str, session_id: Optional[str],
) -> ChatSession:
    """Devuelve la sesión indicada o crea una nueva si `session_id` es None
    o no pertenece al usuario."""
    try:
        uid = uuid.UUID(user_id)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "user_id inválido") from e

    if session_id:
        try:
            sid = uuid.UUID(session_id)
        except ValueError:
            sid = None
        if sid is not None:
            row = db.execute(
                select(ChatSession).where(
                    ChatSession.id == sid, ChatSession.user_id == uid,
                )
            ).scalar_one_or_none()
            if row is not None:
                return row
            # session_id existe pero no es del usuario: tratamos como nueva sesión.
            logger.warning(f"session_id {session_id} no pertenece a {user_id}; creando nueva")

    row = ChatSession(user_id=uid, title="Nueva conversación")
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


def _next_sequence(db: Session, session_id: uuid.UUID) -> int:
    """Siguiente número de orden para `chat_messages` dentro de una sesión."""
    current_max = db.execute(
        select(func.max(ChatMessage.sequence)).where(ChatMessage.session_id == session_id)
    ).scalar_one()
    return (current_max or 0) + 1


def _persist_message(
    db: Session, session: ChatSession, role: str, content: str,
    action: Optional[str] = None, chart: Optional[dict[str, Any]] = None,
) -> ChatMessage:
    """Inserta un mensaje y actualiza `last_message_at`.

    Si la respuesta del asistente incluyó un gráfico, se persiste el spec
    íntegro en la columna `chart` (JSON). Así al rehidratar la sesión los
    diagramas vuelven a aparecer sin tener que recalcular el análisis.
    """
    seq = _next_sequence(db, session.id)
    msg = ChatMessage(
        session_id=session.id, role=role, content=content,
        action=action, sequence=seq, chart=chart,
    )
    db.add(msg)
    session.last_message_at = datetime.now(timezone.utc)
    db.flush()
    return msg


# Generación de gráfico (Fase 4 — diagramas dinámicos + XAI)

# Tipo de gráfico por defecto en función del `AnalysisReport.type`. Se
# sobreescribe si el usuario pide explícitamente otro tipo (chart_type
# en target_args del router).
_DEFAULT_CHART_TYPE: dict[str, str] = {
    "trend": "line",
    "category": "bar",
    "savings_rate": "line",
    "prediction": "line",
    "recurring": "bar",
    "summary": "bar",
    "anomaly": "bar",
    "goal_status": "bar",
}


def _chart_title(report: AnalysisReport) -> str:
    """Devuelve un título corto y humano para el gráfico."""
    titles = {
        "trend": "Tendencia mensual del gasto",
        "category": f"Gasto por categoría{(' · ' + report.period) if report.period else ''}",
        "savings_rate": "Tasa de ahorro mensual",
        "prediction": "Predicción del próximo mes",
        "recurring": "Gastos recurrentes detectados",
        "summary": f"Resumen{(' · ' + report.period) if report.period else ''}",
        "anomaly": "Anomalías detectadas",
    }
    return titles.get(report.type, "Visualización")


_CHART_TYPE_ES = {
    "line": "líneas",
    "bar": "barras",
    "pie": "sectores (pie)",
    "area": "áreas",
}


def _build_xai_explanation(report: AnalysisReport, chart_type: str) -> str:
    """Genera la explicación (XAI) que acompaña al gráfico.

    Plantilla basada en `report.type`: enuncia QUÉ representa el gráfico y
    destaca el dato clave (máximo, último valor, variación, etc.) para que
    el usuario interprete lo que ve sin tener que inferirlo.
    """
    if not report.series:
        return ""

    series = report.series
    chart_es = _CHART_TYPE_ES.get(chart_type, chart_type)
    if report.type == "trend":
        first = series[0].value
        last = series[-1].value
        delta = last - first
        if abs(delta) < 1:
            tendency = "estable"
        elif delta > 0:
            tendency = f"al alza (+{delta:.0f}€ en {len(series)} meses)"
        else:
            tendency = f"a la baja ({delta:.0f}€ en {len(series)} meses)"
        return (
            f"El gráfico de {chart_es} muestra la evolución mensual del gasto. "
            f"La tendencia es {tendency}."
        )
    if report.type == "category":
        top = max(series, key=lambda p: p.value)
        total = sum(p.value for p in series)
        pct = (top.value / total * 100) if total else 0
        return (
            f"Distribución del gasto por categoría. '{top.label}' es la categoría "
            f"con mayor gasto: {top.value:.0f}€ ({pct:.0f}% del total)."
        )
    if report.type == "savings_rate":
        last = series[-1].value
        return (
            f"Tasa de ahorro mensual (ingresos − gastos) / ingresos. "
            f"Último valor: {last * 100:.1f}%."
        )
    if report.type == "prediction":
        last = series[-1].value
        predicted = report.metrics.get("predicted_amount")
        if predicted is not None:
            return (
                f"Histórico mensual + predicción ({predicted:.0f}€). "
                f"Compara la barra/punto final con los meses previos para juzgar la previsión."
            )
        return f"Histórico de los últimos {len(series)} meses como base para la predicción."
    if report.type == "recurring":
        return (
            f"Se detectaron {len(series)} gastos recurrentes (suscripciones, "
            f"facturas...). Las barras más altas son los recurrentes que más pesan."
        )
    return f"Visualización de los datos del análisis ({len(series)} puntos)."


def _build_chart_spec(report: Optional[AnalysisReport]) -> Optional[dict[str, Any]]:
    """Construye un `ChartSpec` desde un `AnalysisReport`. None si no aplica.

    Reglas:
      - `report` None o sin `series` → sin gráfico.
      - `report.chart_type == 'none'` → usuario pidió quitar gráfico → None.
      - En otro caso, usa `report.chart_type` si está, o el default según
        `report.type`.
    """
    if report is None or not report.series:
        return None
    if report.chart_type == "none":
        return None

    chart_type = report.chart_type or _DEFAULT_CHART_TYPE.get(report.type, "bar")
    data = [{"label": p.label, "value": float(p.value)} for p in report.series]
    return {
        "type": chart_type,
        "title": _chart_title(report),
        "data": data,
        "explanation": _build_xai_explanation(report, chart_type),
    }


def _load_user_role(db: Session, user_id: str) -> str:
    """Lee `users.role` para inyectarlo en el estado del grafo.

    Si la fila no existe o el rol está vacío, default 'basic' (más seguro:
    respuestas más cortas y sin tecnicismos).
    """
    try:
        uid = uuid.UUID(user_id)
    except (TypeError, ValueError):
        return "basic"
    row = db.execute(select(User).where(User.id == uid)).scalar_one_or_none()
    if row is None:
        return "basic"
    return row.role or "basic"


def _autotitle_if_first(session: ChatSession, first_user_message: str) -> None:
    """Cuando se crea la sesión, su título es 'Nueva conversación'. La primera
    vez que el usuario manda un mensaje, lo usamos como título (recortado)."""
    if session.title == "Nueva conversación":
        cleaned = first_user_message.strip().replace("\n", " ")
        session.title = (cleaned[:60] + "…") if len(cleaned) > 60 else cleaned or "Nueva conversación"


def _build_prompt_messages(
    db: Session, session: ChatSession,
) -> list[BaseMessage]:
    """Reconstruye el contexto conversacional a partir de `chat_messages` + `summary`.

    Como damos un `thread_id` único por turno al grafo (sin acumulación), aquí
    es donde inyectamos toda la historia visible: summary como SystemMessage +
    últimos K mensajes en orden cronológico.
    """
    prompt: list[BaseMessage] = []
    if session.summary:
        prompt.append(SystemMessage(
            content=f"Resumen de la conversación previa:\n{session.summary}"
        ))

    recent = db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session.id)
        .order_by(desc(ChatMessage.sequence))
        .limit(RECENT_MESSAGES_LIMIT)
    ).scalars().all()
    recent.reverse()

    for m in recent:
        if m.role == "user":
            prompt.append(HumanMessage(content=m.content))
        elif m.role == "assistant":
            prompt.append(AIMessage(content=m.content))
        else:
            prompt.append(SystemMessage(content=m.content))
    return prompt


def _maybe_summarize(db: Session, session: ChatSession, user_id: str) -> None:
    """Si la sesión excede el horizonte + lote, regenera summary y poda mensajes.

    Disparo: total_messages > RECENT_MESSAGES_LIMIT + SUMMARY_BATCH_SIZE.
    Procedimiento:
      1. Toma los SUMMARY_BATCH_SIZE mensajes más antiguos.
      2. Pide al LLM un summary actualizado que mezcle el previo + el bloque.
      3. Persiste el nuevo summary en `session.summary`.
      4. Borra esos mensajes de `chat_messages` (su info queda en summary).
    """
    total = db.execute(
        select(func.count(ChatMessage.id)).where(ChatMessage.session_id == session.id)
    ).scalar_one()

    if total <= RECENT_MESSAGES_LIMIT + SUMMARY_BATCH_SIZE:
        return

    oldest = db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session.id)
        .order_by(ChatMessage.sequence.asc())
        .limit(SUMMARY_BATCH_SIZE)
    ).scalars().all()

    new_block = "\n".join(
        f"[{m.role}] {m.content}" for m in oldest
    )
    prompt = _SUMMARY_PROMPT.format(
        previous_summary=session.summary or "(sin resumen previo)",
        new_block=new_block,
    )

    with start_observation(
        name="chat.summarize",
        as_type="span",
        input={"summary_length": len(new_block), "session_id": str(session.id)},
        user_id=user_id,
        session_id=str(session.id),
    ):
        try:
            llm = get_llm(user_id=user_id)
            response = llm.invoke(prompt)
            new_summary = response.content if hasattr(response, "content") else str(response)
            new_summary = new_summary.strip()
        except Exception as e:
            logger.warning(f"_maybe_summarize: LLM falló, conservando estado: {e}")
            return

    if not new_summary:
        logger.warning("_maybe_summarize: el LLM devolvió cadena vacía; saltando")
        return

    session.summary = new_summary[:4000]  # límite defensivo (Text es ilimitado pero...)
    for m in oldest:
        db.delete(m)
    db.flush()
    logger.info(
        f"Summary regenerado para session {session.id} "
        f"({len(oldest)} mensajes condensados; {len(session.summary)} chars)"
    )


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


class ChartSpecOut(BaseModel):
    """Spec de gráfico que acompaña a la respuesta. Pydantic-friendly."""
    type: str
    title: str
    data: list[dict[str, Any]] = []
    explanation: str = ""


class ChatResponse(BaseModel):
    response: str
    session_id: str
    last_action: Optional[str] = Field(
        None,
        description=("Última `OrchestratorDecision.action` ejecutada: útil para que "
                     "el cliente sepa qué sub-agente se invocó "
                     "(`delegate_analyst`, `delegate_registrar`, etc.)."),
    )
    chart: Optional[ChartSpecOut] = Field(
        None,
        description=("Spec opcional de gráfico para renderizar junto a la "
                     "respuesta. Presente cuando el Analyst devuelve datos "
                     "con `series` no vacío y el usuario no ha pedido "
                     "explícitamente 'sin gráfico'."),
    )


@router.post("", response_model=ChatResponse, summary="Enviar mensaje al orquestador")
def chat(
    req: ChatRequest,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> ChatResponse:
    """
    Pipeline:
      1. Obtener (o crear) la `ChatSession` del usuario.
      2. Persistir el mensaje del usuario en `chat_messages`.
      3. Auto-titular si es el primer turno.
      4. Construir el contexto de prompt desde BD (summary + últimos K mensajes).
      5. Invocar el grafo con `thread_id` único por turno → la BD es la fuente
         de verdad de la historia, no el checkpointer.
      6. Persistir la respuesta del asistente.
      7. Disparar `_maybe_summarize` si la cola crece más allá del horizonte.
    """
    session = _get_or_create_session(db, user_id, req.session_id)
    is_first_turn = session.title == "Nueva conversación"

    _persist_message(db, session, "user", req.message)
    if is_first_turn:
        _autotitle_if_first(session, req.message)

    user_role = _load_user_role(db, user_id)
    with start_observation(
        name="chat.request",
        as_type="span",
        input={
            "message": req.message,
            "session_id": str(session.id),
            "user_role": user_role,
        },
        user_id=user_id,
        session_id=str(session.id),
    ):
        with propagate_user_context(user_id, str(session.id), metadata={"entry_point": "chat"}):
            prompt_messages = _build_prompt_messages(db, session)

            # thread_id único por turno: evita la acumulación implícita del
            # MemorySaver de LangGraph. Cada invoke arranca con la lista que le
            # pasamos explícitamente, leída de nuestra BD.
            turn_thread_id = f"{user_id}:{session.id}:{uuid.uuid4()}"
            config = {"configurable": {"thread_id": turn_thread_id}}

            graph_failed = False
            graph_error: Optional[Exception] = None
            try:
                final = _get_graph().invoke(
                    {
                        "messages": prompt_messages,
                        "user_id": user_id,
                        "session_id": str(session.id),
                        "user_role": user_role,
                        # Slots reseteados por turno: evita que el narrador reaproveche
                        # datos del turno anterior cuando la pregunta nueva no los pide.
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
                # No propagamos 500: el chat tiene que responder SIEMPRE algo
                # legible. El detalle técnico queda en `events` (logger.exception)
                # para el panel admin.
                logger.exception(f"chat invoke falló: {e}")
                graph_failed = True
                graph_error = e
                final = {"messages": [], "last_decision": None,
                         "analysis_report": None}

    if graph_failed:
        text = (
            f"Lo siento, no pude procesar tu mensaje por un fallo técnico del "
            f"sistema ({type(graph_error).__name__}). Inténtalo de nuevo en unos "
            "segundos. Si persiste, revisa el panel Monitor o avisa al "
            "administrador."
        )
        decision = None
        action = None
    else:
        last_msg = final["messages"][-1] if final.get("messages") else None
        decision = final.get("last_decision")
        text = (
            last_msg.content if last_msg is not None and hasattr(last_msg, "content")
            else (str(last_msg) if last_msg is not None else "(sin respuesta)")
        )
        action = decision.action if decision else None

    # Si la respuesta viene de un análisis con series temporales/categóricas,
    # generamos un ChartSpec para que el frontend lo pinte junto al texto.
    chart_dict = _build_chart_spec(final.get("analysis_report"))
    chart = ChartSpecOut(**chart_dict) if chart_dict else None

    # Persistimos el chart junto al mensaje para que la rehidratación de la
    # sesión (GET /chat/sessions/{id}) lo recupere.
    _persist_message(db, session, "assistant", text, action=action, chart=chart_dict)
    _maybe_summarize(db, session, user_id)

    return ChatResponse(
        response=text,
        session_id=str(session.id),
        last_action=action,
        chart=chart,
    )


# Gestión de sesiones persistentes (Fase 1 C.2)

class ChatSessionOut(BaseModel):
    id: str
    title: str
    summary: Optional[str] = None
    archived: bool = False
    created_at: datetime
    last_message_at: datetime


class ChatMessageOut(BaseModel):
    role: str
    content: str
    action: Optional[str] = None
    sequence: int
    created_at: datetime
    chart: Optional[dict[str, Any]] = None


class ChatSessionDetail(BaseModel):
    """Sesión + mensajes recientes para rehidratar la UI."""
    session: ChatSessionOut
    messages: list[ChatMessageOut]


class CreateSessionRequest(BaseModel):
    title: Optional[str] = Field(
        None, max_length=120,
        description="Si se omite, se usará 'Nueva conversación' (el primer mensaje lo sobrescribirá).",
    )


class PatchSessionRequest(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=120)
    archived: Optional[bool] = None


def _parse_uuid_or_404(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except (TypeError, ValueError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "session_id no es un UUID válido")


def _session_to_out(row: ChatSession) -> ChatSessionOut:
    return ChatSessionOut(
        id=str(row.id),
        title=row.title,
        summary=row.summary,
        archived=row.archived,
        created_at=row.created_at,
        last_message_at=row.last_message_at,
    )


def _message_to_out(msg: ChatMessage) -> ChatMessageOut:
    return ChatMessageOut(
        role=msg.role,
        content=msg.content,
        action=msg.action,
        sequence=msg.sequence,
        created_at=msg.created_at,
        chart=msg.chart,
    )


def _get_user_session(
    db: Session, user_id: str, session_id: str,
) -> ChatSession:
    """Carga la sesión `session_id` validando que pertenezca al user_id. 404 si no."""
    sid = _parse_uuid_or_404(session_id)
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "user_id no es un UUID válido")

    row = db.execute(
        select(ChatSession).where(
            ChatSession.id == sid, ChatSession.user_id == uid,
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sesión no encontrada")
    return row


@router.post(
    "/sessions",
    response_model=ChatSessionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Crear una nueva sesión de chat",
)
def create_session(
    body: CreateSessionRequest,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> ChatSessionOut:
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "user_id inválido")

    row = ChatSession(
        user_id=uid,
        title=(body.title or "Nueva conversación").strip()[:120],
    )
    db.add(row)
    db.flush()
    db.refresh(row)
    return _session_to_out(row)


@router.get(
    "/sessions",
    response_model=list[ChatSessionOut],
    summary="Listar las sesiones del usuario",
)
def list_sessions(
    include_archived: bool = False,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> list[ChatSessionOut]:
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "user_id inválido")

    stmt = select(ChatSession).where(ChatSession.user_id == uid)
    if not include_archived:
        stmt = stmt.where(ChatSession.archived.is_(False))
    stmt = stmt.order_by(desc(ChatSession.last_message_at))

    rows = db.execute(stmt).scalars().all()
    return [_session_to_out(r) for r in rows]


@router.get(
    "/sessions/{session_id}",
    response_model=ChatSessionDetail,
    summary="Obtener una sesión con sus últimos mensajes",
)
def get_session_detail(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> ChatSessionDetail:
    """Devuelve la sesión + los últimos K mensajes (ordenados ascendentemente).

    Los mensajes más antiguos que el horizonte ya no están en `chat_messages`,
    pero su contenido sobrevive en `session.summary` (regenerado por el
    backend cada N turnos — cf. Fase 1 C.3).
    """
    row = _get_user_session(db, user_id, session_id)

    # Mensajes más recientes (orden DESC) → invertir para devolverlos en ASC.
    recent = db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == row.id)
        .order_by(desc(ChatMessage.sequence))
        .limit(RECENT_MESSAGES_LIMIT)
    ).scalars().all()
    recent.reverse()

    return ChatSessionDetail(
        session=_session_to_out(row),
        messages=[_message_to_out(m) for m in recent],
    )


@router.patch(
    "/sessions/{session_id}",
    response_model=ChatSessionOut,
    summary="Renombrar o archivar una sesión",
)
def patch_session(
    session_id: str,
    body: PatchSessionRequest,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> ChatSessionOut:
    row = _get_user_session(db, user_id, session_id)
    changed = False
    if body.title is not None:
        row.title = body.title.strip()[:120]
        changed = True
    if body.archived is not None:
        row.archived = body.archived
        changed = True
    if changed:
        db.flush()
        db.refresh(row)
    return _session_to_out(row)


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Borrar una sesión (cascada a sus mensajes)",
)
def delete_session(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> None:
    row = _get_user_session(db, user_id, session_id)
    db.delete(row)
    db.flush()
    return None

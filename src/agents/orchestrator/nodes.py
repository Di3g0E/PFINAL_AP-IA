"""
Nodos del grafo LangGraph.

Cada nodo es una función pura `state → dict_update`. LangGraph fusiona el
diccionario devuelto con el estado actual (los `messages` se acumulan vía
`add_messages`).

orchestrator_node tiene **dos modos** según el estado:

  - **Router** (sin datos de sub-agente): el LLM elige una acción mediante
    `with_structured_output(OrchestratorDecision)`. Sin texto al usuario.
  - **Narrator** (con datos): el LLM produce un texto en español a partir
    del slot poblado y termina con `respond_final`.

Esta separación evita que el LLM, viendo el `analysis_report` ya generado,
vuelva a elegir `delegate_analyst` y entre en bucle.
"""

from __future__ import annotations

import inspect
import re

import pandas as pd
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from loguru import logger

from src.agents.analyst import agent as analyst
from src.agents.analyst.data_source import load_user_transactions
from src.agents.contracts import (
    AnalysisReport, ManualEntry, OrchestratorDecision,
    RegistryResult, RejectedItem, SecurityVerdict,
)
from src.agents.registrar import agent as registrar
from src.agents.security import agent as security
from src.agents.orchestrator.llm_factory import get_llm
from src.agents.orchestrator.prompts import (
    NARRATOR_SYSTEM_PROMPT, build_context_block, get_router_system_prompt,
)
from src.agents.orchestrator.state import MAX_ITERATIONS, OrchestratorState
from src.utils.logging_config import Stopwatch, log_event


def _load_user_dataframe(user_id: str) -> pd.DataFrame:
    """Alias local hacia el helper compartido (mantiene la API anterior)."""
    return load_user_transactions(user_id)


# Algunos LLMs filtran al final del texto la etiqueta de la accion que han
# elegido (`respond_final`, `delegate_analyst`, ...). El prompt ya lo prohibe,
# pero esto es un cinturon de seguridad para que nunca llegue al usuario.
_ACTION_LABEL_RE = re.compile(
    r"\s*(?:respond_final|delegate_analyst|delegate_security|delegate_registrar|"
    r"ask_user|confirm_pending|reject_pending)\s*\.?\s*$",
    re.IGNORECASE,
)


def _strip_action_labels(text: str) -> str:
    return _ACTION_LABEL_RE.sub("", text).rstrip()


# Helpers privados del orquestador

def _has_subagent_data(state: OrchestratorState) -> bool:
    return (
        state.get("analysis_report") is not None
        or state.get("security_verdict") is not None
        or state.get("registry_result") is not None
    )


def _route(state: OrchestratorState, iterations: int) -> dict:
    """Elige la siguiente acción mediante structured output."""
    user_id = state.get("user_id")
    session_id = state.get("session_id")

    with Stopwatch(agent="orchestrator", action="route",
                   user_id=user_id, session_id=session_id) as sw:
        llm = get_llm(user_id=user_id)
        router = llm.with_structured_output(OrchestratorDecision)
        prompt: list[BaseMessage] = [
            SystemMessage(content=get_router_system_prompt()),
            *state.get("messages", []),
        ]
        decision: OrchestratorDecision = router.invoke(prompt)
        sw.payload["action"] = decision.action
        sw.payload["target_op"] = decision.target_op

    update: dict = {"last_decision": decision, "iterations": iterations + 1}
    # Si el LLM responde directamente al usuario (sin delegar), añadimos el mensaje
    if decision.action in ("ask_user", "respond_final"):
        update["messages"] = [AIMessage(content=decision.user_message or "(sin contenido)")]
    return update


def _narrate(state: OrchestratorState, iterations: int) -> dict:
    """Produce el texto final en español a partir del slot poblado."""
    user_id = state.get("user_id")
    session_id = state.get("session_id")

    with Stopwatch(agent="orchestrator", action="narrate",
                   user_id=user_id, session_id=session_id) as sw:
        llm = get_llm(user_id=user_id)
        context_block = build_context_block(
            analysis_report=state.get("analysis_report"),
            security_verdict=state.get("security_verdict"),
            registry_result=state.get("registry_result"),
        )
        prompt: list[BaseMessage] = [
            SystemMessage(content=NARRATOR_SYSTEM_PROMPT),
            SystemMessage(content=context_block),
            *state.get("messages", []),
        ]
        response = llm.invoke(prompt)
        text = response.content if hasattr(response, "content") else str(response)
        text = _strip_action_labels(text)
        sw.payload["chars"] = len(text)

    return {
        "messages": [AIMessage(content=text)],
        "last_decision": OrchestratorDecision(action="respond_final", user_message=text),
        "iterations": iterations + 1,
    }


# Nodo principal expuesto al grafo

def orchestrator_node(state: OrchestratorState) -> dict:
    """
    Decide entre rutear (no hay datos) o narrar (ya hay datos).

    Si `iterations >= MAX_ITERATIONS`, fuerza un mensaje de fallback para
    blindarse contra bucles patológicos.
    """
    user_id = state.get("user_id")
    session_id = state.get("session_id")
    iterations = state.get("iterations", 0)

    if iterations >= MAX_ITERATIONS:
        msg = ("He alcanzado el límite de pasos internos para esta consulta. "
               "Reformula la pregunta y volveré a intentarlo.")
        log_event(agent="orchestrator", action="iteration_limit",
                  status="warning", user_id=user_id, session_id=session_id,
                  payload={"iterations": iterations})
        return {
            "messages": [AIMessage(content=msg)],
            "last_decision": OrchestratorDecision(action="respond_final", user_message=msg),
            "iterations": iterations + 1,
        }

    if _has_subagent_data(state):
        return _narrate(state, iterations)
    return _route(state, iterations)


# Sub-agente Analyst: enruta la operación al módulo correspondiente.
#
# El LLM puede pasar kwargs alucinados que no estén en la firma real (p. ej.
# `period` a `monthly_summary`). `_safe_kwargs` filtra el dict con la firma
# real para que solo lleguen los argumentos válidos.

def _safe_kwargs(func, args: dict) -> dict:
    """Devuelve un dict con sólo los kwargs aceptados por `func`."""
    sig = inspect.signature(func)
    accepted = {
        name for name, p in sig.parameters.items()
        if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD,
                      inspect.Parameter.KEYWORD_ONLY)
    }
    extras = set(args) - accepted
    if extras:
        logger.debug(f"_safe_kwargs descarta {extras} para {func.__name__}")
    return {k: v for k, v in args.items() if k in accepted}


# Cada handler recibe (df, args, user_id). Las ops puramente analíticas
# ignoran user_id; las de objetivos lo necesitan para leer/escribir su tabla.
_ANALYST_OPS = {
    "monthly_summary":    lambda df, args, user_id: analyst.monthly_summary(df, **_safe_kwargs(analyst.monthly_summary, args)),
    "category_breakdown": lambda df, args, user_id: analyst.category_breakdown(df, **_safe_kwargs(analyst.category_breakdown, args)),
    "spending_trends":    lambda df, args, user_id: analyst.spending_trends(df, **_safe_kwargs(analyst.spending_trends, args)),
    "savings_rate":       lambda df, args, user_id: analyst.savings_rate(df, **_safe_kwargs(analyst.savings_rate, args)),
    "detect_anomalies":   lambda df, args, user_id: analyst.detect_anomalies(df),
    "recurring_expenses": lambda df, args, user_id: analyst.recurring_expenses(df),
    "recent_transactions": lambda df, args, user_id: analyst.recent_transactions(df, **_safe_kwargs(analyst.recent_transactions, args)),
    "predict_next_month": lambda df, args, user_id: analyst.predict_next_month(df, **_safe_kwargs(analyst.predict_next_month, args)),
    # Objetivos: si el LLM no pasa goals, check_goals los carga de BD por user_id.
    "check_goals":        lambda df, args, user_id: analyst.check_goals(df, goals=args.get("goals"), user_id=user_id),
    "set_goal":           lambda df, args, user_id: analyst.set_goal(user_id, **_safe_kwargs(analyst.set_goal, args)),
    "list_goals":         lambda df, args, user_id: analyst.list_goals(user_id),
    "remove_goal":        lambda df, args, user_id: analyst.remove_goal(user_id, **_safe_kwargs(analyst.remove_goal, args)),
}


def analyst_node(state: OrchestratorState) -> dict:
    """Ejecuta la operación del Analyst indicada por el Orquestador."""
    user_id = state.get("user_id", "")
    session_id = state.get("session_id")
    decision = state.get("last_decision")

    if decision is None or decision.action != "delegate_analyst":
        return {"analysis_report": AnalysisReport(type="summary",
                                                  metrics={"error": "decisión inválida"})}

    op = decision.target_op or ""
    args = decision.target_args or {}
    handler = _ANALYST_OPS.get(op)

    with Stopwatch(agent="analyst", action=op or "unknown",
                   user_id=user_id, session_id=session_id) as sw:
        if handler is None:
            report = AnalysisReport(type="summary",
                                    metrics={"error": f"operación desconocida: {op!r}"})
            sw.payload["error"] = "unknown_op"
        else:
            try:
                # Las ops de gestión de objetivos no necesitan el DataFrame,
                # pero cargarlo es barato (cache) y mantener la firma uniforme
                # simplifica el dispatcher.
                df = _load_user_dataframe(user_id)
                report = handler(df, args, user_id)
            except Exception as e:
                logger.exception(f"Analyst {op} falló: {e}")
                report = AnalysisReport(type="summary",
                                        metrics={"error": f"{type(e).__name__}: {e}"})
                sw.payload["error"] = str(e)
        sw.payload["report_type"] = report.type

    return {"analysis_report": report}


# Stubs para Security y Registrar (no implementados en v1)

def security_node(state: OrchestratorState) -> dict:
    """Ejecuta la operación del Security indicada por el Orquestador.

    Operaciones soportadas en v1:
      - `validate_transaction` — anti-anomalía (operacional).
      - `register_user` / `login_user` — devuelven 'deny' explicando que la
        biometría llega en la siguiente iteración (los endpoints HTTP las
        invocarán cuando exista el frontend con webcam).
    """
    user_id = state.get("user_id", "")
    session_id = state.get("session_id")
    decision = state.get("last_decision")

    if decision is None or decision.action != "delegate_security":
        return {"security_verdict": SecurityVerdict(
            decision="deny", reason="decisión inválida",
        )}

    op = decision.target_op or ""

    # Las operaciones del Security NO se invocan desde el chat:
    #   - validate_transaction → interno del Registrar.
    #   - register_user / login_user → requieren imagen facial (bytes), que
    #     llega solo por endpoints HTTP (POST con multipart). El LLM no
    #     puede subirla.
    # Devolvemos un mensaje informativo para que el narrador lo comunique.
    with Stopwatch(agent="security", action=op or "unknown",
                   user_id=user_id, session_id=session_id) as sw:
        if op == "validate_transaction":
            reason = ("validate_transaction es interno del Registrar; "
                      "no se invoca desde el chat.")
        elif op in ("register_user", "login_user"):
            reason = (f"La operación '{op}' requiere subir una foto y se invoca "
                      "vía endpoint HTTP /auth (no implementado todavía en chat).")
        else:
            reason = f"Operación desconocida: {op!r}"
        verdict = SecurityVerdict(decision="deny", reason=reason)
        sw.payload["decision"] = verdict.decision

    return {"security_verdict": verdict}


def registrar_node(state: OrchestratorState) -> dict:
    """Ejecuta la operación del Registrar indicada por el Orquestador."""
    user_id = state.get("user_id", "")
    session_id = state.get("session_id")
    decision = state.get("last_decision")

    if decision is None or decision.action != "delegate_registrar":
        return {"registry_result": RegistryResult(
            rejected=[RejectedItem(reason="decisión inválida")],
        )}

    op = decision.target_op or ""
    args = decision.target_args or {}

    with Stopwatch(agent="registrar", action=op or "unknown",
                   user_id=user_id, session_id=session_id) as sw:
        try:
            if op == "add_manual_transaction":
                # El LLM puede no incluir user_id; lo inyectamos del estado
                args = {**args, "user_id": args.get("user_id") or user_id}
                entry = ManualEntry(**args)
                result = registrar.add_manual_transaction(entry)
            elif op == "add_from_image":
                # Las imágenes no llegan por el LLM (vienen del endpoint HTTP);
                # esta operación se llamará cuando exista el upload.
                result = RegistryResult(rejected=[RejectedItem(
                    reason="add_from_image solo está disponible vía endpoint HTTP",
                )])
            elif op == "list_pending_reviews":
                result = registrar.list_pending_reviews(user_id)
            elif op == "confirm_pending":
                tx_id = args.get("transaction_id") or args.get("id") or ""
                result = registrar.confirm_pending(user_id, tx_id)
            elif op == "reject_pending":
                tx_id = args.get("transaction_id") or args.get("id") or ""
                result = registrar.reject_pending(user_id, tx_id)
            else:
                result = RegistryResult(rejected=[RejectedItem(
                    reason=f"operación desconocida: {op!r}",
                    raw_input=args,
                )])
            sw.payload["accepted"] = len(result.accepted)
            sw.payload["pending"] = len(result.pending_review)
            sw.payload["rejected"] = len(result.rejected)
        except Exception as e:
            logger.exception(f"Registrar {op} falló: {e}")
            result = RegistryResult(rejected=[RejectedItem(
                reason=f"{type(e).__name__}: {e}",
                raw_input=args,
            )])
            sw.payload["error"] = str(e)

    return {"registry_result": result}

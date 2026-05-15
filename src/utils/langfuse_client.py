"""
Cliente Langfuse para monitorización humana de los agentes.

Cumple el requisito "Uso de Langfuse" del enunciado: cada llamada a un LLM
(router, narrator, conversational, summary) se traza con su `user_id`,
`session_id` y la operación que la disparó. Errores, latencias y prompts
quedan visibles en https://cloud.langfuse.com.

Degrada elegantemente:
  - Sin `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` configurados,
    `build_callbacks()` devuelve `[]` y los `llm.invoke(config={"callbacks": [...]})`
    siguen funcionando sin trazar nada.
  - Si la librería langfuse no está instalada (poco probable, está en
    requirements.txt) o falla al inicializar, también devuelve `[]` y
    loguea un warning.
"""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from src.utils.config import settings


def build_callbacks(
    *,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    agent: Optional[str] = None,
    action: Optional[str] = None,
    extra_metadata: Optional[dict[str, Any]] = None,
) -> list[Any]:
    """Construye la lista de callbacks para pasar a `llm.invoke(config={...})`.

    Devuelve `[]` si Langfuse no está configurado. Cada item es un
    `CallbackHandler` de Langfuse con el contexto correcto para que las
    traces aparezcan correlacionadas por usuario y sesión.
    """
    if not settings.langfuse_enabled:
        return []
    try:
        # Importación tardía: si la librería falla, no rompemos toda la app.
        from langfuse.callback import CallbackHandler
    except Exception as e:
        logger.warning(f"langfuse no disponible: {e}")
        return []

    metadata: dict[str, Any] = {}
    if agent:
        metadata["agent"] = agent
    if action:
        metadata["action"] = action
    if extra_metadata:
        metadata.update(extra_metadata)

    try:
        handler = CallbackHandler(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
            session_id=session_id,
            user_id=user_id,
            metadata=metadata or None,
        )
        return [handler]
    except Exception as e:
        logger.warning(f"Langfuse CallbackHandler falló al inicializar: {e}")
        return []


def invoke_config(
    *,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    agent: Optional[str] = None,
    action: Optional[str] = None,
    extra_metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Atajo: devuelve directamente el `config={"callbacks": [...]}`.

    Uso:
        llm.invoke(prompt, config=invoke_config(user_id=..., agent='analyst', ...))
    """
    callbacks = build_callbacks(
        user_id=user_id, session_id=session_id,
        agent=agent, action=action, extra_metadata=extra_metadata,
    )
    return {"callbacks": callbacks} if callbacks else {}

"""Integración opcional con Langfuse.

Permite inicializar el cliente Langfuse si las variables de entorno están
configuradas y proporciona helpers de instrumentación sin forzar la dependencia
cuando no está instalada.
"""

from __future__ import annotations

import os
from contextlib import nullcontext
from typing import Any, Callable, ContextManager, Optional, TypeVar

from loguru import logger

from src.utils.config import settings

F = TypeVar("F", bound=Callable[..., Any])


LANGFUSE_AVAILABLE = False
langfuse: Any = None
observe: Optional[Callable[..., Any]] = None
get_client: Optional[Callable[..., Any]] = None
propagate_attributes: Optional[Callable[..., Any]] = None

try:
    import langfuse as _langfuse
    from langfuse import get_client as _get_client
    from langfuse import observe as _observe
    from langfuse import propagate_attributes as _propagate_attributes

    LANGFUSE_AVAILABLE = True
    langfuse = _langfuse
    get_client = _get_client
    observe = _observe
    propagate_attributes = _propagate_attributes
except ImportError:
    logger.debug("Langfuse no está instalado; la instrumentación Langfuse está deshabilitada.")

langfuse_client: Optional[Any] = None


def _identity_decorator(fn: F) -> F:
    return fn


def _env_passthrough() -> None:
    """Exporta los valores de configuración de Langfuse a las variables de entorno."""
    if settings.langfuse_public_key:
        os.environ.setdefault("LANGFUSE_PUBLIC_KEY", settings.langfuse_public_key)
    if settings.langfuse_secret_key:
        os.environ.setdefault("LANGFUSE_SECRET_KEY", settings.langfuse_secret_key)
    if settings.langfuse_base_url:
        os.environ.setdefault("LANGFUSE_BASE_URL", settings.langfuse_base_url)


def is_enabled() -> bool:
    return bool(LANGFUSE_AVAILABLE and settings.langfuse_secret_key)


def init_langfuse() -> None:
    global langfuse_client
    if not LANGFUSE_AVAILABLE:
        logger.warning("Langfuse no disponible: paquete no instalado.")
        return
    if not settings.langfuse_secret_key:
        logger.info("Langfuse deshabilitado: LANGFUSE_SECRET_KEY no configurada.")
        return

    _env_passthrough()
    try:
        langfuse_client = get_client()
        logger.info("Langfuse inicializado correctamente.")
    except Exception as exc:
        langfuse_client = None
        logger.exception(f"Error inicializando Langfuse: {exc}")


def shutdown_langfuse() -> None:
    global langfuse_client
    if langfuse_client is None:
        return
    try:
        if hasattr(langfuse_client, "shutdown"):
            langfuse_client.shutdown()
            logger.info("Langfuse finalizado correctamente.")
    except Exception as exc:
        logger.exception(f"Error cerrando Langfuse: {exc}")
    finally:
        langfuse_client = None


def observe_decorator(*, name: Optional[str] = None, as_type: Optional[str] = None,
                       capture_input: bool = True, capture_output: bool = True,
                       **kwargs: Any) -> Callable[[F], F]:
    if LANGFUSE_AVAILABLE and observe is not None and settings.langfuse_secret_key:
        return observe(name=name, as_type=as_type,
                       capture_input=capture_input,
                       capture_output=capture_output,
                       **kwargs)
    return _identity_decorator


def start_observation(
    *,
    name: str,
    as_type: str = "span",
    input: Optional[dict[str, Any]] = None,
    metadata: Optional[dict[str, Any]] = None,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> ContextManager[Any]:
    """Context manager para envolver una operación que se trazea en Langfuse.

    En Langfuse v4 las observaciones se crean desde la INSTANCIA del cliente
    (`client.start_as_current_observation(...)`), no desde el módulo. Esa
    confusión era la causa del bug que dejaba el proyecto vacío en
    cloud.langfuse.com aunque las keys eran válidas.
    """
    if not LANGFUSE_AVAILABLE or not settings.langfuse_secret_key:
        return nullcontext()

    _env_passthrough()

    # Si init_langfuse() no se ha llamado todavía (p. ej. importación fuera
    # del lifespan de FastAPI), obtenemos el cliente perezosamente.
    client = langfuse_client
    if client is None and get_client is not None:
        try:
            client = get_client()
        except Exception as exc:
            logger.warning(f"start_observation: no se pudo obtener cliente: {exc}")
            return nullcontext()
    if client is None:
        return nullcontext()

    # Adjuntamos user_id / session_id al metadata para que aparezcan en la
    # UI de Langfuse asociados a la trace. En v4 no van como kwargs propios
    # de la observación — viven en metadata o en update_trace().
    meta: dict[str, Any] = dict(metadata or {})
    if user_id:
        meta.setdefault("user_id", user_id)
    if session_id:
        meta.setdefault("session_id", session_id)

    try:
        ctx = client.start_as_current_observation(
            as_type=as_type,
            name=name,
            input=input,
            metadata=meta or None,
        )
        # Propagamos user_id/session_id a la trace raíz si el SDK lo soporta.
        if user_id or session_id:
            try:
                client.update_current_trace(
                    user_id=user_id, session_id=session_id,
                )
            except Exception:
                # En v4 update_current_trace puede no existir; el metadata
                # ya lleva la info, así que ignoramos el fallo.
                pass
        return ctx
    except Exception as exc:
        logger.exception(f"Error creando observación Langfuse '{name}': {exc}")
        return nullcontext()


def get_langfuse_callbacks() -> list[Any]:
    """Devuelve la lista de callbacks Langfuse para pasar a `llm.invoke(config=...)`.

    El `CallbackHandler` de Langfuse para Langchain captura automáticamente:
      - Modelo usado (`provided_model_name`)
      - Tokens de entrada y salida (`usage_details`)
      - Coste calculado (`total_cost`)
      - Latencia y `time_to_first_token`
      - Errores del LLM (`level=ERROR` + `status_message`)

    Devuelve una lista vacía cuando:
      - Langfuse no está instalado
      - `LANGFUSE_SECRET_KEY` no está configurada
      - El paquete `langchain` no está instalado (la integración de Langfuse
        con Langchain depende de él, pero el resto del proyecto solo usa
        `langchain-core` — si falta no rompemos, simplemente desactivamos)

    Los llamadores hacen::

        callbacks = get_langfuse_callbacks()
        config = {"callbacks": callbacks} if callbacks else None
        response = llm.invoke(prompt, config=config)

    Es seguro llamarlo en cada invocación: el constructor del handler es
    barato (sin I/O) y crea estado local al run. Reutilizar instancias
    entre invocaciones concurrentes podría mezclar run-ids en sus dicts
    internos — preferimos uno por llamada.
    """
    if not LANGFUSE_AVAILABLE or not settings.langfuse_secret_key:
        return []
    _env_passthrough()
    try:
        from langfuse.langchain import CallbackHandler  # noqa: WPS433 (lazy import)
    except (ImportError, ModuleNotFoundError) as exc:
        # langchain meta-package ausente — la integración Langfuse↔Langchain
        # no puede crearse, pero el resto del proyecto sigue funcionando.
        # Avisamos UNA vez para no spamear los logs.
        if not getattr(get_langfuse_callbacks, "_warned", False):
            logger.warning(
                "Langfuse-Langchain CallbackHandler no disponible: {}. "
                "Las llamadas al LLM no reportarán tokens/coste a Langfuse. "
                "Solución: `pip install langchain`.",
                exc,
            )
            setattr(get_langfuse_callbacks, "_warned", True)
        return []
    try:
        return [CallbackHandler()]
    except Exception as exc:
        logger.warning(f"No se pudo crear CallbackHandler de Langfuse: {exc}")
        return []


def propagate_user_context(
    user_id: str,
    session_id: str,
    metadata: Optional[dict[str, Any]] = None,
) -> ContextManager[Any]:
    if not LANGFUSE_AVAILABLE or not settings.langfuse_secret_key or propagate_attributes is None:
        return nullcontext()

    try:
        return propagate_attributes(
            user_id=user_id,
            session_id=session_id,
            metadata=metadata or {},
            as_baggage=True,
        )
    except Exception as exc:
        logger.exception(f"Error propagando atributos Langfuse: {exc}")
        return nullcontext()

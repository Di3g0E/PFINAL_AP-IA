"""
Factory de LLM por usuario.

Lógica:
  1. Si el usuario tiene `user_settings.llm_provider` + `llm_api_key_encrypted`,
     descifra la key (con la clave maestra Fernet del servidor) y devuelve un
     cliente del proveedor elegido.
  2. Si no, fallback a **Groq compartido** con la `GROQ_API_KEY` del `.env`.

Catálogo de modelos (cerrado en frontend para evitar typos):

    groq:      llama-3.3-70b-versatile, llama-3.1-8b-instant, mixtral-8x7b-32768
    openai:    gpt-4o, gpt-4o-mini, gpt-4.1-mini
    anthropic: claude-sonnet-4-5, claude-haiku-4-5
    google:    gemini-2.0-flash, gemini-1.5-pro, gemini-1.5-flash
"""

from __future__ import annotations

import uuid
from typing import Optional

from loguru import logger

from src.utils.config import settings
from src.utils.security import decrypt_api_key


# Catálogo de modelos por proveedor (lista cerrada para el dropdown del frontend)
PROVIDER_MODELS: dict[str, list[str]] = {
    "groq": [
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
        "mixtral-8x7b-32768",
    ],
    "openai": [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4.1-mini",
    ],
    "anthropic": [
        "claude-sonnet-4-5",
        "claude-haiku-4-5",
    ],
    "google": [
        "gemini-2.0-flash",
        "gemini-1.5-pro",
        "gemini-1.5-flash",
    ],
}


def list_providers() -> dict[str, list[str]]:
    """Devuelve el catálogo público para construir el dropdown de la UI."""
    return PROVIDER_MODELS


def _build_groq(model: str, api_key: str, **kw):
    from langchain_groq import ChatGroq
    return ChatGroq(model=model, api_key=api_key, temperature=kw.get("temperature", 0))


def _build_openai(model: str, api_key: str, **kw):
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(model=model, api_key=api_key, temperature=kw.get("temperature", 0))


def _build_anthropic(model: str, api_key: str, **kw):
    from langchain_anthropic import ChatAnthropic
    return ChatAnthropic(model=model, api_key=api_key, temperature=kw.get("temperature", 0))


def _build_google(model: str, api_key: str, **kw):
    from langchain_google_genai import ChatGoogleGenerativeAI
    return ChatGoogleGenerativeAI(model=model, google_api_key=api_key,
                                  temperature=kw.get("temperature", 0))


_BUILDERS = {
    "groq": _build_groq,
    "openai": _build_openai,
    "anthropic": _build_anthropic,
    "google": _build_google,
}


def build_llm(provider: str, model: str, api_key: str, **kw):
    """Crea un cliente LLM concreto. Levanta `ValueError` si el proveedor es desconocido."""
    builder = _BUILDERS.get(provider)
    if builder is None:
        raise ValueError(f"Proveedor desconocido: {provider!r}. "
                         f"Opciones: {sorted(_BUILDERS)}")
    if model not in PROVIDER_MODELS.get(provider, []):
        logger.warning(f"Modelo {model!r} fuera del catálogo para {provider!r}; se intenta igualmente")
    return builder(provider, model, api_key=api_key, **kw)  # type: ignore[arg-type]


def _shared_groq_fallback(**kw):
    """Cliente Groq compartido con la API key del servidor (.env)."""
    if not settings.groq_api_key:
        raise RuntimeError(
            "GROQ_API_KEY no configurada en .env. "
            "Crea una cuenta gratuita en https://console.groq.com y añádela al .env."
        )
    return _build_groq(settings.groq_default_model, settings.groq_api_key, **kw)


def _database_url_is_valid() -> bool:
    """
    Heurística simple: la URL es usable si empieza por un esquema reconocido
    y NO es el placeholder del template (`...`).
    """
    url = (settings.database_url or "").strip()
    if not url or url == "..." or url.endswith("://"):
        return False
    return url.startswith(("postgresql://", "postgresql+psycopg://",
                           "postgresql+asyncpg://", "sqlite://"))


def get_llm(user_id: Optional[str] = None, **kw):
    """
    Devuelve el LLM configurado por el usuario, con fallback a Groq compartido.

    En la primera interacción (usuario sin settings o sin DB disponible) usa
    el GROQ del .env directamente.
    """
    if user_id is None or not _database_url_is_valid():
        # Sin user_id o sin BD configurada → omitimos el lookup silenciosamente
        return _shared_groq_fallback(**kw)

    try:
        from sqlalchemy import select

        from src.data.database import get_session
        from src.data.schema import UserSettings
    except Exception as e:
        logger.warning(f"DB no disponible, usando Groq compartido: {e}")
        return _shared_groq_fallback(**kw)

    # La columna user_settings.user_id es Uuid → SQLAlchemy exige uuid.UUID,
    # no string. Si el str no parsea como UUID, no tiene sentido buscar en BD.
    try:
        user_uuid = uuid.UUID(user_id)
    except ValueError:
        return _shared_groq_fallback(**kw)

    try:
        with get_session() as session:
            stmt = select(UserSettings).where(UserSettings.user_id == user_uuid)
            row = session.execute(stmt).scalar_one_or_none()
            if row and row.llm_provider and row.llm_api_key_encrypted and row.llm_model:
                api_key = decrypt_api_key(row.llm_api_key_encrypted)
                logger.debug(f"LLM personal: {row.llm_provider}/{row.llm_model} para {user_id}")
                return build_llm(row.llm_provider, row.llm_model, api_key, **kw)
    except Exception as e:
        logger.warning(f"Lookup de user_settings falló para {user_id}: {e}")

    logger.debug(f"Fallback a Groq compartido para {user_id}")
    return _shared_groq_fallback(**kw)

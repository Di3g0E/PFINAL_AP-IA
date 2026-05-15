"""
Cliente HTTP interno que usan los tools para hablar con los routers /modules/*.

Por qué loopback (en vez de llamada Python directa):
  El enunciado pide microservicios REST. En despliegue monoproceso (HF, Render,
  local) la llamada va `localhost:PORT → uvicorn worker pool → handler`. Uvicorn
  asigna workers async/threadpool distintos al request original del chat y al
  hop interno, así que no hay deadlock siempre que no haya muchos chats
  concurrentes (>40, default starlette anyio pool).

Auth:
  Los endpoints /modules/* declaran `Depends(get_current_user_id)`. Generamos
  un JWT corto (5 min) con el mismo `JWT_SECRET` y `user_id` del estado del
  grafo, así el bearer pasa la validación del middleware sin tener que tocar
  los routers.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Optional

import httpx
from loguru import logger

from src.api.dependencies import create_access_token
from src.utils.config import settings


# Timeouts generosos en read porque las operaciones del analyst pueden
# cargar miles de filas de Supabase. Connect rápido (es loopback).
_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)

# JWT corto para los hops internos. Si los regenera cada llamada, el coste es
# despreciable y nunca caduca a mitad de un turno.
_INTERNAL_TOKEN_TTL = timedelta(minutes=5)


def _base_url() -> str:
    return settings.internal_api_base_url.rstrip("/")


def _headers(user_id: str, content_type: str = "application/json") -> dict[str, str]:
    token = create_access_token(user_id, expires_delta=_INTERNAL_TOKEN_TTL)
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": content_type,
    }


def _raise_for_module_status(r: httpx.Response, path: str) -> None:
    if r.status_code >= 400:
        # Recoge `detail` (formato FastAPI) si está disponible para hacer
        # el error útil en logs/tests.
        detail: Any
        try:
            detail = r.json().get("detail")
        except Exception:
            detail = r.text
        logger.error(f"Tool {path} → HTTP {r.status_code}: {detail}")
        r.raise_for_status()


def post(path: str, user_id: str, json: Optional[dict] = None) -> Any:
    url = _base_url() + path
    with httpx.Client(timeout=_TIMEOUT) as client:
        r = client.post(url, headers=_headers(user_id), json=json or {})
    _raise_for_module_status(r, path)
    return r.json()


def get(path: str, user_id: str, params: Optional[dict] = None) -> Any:
    url = _base_url() + path
    with httpx.Client(timeout=_TIMEOUT) as client:
        r = client.get(url, headers=_headers(user_id), params=params)
    _raise_for_module_status(r, path)
    return r.json()


def delete(path: str, user_id: str) -> Any:
    url = _base_url() + path
    with httpx.Client(timeout=_TIMEOUT) as client:
        r = client.delete(url, headers=_headers(user_id))
    _raise_for_module_status(r, path)
    if r.status_code == 204 or not r.content:
        return None
    return r.json()

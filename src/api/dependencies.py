"""
Dependencias FastAPI compartidas: JWT (creación + validación) y resolver el
`user_id` autenticado a partir del header `Authorization: Bearer <token>`.

Token payload (JWT estándar):
  - `sub`: user_id (UUID string)
  - `exp`: expiración (timestamp)

El secreto y el algoritmo se leen de `settings.jwt_secret` /
`settings.jwt_algorithm`. Cambia el secreto en producción.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from jose import JWTError, jwt
from loguru import logger

from src.utils.config import settings
from sqlalchemy import select
from src.data.database import get_session
from src.data.schema import User


def create_access_token(
    user_id: str,
    expires_delta: Optional[timedelta] = None,
) -> str:
    """Genera un JWT firmado con el secreto del servidor."""
    if expires_delta is None:
        expires_delta = timedelta(minutes=settings.jwt_expire_minutes)
    payload = {
        "sub": user_id,
        "exp": datetime.now(timezone.utc) + expires_delta,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    """Decodifica y valida un JWT. Levanta HTTP 401 si es inválido o expiró."""
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError as e:
        logger.debug(f"JWT decode falló: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido o expirado",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e


def get_current_user_id(authorization: Optional[str] = Header(default=None)) -> str:
    """
    Dependencia FastAPI: extrae el `user_id` del header
    `Authorization: Bearer <token>`.

    Cualquier endpoint con `Depends(get_current_user_id)` exige autenticación.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token requerido",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.split(" ", 1)[1].strip()
    payload = decode_token(token)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token sin sub")
    return str(user_id)


def require_admin(user_id: str = Depends(get_current_user_id)) -> str:
    """Dependencia que asegura que el `user_id` corresponde a un admin.

    Levanta HTTP 403 si el usuario no tiene `is_admin=True`.
    Devuelve el `user_id` para inyectarlo en el handler cuando se necesite.
    """
    import uuid as _uuid
    from fastapi import HTTPException, status

    try:
        uid = _uuid.UUID(user_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token con sub inválido")
    with get_session() as s:
        row = s.execute(select(User).where(User.id == uid)).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuario no encontrado")
        if not bool(row.is_admin):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Acceso restringido a administradores")
    return str(uid)

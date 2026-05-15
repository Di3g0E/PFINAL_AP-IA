"""
Endpoints para la configuración de usuario y notificaciones.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Literal, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException, status, Request
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.api.dependencies import get_current_user_id
from src.data.database import get_db
from src.data.schema import User, UserSettings
from src.utils.config import settings as app_settings
from src.utils.notifications import notify, UserNotificationConfig

router = APIRouter(prefix="/api/user", tags=["settings"])


@router.post("/telegram-webhook")
async def telegram_webhook(request: Request) -> Dict[str, Any]:
    """Endpoint para recibir actualizaciones del bot de Telegram."""
    try:
        from src.api.telegram_bot import get_bot_service
        
        update = await request.json()
        bot_service = get_bot_service()
        
        success = bot_service.process_update(update)
        
        return {"status": "ok" if success else "error"}
    except Exception as e:
        logger.error(f"Error en webhook de Telegram: {e}")
        return {"status": "error", "message": str(e)}


class SettingsUpdate(BaseModel):
    """Modelo para actualizar la configuración de usuario."""
    notifications_enabled: bool = False
    telegram_chat_id: Optional[str] = None
    notification_level: str = "redacted"


class SettingsResponse(BaseModel):
    """Respuesta con la configuración actual del usuario."""
    notifications_enabled: bool
    telegram_chat_id: Optional[str]
    notification_level: str


@router.get("/settings", response_model=SettingsResponse)
async def get_settings(
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db)
) -> SettingsResponse:
    """Obtiene la configuración actual del usuario."""
    try:
        settings = db.execute(
            select(UserSettings).where(UserSettings.user_id == user_id)
        ).scalar_one_or_none()
        
        if not settings:
            # Crear configuración por defecto si no existe
            settings = UserSettings(
                user_id=user_id,
                notifications_enabled=False,
                notification_level="redacted"
            )
            db.add(settings)
            db.commit()
            db.refresh(settings)
        
        return SettingsResponse(
            notifications_enabled=settings.notifications_enabled,
            telegram_chat_id=settings.telegram_chat_id,
            notification_level=settings.notification_level
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al obtener configuración: {str(e)}"
        )


@router.put("/settings", response_model=SettingsResponse)
async def update_settings(
    settings_update: SettingsUpdate,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db)
) -> SettingsResponse:
    """Actualiza la configuración del usuario."""
    logger.info(f"Actualizando settings para user_id: {user_id}")
    logger.info(f"Settings update: {settings_update}")
    
    try:
        settings = db.execute(
            select(UserSettings).where(UserSettings.user_id == user_id)
        ).scalar_one_or_none()
        
        if not settings:
            # Crear configuración si no existe
            settings = UserSettings(user_id=user_id)
            db.add(settings)
        
        # Actualizar campos
        settings.notifications_enabled = settings_update.notifications_enabled
        settings.telegram_chat_id = (
            settings_update.telegram_chat_id 
            if settings_update.notifications_enabled 
            else None
        )
        settings.notification_level = settings_update.notification_level
        
        db.commit()
        db.refresh(settings)
        
        return SettingsResponse(
            notifications_enabled=settings.notifications_enabled,
            telegram_chat_id=settings.telegram_chat_id,
            notification_level=settings.notification_level
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al actualizar configuración: {str(e)}"
        )


# Rol del usuario (Fase 3: perfilado básico/avanzado)

class UserRoleResponse(BaseModel):
    role: Literal["basic", "advanced"] = Field(
        ..., description="Rol que condiciona el tono y nivel de detalle de las respuestas",
    )


class UserRoleUpdate(BaseModel):
    role: Literal["basic", "advanced"]


def _get_user(db: Session, user_id: str) -> User:
    try:
        uid = uuid.UUID(user_id)
    except (TypeError, ValueError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "user_id no es un UUID válido")
    row = db.execute(select(User).where(User.id == uid)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuario no encontrado")
    return row


@router.get("/role", response_model=UserRoleResponse,
            summary="Devuelve el rol actual del usuario (basic|advanced)")
def get_user_role(
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> UserRoleResponse:
    row = _get_user(db, user_id)
    return UserRoleResponse(role=row.role or "basic")  # type: ignore[arg-type]


@router.put("/role", response_model=UserRoleResponse,
            summary="Actualiza el rol del usuario (basic|advanced)")
def update_user_role(
    body: UserRoleUpdate,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> UserRoleResponse:
    row = _get_user(db, user_id)
    row.role = body.role
    db.flush()
    db.refresh(row)
    logger.info(f"role update: {user_id} → {row.role}")
    return UserRoleResponse(role=row.role)  # type: ignore[arg-type]


@router.post("/test-notification")
async def test_notification(
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """Envía una notificación de prueba al usuario y propaga el error real de Telegram."""
    try:
        user_settings = db.execute(
            select(UserSettings).where(UserSettings.user_id == user_id)
        ).scalar_one_or_none()

        if not user_settings or not user_settings.notifications_enabled:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Las notificaciones no están activadas"
            )

        if not user_settings.telegram_chat_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No hay Chat ID de Telegram configurado"
            )

        config = UserNotificationConfig(
            user_id=user_id,
            notifications_enabled=user_settings.notifications_enabled,
            telegram_chat_id=user_settings.telegram_chat_id,
            notification_level=user_settings.notification_level
        )

        result = notify(config, "register", success=True)

        # Si Telegram falló, propagar el error real al frontend en vez de
        # mentir con 200 OK. 502 indica que un servicio aguas abajo (Telegram)
        # devolvió un error que no podemos resolver desde el servidor.
        if result.get("telegram") != "ok":
            err = result.get("telegram_error") or "Telegram no envió el mensaje (sin detalle)"
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Telegram falló: {err}"
            )

        return {
            "status": "success",
            "message": "Notificación de prueba enviada a Telegram",
            "result": result,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al enviar notificación de prueba: {str(e)}"
        )


@router.get("/telegram-status")
async def telegram_status(
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """Diagnóstico de la configuración de Telegram para este usuario.

    Devuelve:
      - token_configured: si el servidor tiene TELEGRAM_BOT_TOKEN definido.
      - chat_id_saved: el chat_id guardado en user_settings (o None).
      - notifications_enabled: flag del usuario.
      - bot_info: respuesta de getMe (username + first_name del bot real).
        Sirve para que el usuario verifique que el bot al que hizo /start
        coincide con el que está usando el servidor.
      - bot_reachable / error: estado de la conexión con la API de Telegram.
    """
    user_settings = db.execute(
        select(UserSettings).where(UserSettings.user_id == user_id)
    ).scalar_one_or_none()

    result: Dict[str, Any] = {
        "token_configured": bool(app_settings.telegram_bot_token),
        "chat_id_saved": user_settings.telegram_chat_id if user_settings else None,
        "notifications_enabled": bool(user_settings and user_settings.notifications_enabled),
        "bot_info": None,
        "bot_reachable": False,
        "error": None,
    }

    if not app_settings.telegram_bot_token:
        result["error"] = "TELEGRAM_BOT_TOKEN no configurado en el servidor"
        return result

    # getMe vía Telegram API. Usa los mismos parámetros que `send_message`
    # (timeout largo + 2 reintentos ante errores transitorios) porque HF
    # Spaces tiene latencia muy variable hacia api.telegram.org.
    url = f"https://api.telegram.org/bot{app_settings.telegram_bot_token}/getMe"
    last_error: Optional[str] = None
    body: Optional[dict] = None

    for attempt in range(1, 4):
        try:
            r = requests.get(url, timeout=(10, 25))
        except (requests.Timeout, requests.ConnectionError) as e:
            last_error = f"{type(e).__name__}: {e}"
            logger.warning(f"getMe intento {attempt}/3 falló: {last_error}")
            if attempt < 3:
                import time as _time
                _time.sleep(2 ** (attempt - 1))
            continue
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            break

        if r.status_code == 200:
            try:
                body = r.json()
            except Exception as e:
                last_error = f"JSON decode error: {e}"
                break
            if body.get("ok"):
                bot = body.get("result", {})
                result["bot_reachable"] = True
                result["bot_info"] = {
                    "id": bot.get("id"),
                    "username": bot.get("username"),
                    "first_name": bot.get("first_name"),
                }
                return result
            last_error = f"getMe respondió ok=false: {body}"
            break

        try:
            desc = r.json().get("description") or r.text
        except Exception:
            desc = r.text
        last_error = f"getMe HTTP {r.status_code}: {desc}"
        # 5xx merece reintento; 4xx es permanente.
        if r.status_code < 500:
            break
        if attempt < 3:
            import time as _time
            _time.sleep(2 ** (attempt - 1))

    result["error"] = last_error
    return result

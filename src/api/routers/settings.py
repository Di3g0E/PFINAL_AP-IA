"""
Endpoints para la configuración de usuario y notificaciones.
"""

from __future__ import annotations

from typing import Any, Dict, Optional
import logging

from fastapi import APIRouter, Depends, HTTPException, status, Request
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.api.dependencies import get_current_user_id
from src.data.database import get_db
from src.data.schema import UserSettings
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


@router.post("/test-notification")
async def test_notification(
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """Envía una notificación de prueba al usuario."""
    try:
        settings = db.execute(
            select(UserSettings).where(UserSettings.user_id == user_id)
        ).scalar_one_or_none()
        
        if not settings or not settings.notifications_enabled:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Las notificaciones no están activadas"
            )
        
        if not settings.telegram_chat_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No hay Chat ID de Telegram configurado"
            )
        
        # Crear configuración de notificación
        config = UserNotificationConfig(
            user_id=user_id,
            notifications_enabled=settings.notifications_enabled,
            telegram_chat_id=settings.telegram_chat_id,
            notification_level=settings.notification_level
        )
        
        # Enviar notificación de prueba
        notify(config, "register", success=True)
        
        return {
            "status": "success",
            "message": "Notificación de prueba enviada a Telegram"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al enviar notificación de prueba: {str(e)}"
        )

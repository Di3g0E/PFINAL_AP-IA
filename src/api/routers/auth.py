"""
Endpoints de autenticación biométrica.

`POST /auth/register` y `POST /auth/login` usan **multipart/form-data**
porque incluyen una imagen facial. Internamente delegan al agente Security
(`src/agents/security/agent.py`).

Ejemplo curl:
    curl -X POST http://localhost:8000/auth/register \
      -F "email=alice@p6.local" -F "passphrase=hunter2" \
      -F "biometric_consent=true" -F "face=@mi_foto.jpg"

Devuelve un JWT que el cliente debe usar como `Authorization: Bearer <token>`
en los endpoints protegidos.
"""

from __future__ import annotations
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel

from src.agents.contracts import LoginRequest, RegisterRequest
from src.agents.security import agent as security
from src.api.dependencies import create_access_token


router = APIRouter(prefix="/auth", tags=["auth"])


class TokenResponse(BaseModel):
    """Respuesta estándar de los endpoints de auth: JWT + metadatos."""
    access_token: str
    token_type: str = "bearer"
    user_id: str
    similarity: float | None = None
    liveness_score: float | None = None


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Alta de usuario con biometría",
)
async def register(
    email: str = Form(..., description="Email único del usuario"),
    passphrase: str = Form(..., min_length=6, description="Contraseña ≥6 caracteres"),
    biometric_consent: bool = Form(
        ..., description="RGPD: consentimiento explícito al tratamiento biométrico",
    ),
    face: UploadFile = File(..., description="Imagen facial (JPEG/PNG)"),
    notifications_enabled: bool = Form(False, description="Notificaciones Telegram activadas"),
    telegram_chat_id: Optional[str] = Form(None, description="Chat ID de Telegram"),
) -> TokenResponse:
    image_bytes = await face.read()
    if not image_bytes:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Imagen vacía")

    req = RegisterRequest(
        email=email,
        passphrase=passphrase,
        face_image=image_bytes,
        biometric_consent=biometric_consent,
        notifications_enabled=notifications_enabled,
        telegram_chat_id=telegram_chat_id,
    )
    verdict = security.register_user(req)
    if verdict.decision != "allow" or not verdict.user_id:
        # 400 si fue por consent/liveness/duplicado, 500 si error inesperado
        raise HTTPException(status.HTTP_400_BAD_REQUEST, verdict.reason)

    return TokenResponse(
        access_token=create_access_token(verdict.user_id),
        user_id=verdict.user_id,
        liveness_score=verdict.liveness_score,
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login con passphrase + biometría (foto o vídeo)",
)
async def login(
    email: str = Form(...),
    passphrase: str = Form(...),
    face: UploadFile = File(..., description="Imagen facial (JPEG/PNG)"),
    face_video: Optional[UploadFile] = File(
        None,
        description=(
            "Vídeo facial (WebM/MP4, 3-5s). Si presente y "
            "SECURITY_VIDEO_ENABLED=True, se usa en lugar de 'face' "
            "para extraer embeddings promediados de múltiples frames (E3)."
        ),
    ),
) -> TokenResponse:
    image_bytes = await face.read()
    if not image_bytes:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Imagen vacía")

    video_bytes: Optional[bytes] = None
    if face_video is not None:
        video_bytes = await face_video.read()
        if not video_bytes:
            video_bytes = None  # Tratar como si no se hubiera enviado

    req = LoginRequest(
        email=email,
        passphrase=passphrase,
        face_image=image_bytes,
        face_video=video_bytes,
    )
    verdict = security.login_user(req)
    if verdict.decision != "allow" or not verdict.user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, verdict.reason)

    return TokenResponse(
        access_token=create_access_token(verdict.user_id),
        user_id=verdict.user_id,
        similarity=verdict.similarity,
        liveness_score=verdict.liveness_score,
    )


class AdminLoginRequest(BaseModel):
    """Body de `/auth/login-admin`: solo email + passphrase (sin biometría)."""
    email: str
    passphrase: str


@router.post(
    "/login-admin",
    response_model=TokenResponse,
    summary="Login para cuentas operacionales (passphrase only, sin biometría)",
    description=(
        "Solo válido para usuarios con `users.is_admin = True`. Pensado como "
        "cuenta de recovery/operación. Si el usuario no es admin o la "
        "passphrase falla, devuelve 401 con el mismo mensaje genérico que "
        "el login normal para no filtrar privilegios."
    ),
)
async def login_admin(body: AdminLoginRequest) -> TokenResponse:
    verdict = security.login_admin(body.email, body.passphrase)
    if verdict.decision != "allow" or not verdict.user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, verdict.reason)
    return TokenResponse(
        access_token=create_access_token(verdict.user_id),
        user_id=verdict.user_id,
    )

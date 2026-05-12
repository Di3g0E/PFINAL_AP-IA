"""
Aplicación FastAPI del sistema multiagente P6.

Arranque:
    uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload

Endpoints expuestos en v1:
  - POST   /auth/register
  - POST   /auth/login
  - POST   /chat
  - POST   /transactions
  - POST   /transactions/ocr-extract
  - GET    /transactions/pending
  - POST   /transactions/pending/{id}/confirm
  - DELETE /transactions/pending/{id}
  - GET    /            (healthcheck)

Documentación interactiva: http://localhost:8000/docs (Swagger UI).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from src.api.routers import auth, chat, transactions, settings as settings_router
from src.utils.config import settings
from src.utils.logging_config import configure_logging


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Configura logging al arrancar y limpia recursos al cerrar."""
    configure_logging()
    logger.info("FastAPI lifespan: arrancando")
    yield
    logger.info("FastAPI lifespan: cerrando")


app = FastAPI(
    title="P6_AP-IA — Sistema multiagente financiero",
    version="0.8.0",
    description=(
        "API REST del sistema multiagente. Cuatro agentes (Orchestrator + "
        "Security + Registrar + Analyst) sobre LangGraph. Autenticación con "
        "passphrase + biometría facial. RGPD opt-in en notificaciones."
    ),
    lifespan=lifespan,
)

# CORS para que el frontend (Next.js) pueda consumir la API en desarrollo.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(transactions.router)
app.include_router(settings_router.router)

# Endpoint público para webhook de Telegram (no requiere autenticación)
app.include_router(settings_router.router, prefix="/telegram", tags=["telegram"])


@app.get("/", tags=["health"], summary="Healthcheck")
def root() -> dict:
    """Endpoint mínimo de comprobación. No requiere auth."""
    return {
        "name": "P6_AP-IA",
        "version": app.version,
        "status": "ok",
    }

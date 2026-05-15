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
from src.api.routers.modules import p1 as module_p1, p2 as module_p2, p3 as module_p3, p4 as module_p4, p5 as module_p5
from src.utils.config import settings
from src.utils.langfuse_integration import init_langfuse, shutdown_langfuse
from src.utils.logging_config import configure_logging


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Configura logging al arrancar y limpia recursos al cerrar.

    Llama a `init_db()` (idempotente) para que las tablas nuevas declaradas
    en `schema.py` se creen automáticamente en el siguiente deploy. SQLAlchemy
    `create_all` no toca tablas existentes, así que es seguro reejecutarlo.
    """
    configure_logging()
    init_langfuse()
    logger.info("FastAPI lifespan: arrancando")
    try:
        from src.data.database import init_db, is_database_configured
        if is_database_configured():
            init_db()
            logger.info("Esquema de BD verificado / creado")
        else:
            logger.warning("DATABASE_URL no configurada; saltando init_db")
    except Exception as e:
        logger.exception(f"init_db falló al arrancar: {e}")
    yield
    shutdown_langfuse()
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

# P1-P5 expuestos como microservicios REST (Fase 2 del enunciado).
app.include_router(module_p1.router)
app.include_router(module_p2.router)
app.include_router(module_p3.router)
app.include_router(module_p4.router)
app.include_router(module_p5.router)


@app.get("/", tags=["health"], summary="Healthcheck")
def root() -> dict:
    """Endpoint mínimo de comprobación. No requiere auth."""
    return {
        "name": "P6_AP-IA",
        "version": app.version,
        "status": "ok",
    }

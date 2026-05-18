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

# IMPORTANTE: este `os.environ[...]` DEBE ejecutarse antes de cualquier import
# que cargue paddle/opentelemetry/protobuf. La instalación de langfuse v4
# arrastró protobuf 6.x, incompatible con los _pb2.py de PaddleOCR 2.8 que
# fueron generados con protoc 3.x ("Descriptors cannot be created directly").
# Forzar el binding pure-Python de protobuf acepta ambos formatos. Es más
# lento pero la carga de OCR es esporádica y no crítica en latencia.
import os
os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")

# Pre-carga torch en el hilo principal del worker. En Windows + OneDrive,
# la inicialización de `torch` (carga de shm.dll y dependencias) falla con
# `OSError [WinError 127]` si ocurre por primera vez en un thread auxiliar
# (p. ej. el AnyIO worker que sirve `/chat` → `get_llm` → `langchain_groq`
# → `langchain_core.language_models.base` → `from transformers import ...`
# → `import torch`). Importarlo aquí, al cargar el módulo del worker en su
# hilo principal, "fija" los DLLs en `sys.modules` y los reimports
# posteriores desde threads se resuelven por caché sin volver a tocar el FS.
try:
    import torch  # noqa: F401
except Exception:
    # Si torch no está instalado o falla, lo dejamos para que falle de forma
    # localizada en el sitio que de verdad lo necesita.
    pass

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from src.api.routers import auth, chat, monitor as monitor_router, transactions, settings as settings_router
from src.api.routers import admin_langfuse, langfuse
from src.api.routers.modules import p1 as module_p1, p2 as module_p2, p3 as module_p3, p4 as module_p4, p5 as module_p5
from src.utils.config import settings
from src.utils.langfuse_integration import init_langfuse, shutdown_langfuse
from src.utils.logging_config import configure_logging, log_event


_MONITOR_INTERVAL_SECONDS = 300  # 5 min entre snapshots del MonitorAgent


async def _monitor_loop() -> None:
    """Background task: el MonitorAgent evalúa el sistema cada N segundos.

    Loguea un resumen (que también se persiste en `events` como un evento
    `agent='monitor'`) sin bloquear el event loop principal. Si la BD no
    está disponible o la evaluación falla, el monitor lo deja constancia y
    sigue con el siguiente tick.
    """
    from src.agents.monitor import MonitorAgent
    from src.utils.logging_config import log_event

    monitor = MonitorAgent(window_minutes=60)
    while True:
        try:
            report = await asyncio.to_thread(monitor.evaluate)
            log_event(
                agent="monitor", action="snapshot",
                status="ok" if report.db_available else "warning",
                payload={
                    "health": report.health,
                    "error_rate": report.error_rate,
                    "total_events": report.total_events,
                    "errors": report.error_count,
                    "active_sessions": report.active_sessions,
                    "active_users": report.active_users,
                },
            )
        except Exception as e:
            logger.warning(f"monitor_loop: tick falló: {e}")
        try:
            await asyncio.sleep(_MONITOR_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            break


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Configura logging al arrancar y limpia recursos al cerrar.

    Llama a `init_db()` (idempotente) para que las tablas nuevas declaradas
    en `schema.py` se creen automáticamente en el siguiente deploy. SQLAlchemy
    `create_all` no toca tablas existentes, así que es seguro reejecutarlo.

    Arranca el `monitor_loop` como background task: cada 5 minutos el
    MonitorAgent evalúa la salud del sistema y registra un evento
    `agent=monitor`/`action=snapshot`.
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

    monitor_task = asyncio.create_task(_monitor_loop(), name="monitor_loop")
    logger.info("Monitor agent: background loop arrancado (intervalo 5 min)")

    # Warm-up SOLO de PaddleOCR. El primer /transactions/ocr-extract hace
    # cold-start (~5-10 s descargando modelos la primera vez), lo
    # precargamos sin bloquear el arranque del servidor.
    #
    # ¡No precargamos el HybridClassifier aquí! En Windows + OneDrive
    # `sentence-transformers → torch` falla al cargar `shm.dll` cuando
    # se inicializa en un thread auxiliar (`[WinError 127]`), y eso
    # envenena el módulo torch para el resto del proceso, rompiendo el
    # nodo orchestrator.route del grafo. El HybridClassifier se carga
    # lazy en la primera request real, donde sí tiene el escudo
    # `_HYBRID_DISABLED` para caer al clasificador legacy si falla.
    async def _warm_up() -> None:
        try:
            def _do_warm_up():
                from src.agents.registrar.ocr_engine_eur import EnrichedOCRExtractor
                EnrichedOCRExtractor.shared()._base._ensure_ocr()
            await asyncio.to_thread(_do_warm_up)
            logger.info("Warm-up: PaddleOCR listo.")
        except Exception as e:
            logger.warning(f"Warm-up de OCR falló (se hará lazy en la 1ª request): {e}")
    asyncio.create_task(_warm_up(), name="warm_up")

    yield

    monitor_task.cancel()
    try:
        await monitor_task
    except (asyncio.CancelledError, Exception):
        pass
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
# Compose a robust allowlist for local development (covers localhost and 127.0.0.1)
local_dev_origins = [
    "http://localhost:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:3001",
]
allow_origins_list = list(dict.fromkeys(settings.cors_origins_list + local_dev_origins))

# Development fallback: allow any origin but do NOT allow credentials
# (credentials + "*" is rejected by Starlette). This is safe for local dev
# where the frontend uses Authorization headers rather than cookies.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(transactions.router)
app.include_router(settings_router.router)
# Agente Monitor (Punto 4 del enunciado).
app.include_router(monitor_router.router)

# P1-P5 expuestos como microservicios REST (Fase 2 del enunciado).
app.include_router(module_p1.router)
app.include_router(module_p2.router)
app.include_router(module_p3.router)
app.include_router(module_p4.router)
app.include_router(module_p5.router)
app.include_router(langfuse.router)
app.include_router(admin_langfuse.router)


@app.get("/", tags=["health"], summary="Healthcheck")
def root() -> dict:
    """Endpoint mínimo de comprobación. No requiere auth."""
    return {
        "name": "P6_AP-IA",
        "version": app.version,
        "status": "ok",
    }

"""
Configuración centralizada vía Pydantic Settings.

Lee variables de entorno (o .env) y las expone como atributos tipados.
Uso: `from src.utils.config import settings`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_SQLITE_DEFAULT = "sqlite:///./data/p6.db"
_VALID_DB_PREFIXES = (
    "postgresql://", "postgresql+psycopg://", "postgresql+asyncpg://",
    "sqlite://", "sqlite:///",
)


# UUID fijo del usuario "demo" creado por scripts/init_db.py.
# Se expone como string para que encaje en `OrchestratorState.user_id` y
# `TransactionRecord.user_id` (ambos `str`); las capas BD lo convertirán.
DEMO_USER_ID: str = "00000000-0000-0000-0000-000000000001"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8",
                                      extra="ignore", case_sensitive=False)

    # Database — SQLite por defecto en dev (cero setup); Postgres en producción.
    # El validador de abajo sustituye URLs inválidas/placeholder por SQLite.
    database_url: str = _SQLITE_DEFAULT

    @field_validator("database_url", mode="before")
    @classmethod
    def _coerce_database_url(cls, v):
        """Si la URL está vacía o es '...' (placeholder), cae a SQLite local."""
        if not isinstance(v, str):
            return _SQLITE_DEFAULT
        v = v.strip()
        if not v or v == "..." or v.endswith("://"):
            return _SQLITE_DEFAULT
        if not v.startswith(_VALID_DB_PREFIXES):
            return _SQLITE_DEFAULT
        return v

    # Cifrado
    master_fernet_key: str = ""
    embedding_store_passphrase: str = "change-me-please"

    # LLM defaults (Groq compartido)
    groq_api_key: str = ""
    groq_default_model: str = "llama-3.3-70b-versatile"

    # Notifications (Telegram bot del servidor)
    telegram_bot_token: str = ""

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: str = "http://localhost:3000"

    # URL base que los tools del agente usan para hablar con los módulos
    # P1-P5 vía REST. En despliegues monoproceso (HF Space, Render, local)
    # apunta a localhost; en multi-contenedor se puede cambiar al servicio.
    internal_api_base_url: str = "http://localhost:8000"

    # JWT
    jwt_secret: str = "change-me-please"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60

    # Biometría — umbrales y modelo del agente Security.
    #
    # NOTA: el threshold sano depende de si hay un modelo de liveness
    # fine-tuneado en `liveness_model_path`:
    #   - SIN modelo (solo ImageNet): scores ruidosos → 0.30-0.50.
    #   - CON modelo fine-tuneado (NUAA/CASIA-FASD/Replay-Attack):
    #     subir a 0.95 (default conservador de P5).
    liveness_threshold: float = 0.50
    face_similarity_threshold: float = 0.60

    # Ruta opcional al checkpoint .pth con la cabeza binaria entrenada del
    # DenseNet201. Si el fichero existe, se cargan sus pesos en `extract()`;
    # si no, el modelo usa solo los pesos ImageNet (modo desarrollo).
    # Relativa al `project_root` (raíz del repo).
    liveness_model_path: str = "models/liveness_kaggle.pth"

    # Logging
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "human"] = "json"
    log_file: str = "logs/app.log"

    # Langfuse (monitorización humana de los agentes — Fase 6).
    # Si los keys están vacíos, no se trazea nada (degrada elegante,
    # no rompe). Crear cuenta gratuita en https://cloud.langfuse.com
    # y pegar las keys aquí o en el cloud secret manager.
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    @property
    def langfuse_enabled(self) -> bool:
        """True si Langfuse está configurado y se puede trazar."""
        return bool(self.langfuse_public_key and self.langfuse_secret_key)

    # Paths derivadas
    project_root: Path = Path(__file__).resolve().parents[2]

    @property
    def models_dir(self) -> Path:
        return self.project_root / "models"

    @property
    def data_dir(self) -> Path:
        return self.project_root / "data"

    @property
    def logs_dir(self) -> Path:
        return self.project_root / "logs"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()

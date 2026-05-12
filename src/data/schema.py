"""
Modelos SQLAlchemy del esquema de base de datos.

Tipos portables (Postgres + SQLite):
  - `Uuid` (genérico) en vez de `dialects.postgresql.UUID`
  - `JSON` en vez de `ARRAY(String)` y `JSONB`

Tablas:
  - users:           identidad y credencial (passphrase hash + salt)
  - user_settings:   LLM provider/key cifrada, canales de notificación
  - transactions:    ingresos y gastos (esquema unificado del CSV de P5)
  - goals:           objetivos de gasto por categoría
  - events:          log estructurado para auditoría y futura observabilidad

Notas:
  - Datos financieros NO cifrados a nivel app: confiamos en TDE del proveedor
    + control por user_id. Embeddings biométricos sí cifrados (gestionado por
    `EncryptedEmbeddingStore` en `src/utils/security.py`).
  - El estado de LangGraph (memoria de chat) NO se modela aquí: lo gestiona
    `langgraph.checkpoint.postgres.PostgresSaver` con sus propias tablas
    cuando se conecte a Postgres en producción.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    JSON, BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey,
    Integer, Numeric, String, Text, Uuid, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    passphrase_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    biometric_consent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    biometric_consent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    settings: Mapped["UserSettings"] = relationship(back_populates="user",
                                                    uselist=False, cascade="all, delete-orphan")
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="user",
                                                             cascade="all, delete-orphan")
    goals: Mapped[list["Goal"]] = relationship(back_populates="user",
                                               cascade="all, delete-orphan")


class UserSettings(Base):
    __tablename__ = "user_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid,
                                               ForeignKey("users.id", ondelete="CASCADE"),
                                               primary_key=True)

    # LLM (defecto Groq compartido si no hay API key)
    llm_provider: Mapped[Optional[str]] = mapped_column(String(32))      # 'groq'|'openai'|'anthropic'|'google'
    llm_model: Mapped[Optional[str]] = mapped_column(String(64))
    llm_api_key_encrypted: Mapped[Optional[str]] = mapped_column(Text)   # Fernet (clave maestra del servidor)

    # Notificaciones (configuradas por el usuario, RGPD opt-in)
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notification_level: Mapped[str] = mapped_column(String(16), nullable=False,
                                                    default="redacted")   # 'redacted'|'full'
    telegram_chat_id: Mapped[Optional[str]] = mapped_column(String(64))
    whatsapp_phone: Mapped[Optional[str]] = mapped_column(String(32))    # +34600000000

    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="settings")

    __table_args__ = (
        CheckConstraint("notification_level IN ('redacted', 'full')",
                        name="ck_notification_level"),
        CheckConstraint("llm_provider IS NULL OR llm_provider IN "
                        "('groq', 'openai', 'anthropic', 'google')",
                        name="ck_llm_provider"),
    )


class Transaction(Base):
    """Esquema unificado del CSV de P5 (Description, Date, Amount, Area, Type)."""
    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid,
                                               ForeignKey("users.id", ondelete="CASCADE"),
                                               nullable=False, index=True)

    description: Mapped[str] = mapped_column(Text, nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EUR")
    # Multilabel: lista de categorías serializada como JSON. Postgres usa JSONB,
    # SQLite usa TEXT — SQLAlchemy hace la conversión transparente.
    area: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)            # 'Income'|'Expenses'
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    # Status del ciclo de vida:
    #   'accepted' — visible en analytics, contabilizada normalmente.
    #   'pending'  — Security la marcó anómala; espera confirmación del usuario.
    #   'rejected' — el usuario la rechazó (se mantiene como traza, no se cuenta).
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="accepted")
    # Razones de anomalía (cuando status='pending') — JSON list de strings.
    anomaly_reasons: Mapped[Optional[list]] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="transactions")

    __table_args__ = (
        CheckConstraint("type IN ('Income', 'Expenses')", name="ck_transaction_type"),
        CheckConstraint("source IN ('manual', 'ocr', 'import')", name="ck_transaction_source"),
        CheckConstraint("status IN ('accepted', 'pending', 'rejected')",
                        name="ck_transaction_status"),
    )


class Goal(Base):
    """Objetivos de gasto máximo por categoría (origen P4)."""
    __tablename__ = "goals"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid,
                                               ForeignKey("users.id", ondelete="CASCADE"),
                                               nullable=False, index=True)
    area: Mapped[str] = mapped_column(String(64), nullable=False)
    max_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    period: Mapped[str] = mapped_column(String(16), nullable=False, default="monthly")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="goals")

    __table_args__ = (
        CheckConstraint("period IN ('monthly', 'weekly')", name="ck_goal_period"),
    )


class Event(Base):
    """
    Log estructurado de eventos (v1 simple, v2 evolucionará).

    Reglas:
      - Sin PII en claro: ni amount ni description aquí (sólo hashes/IDs).
      - Una fila por evento relevante; los DEBUG van solo a fichero.
      - Política de retención: 90 días (purga periódica fuera de este modelo).
    """
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                         server_default=func.now(), index=True)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, index=True)
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    agent: Mapped[str] = mapped_column(String(32), nullable=False)         # 'orchestrator'|'security'|'registrar'|'analyst'|'api'
    action: Mapped[str] = mapped_column(String(64), nullable=False)        # 'login_attempt'|'transaction_added'|...
    status: Mapped[str] = mapped_column(String(16), nullable=False)        # 'ok'|'error'|'denied'
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer)
    payload: Mapped[Optional[dict]] = mapped_column(JSON)                  # metadatos sin PII

"""
Contratos compartidos entre el Orquestador y los sub-agentes.

Estas clases son la API interna del sistema multiagente: cada sub-agente
recibe un Request tipado y devuelve un Verdict/Result/Report. El Orquestador
es el ÚNICO componente que produce texto para el usuario.

Documento de referencia: doc/agent_contracts.md
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# Tipos compartidos

TransactionType = Literal["Income", "Expenses"]
TransactionSource = Literal["manual", "ocr", "import"]
SecurityDecision = Literal["allow", "deny", "challenge"]
GoalPeriod = Literal["monthly", "weekly"]
ReportType = Literal["summary", "trend", "prediction", "goal_status",
                     "anomaly", "recurring", "category", "savings_rate"]
NotificationLevel = Literal["redacted", "full"]


# Transacción canónica del sistema

class TransactionDraft(BaseModel):
    """Transacción candidata a persistir; aún no validada por Security."""
    user_id: str
    description: str
    date: date
    amount: Decimal
    area: list[str] = Field(default_factory=list)
    type: TransactionType
    source: TransactionSource = "manual"
    currency: str = "EUR"


class TransactionRecord(TransactionDraft):
    """Transacción ya persistida en la BD."""
    id: str
    created_at: datetime
    status: Literal["accepted", "pending", "rejected"] = "accepted"
    anomaly_reasons: list[str] = Field(default_factory=list)


# Agente Security

class RegisterRequest(BaseModel):
    email: str
    passphrase: str
    face_image: bytes
    biometric_consent: bool = False
    notifications_enabled: bool = False
    telegram_chat_id: Optional[str] = None


class LoginRequest(BaseModel):
    email: str
    passphrase: str
    face_image: bytes


class SecurityVerdict(BaseModel):
    """Resultado de cualquier operación del agente Security."""
    decision: SecurityDecision
    user_id: Optional[str] = None
    reason: str
    anomaly_reasons: list[str] = Field(default_factory=list)
    similarity: Optional[float] = None
    liveness_score: Optional[float] = None
    locked_until: Optional[datetime] = None


# Agente Registrar

class ManualEntry(BaseModel):
    user_id: str
    description: str
    date: date
    amount: Decimal
    type: TransactionType
    area: Optional[list[str]] = None  # None → autoclasificar con P2


class ImageUpload(BaseModel):
    user_id: str
    image: bytes
    description_hint: Optional[str] = None
    date_hint: Optional[date] = None


class ReviewItem(BaseModel):
    """
    Transacción persistida con `status='pending'`, esperando confirmación
    o rechazo del usuario.

    El `record` ya está en la BD: el usuario puede confirmarla por id
    (status → 'accepted') o rechazarla (se elimina/marca rejected).
    """
    record: TransactionRecord
    anomaly_reasons: list[str]


class RejectedItem(BaseModel):
    """Transacción rechazada (OCR fallido, input inválido, etc.)."""
    reason: str
    raw_input: dict = Field(default_factory=dict)


class RegistryResult(BaseModel):
    accepted: list[TransactionRecord] = Field(default_factory=list)
    pending_review: list[ReviewItem] = Field(default_factory=list)
    rejected: list[RejectedItem] = Field(default_factory=list)


class ExtractedTransaction(BaseModel):
    """
    Borrador extraído por OCR pero **sin persistir**: el usuario lo revisa
    y confirma/edita antes de que se cree la transacción real.
    """
    amount: Decimal
    description_suggested: str
    date_suggested: date
    area_suggested: list[str] = Field(default_factory=list)
    type_suggested: TransactionType = "Expenses"
    currency: str = "EUR"


class OCRExtractResult(BaseModel):
    """Resultado de un intento de extracción OCR."""
    extracted: Optional[ExtractedTransaction] = None
    reason: Optional[str] = None  # mensaje si extracted is None


# Agente Analyst

class DataPoint(BaseModel):
    """Punto temporal para series (ej. tendencias mensuales)."""
    label: str        # 'YYYY-MM', categoría, etc.
    value: float


class GoalAlert(BaseModel):
    goal_id: str
    area: str
    current: Decimal
    limit: Decimal
    pct: float        # 0.0-1.5
    severity: Literal["info", "warning", "critical"]


class Goal(BaseModel):
    id: Optional[str] = None     # None hasta persistir
    user_id: str
    area: str
    max_amount: Decimal
    period: GoalPeriod = "monthly"
    active: bool = True


class AnalysisReport(BaseModel):
    """Salida estructurada del Analyst: el Orquestador la narra al usuario."""
    type: ReportType
    period: Optional[str] = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    series: list[DataPoint] = Field(default_factory=list)
    goal_alerts: list[GoalAlert] = Field(default_factory=list)
    # Preferencia de tipo de gráfico que el usuario haya indicado por chat
    # ("como barras", "en pie", "sin gráfico"). Si es None, `chat.py` elige
    # el tipo por defecto según `type` del reporte (cf. _default_chart_type).
    chart_type: Optional[Literal["line", "bar", "pie", "none"]] = None

    model_config = ConfigDict(arbitrary_types_allowed=True)


class ChartSpec(BaseModel):
    """Especificación de un gráfico para renderizar en el frontend.

    El backend la genera junto con la respuesta del chat cuando el
    `AnalysisReport` tiene `series` no vacío. El frontend (Recharts) la
    consume y dibuja el gráfico encima de la respuesta textual.
    """
    type: Literal["line", "bar", "pie", "area"]
    title: str
    data: list[dict[str, Any]] = Field(default_factory=list,
                                       description="Lista de {label, value}.")
    explanation: str = Field(
        default="",
        description=("Explicación breve (XAI) de lo que el gráfico muestra. "
                     "Acompaña al gráfico para que el usuario entienda QUÉ "
                     "está viendo además de cómo se compara."),
    )


# Agente Orquestador

class OrchestratorDecision(BaseModel):
    """Decisión de routing del LLM Orquestador para el siguiente paso del grafo."""
    action: Literal["delegate_security", "delegate_registrar",
                    "delegate_analyst", "delegate_conversational",
                    "ask_user", "respond_final"] = Field(
        description=("Sub-agente al que delegar, o si responder/preguntar al usuario "
                     "directamente. Usa 'delegate_analyst' para preguntas de "
                     "análisis financiero, 'delegate_registrar' para alta de "
                     "transacciones, 'delegate_conversational' para small-talk "
                     "(saludos, gracias, preguntas sobre el sistema). "
                     "Usa 'ask_user' para pedir aclaración. "
                     "Usa 'respond_final' SOLO si NO hace falta consultar ningún sub-agente.")
    )
    target_op: Optional[str] = Field(
        default=None,
        description=("Nombre de la operación dentro del sub-agente (p. ej. "
                     "'monthly_summary' o 'spending_trends'). Obligatorio cuando "
                     "action empieza por 'delegate_'.")
    )
    target_args: dict[str, Any] = Field(
        default_factory=dict,
        description="Argumentos kwargs para la operación del sub-agente."
    )
    user_message: Optional[str] = Field(
        default=None,
        description=("Texto en español para el usuario. Rellénalo SOLO cuando "
                     "action sea 'ask_user' o 'respond_final'. Vacío en delegaciones.")
    )
    reasoning: Optional[str] = Field(
        default=None,
        description="Justificación breve de por qué se eligió esta acción (debug)."
    )


class PendingAction(BaseModel):
    """Acción que el usuario quiere ejecutar pero aún no se ha completado."""
    op: str
    args: dict[str, Any] = Field(default_factory=dict)

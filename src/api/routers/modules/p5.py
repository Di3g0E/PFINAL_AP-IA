"""
P5 — Seguridad: anti-anomalía financiera + lockout.

Expone como microservicio:
  - `validate_transaction`: usado internamente por el Registrar y ahora
    accesible como REST para que cualquier agente lo invoque como tool.
  - `lockout-status`: estado del controlador de intentos del usuario.

Los flujos completos de registro/login biométrico siguen viviendo en
`/auth/register` y `/auth/login` (no se duplican aquí: requieren multipart
con foto + passphrase + consentimiento RGPD, no encaja con un microservicio
generic).
"""

from __future__ import annotations

from datetime import date as dt_date, datetime
from decimal import Decimal
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from pydantic import BaseModel, Field

from src.agents.contracts import TransactionDraft
from src.agents.security import agent as security_agent
from src.api.dependencies import get_current_user_id


router = APIRouter(prefix="/modules/p5", tags=["modules:P5 (security)"])


class ValidateTransactionRequest(BaseModel):
    description: str = Field(..., min_length=1, max_length=500)
    date: dt_date
    amount: Decimal = Field(..., gt=0)
    area: list[str] = Field(default_factory=list,
                            description="Categorías de la transacción.")
    type: Literal["Income", "Expenses"]
    source: Literal["manual", "ocr", "import"] = "manual"
    currency: str = "EUR"


class SecurityVerdictOut(BaseModel):
    decision: Literal["allow", "deny", "challenge"]
    reason: str
    anomaly_reasons: list[str] = []
    similarity: Optional[float] = None
    liveness_score: Optional[float] = None
    locked_until: Optional[datetime] = None
    user_id: Optional[str] = None


@router.post(
    "/validate-transaction",
    response_model=SecurityVerdictOut,
    summary="Valida una transacción candidata contra el histórico (anti-anomalía)",
)
def validate_transaction(
    body: ValidateTransactionRequest,
    user_id: str = Depends(get_current_user_id),
) -> SecurityVerdictOut:
    try:
        draft = TransactionDraft(
            user_id=user_id,
            description=body.description,
            date=body.date,
            amount=body.amount,
            area=list(body.area),
            type=body.type,
            source=body.source,
            currency=body.currency,
        )
        verdict = security_agent.validate_transaction(draft)
    except Exception as e:
        logger.exception(f"P5.validate_transaction falló: {e}")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Error validando: {type(e).__name__}",
        ) from e

    return SecurityVerdictOut(
        decision=verdict.decision,
        reason=verdict.reason,
        anomaly_reasons=verdict.anomaly_reasons,
        similarity=verdict.similarity,
        liveness_score=verdict.liveness_score,
        locked_until=verdict.locked_until,
        user_id=verdict.user_id,
    )


class LockoutStatusOut(BaseModel):
    user_id: str
    locked: bool
    locked_until: Optional[datetime] = None


@router.get(
    "/lockout-status",
    response_model=LockoutStatusOut,
    summary="Indica si el usuario está bloqueado tras varios intentos fallidos",
)
def lockout_status(
    user_id: str = Depends(get_current_user_id),
) -> LockoutStatusOut:
    locked = security_agent._ACCESS_CONTROLLER.is_locked(user_id)
    until = getattr(
        security_agent._ACCESS_CONTROLLER, "locked_until", lambda _u: None,
    )(user_id) if locked else None
    return LockoutStatusOut(user_id=user_id, locked=locked, locked_until=until)

"""
Endpoints REST de transacciones para clientes que prefieren acceso directo
en vez de pasar por el chat.

  - POST   /transactions               — alta manual.
  - GET    /transactions/pending       — lista las marcadas para revisión.
  - POST   /transactions/pending/{id}/confirm — aprueba una pendiente.
  - DELETE /transactions/pending/{id}  — rechaza una pendiente.

Todos requieren `Authorization: Bearer <token>`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from src.agents.contracts import ImageUpload, ManualEntry
from src.agents.registrar import agent as registrar
from src.api.dependencies import get_current_user_id


_ALLOWED_IMAGE_MIME = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}
_MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB


router = APIRouter(prefix="/transactions", tags=["transactions"])


# Schemas de respuesta

class TransactionRecordOut(BaseModel):
    id: str
    description: str
    date: date
    amount: Decimal
    currency: str
    area: list[str]
    type: Literal["Income", "Expenses"]
    source: str
    status: Literal["accepted", "pending", "rejected"]


class PendingReviewOut(BaseModel):
    record: TransactionRecordOut
    anomaly_reasons: list[str]


class ManualTransactionRequest(BaseModel):
    description: str = Field(..., min_length=1)
    date: date
    amount: Decimal
    type: Literal["Income", "Expenses"]
    area: Optional[list[str]] = Field(
        None,
        description="Lista opcional de categorías. Si se omite, el clasificador la inferirá.",
    )


class ManualTransactionResponse(BaseModel):
    accepted: list[TransactionRecordOut] = []
    pending_review: list[PendingReviewOut] = []
    rejected: list[dict] = []


class CategoryStat(BaseModel):
    """Una categoría usada por el usuario y cuántas veces aparece."""
    name: str
    count: int


class UserCategoriesResponse(BaseModel):
    """Categorías que el usuario ya tiene en sus transacciones, agrupadas por tipo."""
    income: list[CategoryStat]
    expenses: list[CategoryStat]


class OCRExtractedOut(BaseModel):
    """Borrador extraído por OCR antes de la confirmación del usuario."""
    amount: Decimal
    description_suggested: str
    date_suggested: date
    area_suggested: list[str]
    type_suggested: Literal["Income", "Expenses"]
    currency: str


# Helpers para serializar TransactionRecord (Pydantic) → dict de salida

def _record_to_out(record) -> TransactionRecordOut:
    return TransactionRecordOut(
        id=record.id,
        description=record.description,
        date=record.date,
        amount=record.amount,
        currency=record.currency,
        area=record.area,
        type=record.type,
        source=record.source,
        status=record.status,
    )


# Endpoints

@router.post(
    "",
    response_model=ManualTransactionResponse,
    summary="Alta manual de transacción",
)
def add_manual(
    body: ManualTransactionRequest,
    user_id: str = Depends(get_current_user_id),
) -> ManualTransactionResponse:
    entry = ManualEntry(
        user_id=user_id,
        description=body.description,
        date=body.date,
        amount=body.amount,
        type=body.type,
        area=body.area,
    )
    result = registrar.add_manual_transaction(entry)
    return ManualTransactionResponse(
        accepted=[_record_to_out(r) for r in result.accepted],
        pending_review=[
            PendingReviewOut(record=_record_to_out(p.record),
                             anomaly_reasons=p.anomaly_reasons)
            for p in result.pending_review
        ],
        rejected=[{"reason": r.reason} for r in result.rejected],
    )


@router.post(
    "/ocr-extract",
    response_model=OCRExtractedOut,
    summary="Extraer datos de una imagen de factura (sin persistir)",
)
async def ocr_extract(
    image: UploadFile = File(..., description="Imagen del ticket/factura"),
    description_hint: Optional[str] = Form(None),
    date_hint: Optional[date] = Form(None),
    user_id: str = Depends(get_current_user_id),
) -> OCRExtractedOut:
    """
    Solo OCR: lee la imagen y devuelve un borrador (importe, fecha, área
    sugerida, descripción). No persiste nada — el cliente debe llamar a
    `POST /transactions` con los datos confirmados/editados por el usuario.
    """
    if image.content_type not in _ALLOWED_IMAGE_MIME:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"Formato no soportado: {image.content_type}",
        )
    image_bytes = await image.read()
    if len(image_bytes) > _MAX_IMAGE_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Imagen demasiado grande (máx. {_MAX_IMAGE_BYTES // (1024 * 1024)} MB)",
        )
    if not image_bytes:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Imagen vacía")

    upload = ImageUpload(
        user_id=user_id,
        image=image_bytes,
        description_hint=description_hint,
        date_hint=date_hint,
    )
    result = registrar.extract_from_image(upload)
    if result.extracted is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            result.reason or "No se pudo procesar la imagen",
        )

    e = result.extracted
    return OCRExtractedOut(
        amount=e.amount,
        description_suggested=e.description_suggested,
        date_suggested=e.date_suggested,
        area_suggested=e.area_suggested,
        type_suggested=e.type_suggested,
        currency=e.currency,
    )


@router.get(
    "",
    response_model=list[TransactionRecordOut],
    summary="Listar todas las transacciones",
)
def list_transactions(
    limit: int = 50,
    user_id: str = Depends(get_current_user_id),
) -> list[TransactionRecordOut]:
    import uuid as _uuid
    from src.data.database import get_session
    from src.data.schema import Transaction
    from sqlalchemy import select
    try:
        user_uuid = _uuid.UUID(user_id)
    except (ValueError, AttributeError, TypeError):
        # JWT con user_id no-UUID (p. ej. tokens emitidos antes de la
        # retirada del backdoor "admin@admin"). Devolvemos lista vacía
        # en lugar de 500 para no romper el frontend.
        return []
    with get_session() as session:
        stmt = (select(Transaction)
                .where(Transaction.user_id == user_uuid)
                .order_by(Transaction.date.desc())
                .limit(limit))
        records = session.execute(stmt).scalars().all()
        return [
            TransactionRecordOut(
                id=str(r.id),
                description=r.description,
                date=r.date,
                amount=r.amount,
                currency=r.currency,
                area=r.area,
                type=r.type,
                source=r.source,
                status=r.status,
            )
            for r in records
        ]


@router.get(
    "/categories",
    response_model=UserCategoriesResponse,
    summary="Categorías usadas por el usuario (con frecuencia), separadas por tipo",
)
def list_user_categories(
    user_id: str = Depends(get_current_user_id),
) -> UserCategoriesResponse:
    """Devuelve las categorías que el usuario ya tiene en sus transacciones,
    ordenadas por frecuencia descendente. Si la cuenta es nueva y no tiene
    transacciones, devuelve un fallback con las categorías "canónicas" del
    CSV base para que la UI tenga algo razonable que sugerir.
    """
    import uuid as _uuid
    from collections import Counter
    from src.data.database import get_session
    from src.data.schema import Transaction
    from sqlalchemy import select

    fallback = UserCategoriesResponse(
        income=[CategoryStat(name="Salary", count=0), CategoryStat(name="Deposit", count=0)],
        expenses=[
            CategoryStat(name="Food", count=0),
            CategoryStat(name="Leisure", count=0),
            CategoryStat(name="Invoice", count=0),
            CategoryStat(name="Investment", count=0),
            CategoryStat(name="Vacations", count=0),
        ],
    )

    try:
        user_uuid = _uuid.UUID(user_id)
    except (ValueError, AttributeError, TypeError):
        return fallback

    with get_session() as session:
        rows = session.execute(
            select(Transaction.area, Transaction.type)
            .where(Transaction.user_id == user_uuid)
        ).all()

    if not rows:
        return fallback

    inc = Counter()
    exp = Counter()
    for areas, tx_type in rows:
        for a in (areas or []):
            a = (a or "").strip()
            if not a:
                continue
            (inc if tx_type == "Income" else exp)[a] += 1

    def _to_stats(counter: Counter) -> list[CategoryStat]:
        return [CategoryStat(name=name, count=cnt) for name, cnt in counter.most_common()]

    result = UserCategoriesResponse(
        income=_to_stats(inc) or fallback.income,
        expenses=_to_stats(exp) or fallback.expenses,
    )
    return result


@router.get(
    "/pending",
    response_model=list[PendingReviewOut],
    summary="Listar transacciones pendientes de revisión",
)
def list_pending(
    user_id: str = Depends(get_current_user_id),
) -> list[PendingReviewOut]:
    result = registrar.list_pending_reviews(user_id)
    return [
        PendingReviewOut(
            record=_record_to_out(p.record),
            anomaly_reasons=p.anomaly_reasons,
        )
        for p in result.pending_review
    ]


@router.post(
    "/pending/{transaction_id}/confirm",
    response_model=TransactionRecordOut,
    summary="Aprobar una transacción pendiente",
)
def confirm_pending(
    transaction_id: str,
    user_id: str = Depends(get_current_user_id),
) -> TransactionRecordOut:
    result = registrar.confirm_pending(user_id, transaction_id)
    if result.accepted:
        return _record_to_out(result.accepted[0])

    # Hubo un error; el motivo está en rejected[0].reason
    reason = (result.rejected[0].reason if result.rejected
              else "No se pudo confirmar la transacción.")
    if "no encontrada" in reason.lower():
        raise HTTPException(status.HTTP_404_NOT_FOUND, reason)
    if "uuid" in reason.lower():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, reason)
    raise HTTPException(status.HTTP_409_CONFLICT, reason)


@router.delete(
    "/pending/{transaction_id}",
    summary="Rechazar una transacción pendiente",
)
def reject_pending(
    transaction_id: str,
    user_id: str = Depends(get_current_user_id),
) -> dict:
    result = registrar.reject_pending(user_id, transaction_id)
    if not result.rejected:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                            "Resultado vacío inesperado")

    item = result.rejected[0]
    raw = item.raw_input or {}
    if raw.get("transaction_id") == transaction_id:
        # Camino feliz: el usuario rechazó la transacción.
        return {"id": transaction_id, "status": "rejected"}

    # Error real
    reason = item.reason
    if "no encontrada" in reason.lower():
        raise HTTPException(status.HTTP_404_NOT_FOUND, reason)
    if "uuid" in reason.lower():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, reason)
    raise HTTPException(status.HTTP_409_CONFLICT, reason)

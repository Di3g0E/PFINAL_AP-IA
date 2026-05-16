"""
P3 — OCR de tickets/facturas (PaddleOCR) — Evolución E2.

Expone el extractor enriquecido de facturas EUR (`EnrichedOCRExtractor`)
que, además del total, devuelve fecha, NIF/CIF, comercio, IVA y método
de pago extraídos del texto OCR.

Endpoints:
  - POST /modules/p3/ocr-extract  (multipart: image + opcionales)

Nota: existe ya `POST /transactions/ocr-extract` con el mismo cuerpo. Este
endpoint es el equivalente "como microservicio P3" para que los agentes lo
descubran bajo el namespace de módulos.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from loguru import logger
from pydantic import BaseModel, Field

from src.agents.contracts import ImageUpload, InvoiceMetadata
from src.agents.registrar import agent as registrar
from src.api.dependencies import get_current_user_id


router = APIRouter(prefix="/modules/p3", tags=["modules:P3 (OCR)"])


_ALLOWED_IMAGE_MIME = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}
_MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB


class OCRExtractedOut(BaseModel):
    """Resultado de la extracción OCR enriquecida (E2).

    Incluye los campos clásicos (amount, description, date, area, type,
    currency) más `metadata` con los campos estructurados extraídos por
    el OCR EUR-aware (NIF, comercio, IVA, método de pago, confianza).
    """
    amount: Decimal
    description_suggested: str
    date_suggested: date
    area_suggested: list[str]
    type_suggested: Literal["Income", "Expenses"]
    currency: str
    metadata: Optional[InvoiceMetadata] = Field(
        default=None,
        description=(
            "Campos estructurados extraídos por el OCR enriquecido (E2): "
            "NIF/CIF, comercio, IVA (porcentaje e importe), método de pago. "
            "Todos opcionales; se incluyen solo cuando el OCR los detecta "
            "con confianza suficiente."
        ),
    )


@router.post(
    "/ocr-extract",
    response_model=OCRExtractedOut,
    summary="OCR de un ticket: extrae total, fecha, NIF, comercio, IVA y propone descripción/área (P3 E2)",
)
async def ocr_extract(
    image: UploadFile = File(..., description="JPEG/PNG/WebP/HEIC, máx 10 MB"),
    description_hint: Optional[str] = Form(None),
    date_hint: Optional[date] = Form(None),
    user_id: str = Depends(get_current_user_id),
) -> OCRExtractedOut:
    if image.content_type not in _ALLOWED_IMAGE_MIME:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"Formato no soportado: {image.content_type}",
        )
    image_bytes = await image.read()
    if not image_bytes:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Imagen vacía")
    if len(image_bytes) > _MAX_IMAGE_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Imagen demasiado grande (máx. {_MAX_IMAGE_BYTES // (1024 * 1024)} MB)",
        )

    upload = ImageUpload(
        user_id=user_id, image=image_bytes,
        description_hint=description_hint, date_hint=date_hint,
    )
    try:
        result = registrar.extract_from_image(upload)
    except Exception as e:
        logger.exception(f"P3.ocr-extract falló: {e}")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Error de OCR: {type(e).__name__}",
        ) from e

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
        metadata=e.metadata,
    )

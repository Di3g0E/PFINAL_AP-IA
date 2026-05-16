"""
Agente Registrar — operaciones expuestas al Orquestador.

Cada operación devuelve un `RegistryResult` (dataclass del contrato), nunca
texto libre.

Operaciones (ver doc/agent_contracts.md):
  - add_manual_transaction(entry: ManualEntry)  → RegistryResult
  - add_from_image(upload: ImageUpload)         → RegistryResult

Flujo interno:
  1. Si imagen: OCR (PaddleOCR) → total → construir descripción.
  2. Categorización con `FinancialClassifier` si `area` es None.
  3. Construcción del `TransactionDraft`.
  4. (Pendiente) Validación con Security `validate_transaction`.
  5. (Pendiente) Persistencia en Postgres.

En esta primera entrega del Registrar:
  - Persistencia: stub. Se devuelve la transacción "aceptada" como si se
    hubiera persistido (con un ID generado). Real Postgres llega cuando se
    levante `docker compose up db` y se cree el script `scripts/init_db.py`.
  - Validación de Security: stub. Sin anomaly detector aún → todo "allow".
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

import numpy as np
from loguru import logger

from src.agents.contracts import (
    ExtractedTransaction, ImageUpload, InvoiceMetadata, ManualEntry,
    OCRExtractResult, RegistryResult, RejectedItem, ReviewItem,
    TransactionDraft, TransactionRecord,
)
from src.agents.registrar.classifier import FinancialClassifier
from src.agents.registrar.ocr_engine import OCRTotalExtractor
from src.agents.registrar.ocr_engine_eur import (
    EnrichedOCRExtractor, generate_description,
)
from src.agents.registrar.preprocessing import preprocess_text
from src.agents.tools import registrar_tools, security_tools


# Cargas perezosas de los recursos pesados

_CLF: Optional[FinancialClassifier] = None
_CLF_PATH = Path(__file__).resolve().parents[3] / "models" / "area_classifier.joblib"

# Si el transformer no está instalado o falla la primera carga, el híbrido
# se desactiva y se cae al `FinancialClassifier` legacy. Estado en módulo
# para evitar reintentos costosos por petición.
_HYBRID_DISABLED: bool = False


def _get_classifier() -> Optional[FinancialClassifier]:
    """Devuelve el clasificador legacy. None si no hay modelo en disco.

    Se mantiene como fallback del `HybridClassifier` (E1) cuando el
    transformer multilingüe no está disponible (p. ej. en entornos de
    test que mockean `sentence-transformers`).
    """
    global _CLF
    if _CLF is not None:
        return _CLF
    if not _CLF_PATH.exists():
        logger.warning(f"Modelo area_classifier no encontrado en {_CLF_PATH}; "
                       "categorización deshabilitada.")
        return None
    try:
        _CLF = FinancialClassifier.load(_CLF_PATH)
        logger.info(f"Clasificador de área (legacy) cargado desde {_CLF_PATH}")
        return _CLF
    except Exception as e:
        logger.error(f"Fallo al cargar el clasificador legacy: {e}")
        return None


def _legacy_classify(description: str) -> list[str]:
    """Predicción in-process con el modelo TF-IDF + SGDClassifier global."""
    clf = _get_classifier()
    if clf is None:
        return ["Other"]
    try:
        cleaned = preprocess_text(description)
        pred = clf.predict([cleaned])[0]
        return [a.strip() for a in str(pred).split(",") if a.strip()]
    except Exception as e:
        logger.error(f"Clasificador legacy falló: {e}")
        return ["Other"]


def classify_area_full(user_id: str, description: str) -> dict:
    """Predicción con metadatos (modo + confianza + tamaño de historial).

    Es el contrato del endpoint `/modules/p2/classify-area` tras E1.
    Devuelve `{area: list[str], confidence: float, mode: str,
    user_history_size: int}`. Si el `HybridClassifier` (transformer)
    no está disponible, cae al legacy con `mode='legacy'`.
    """
    global _HYBRID_DISABLED
    if not _HYBRID_DISABLED:
        try:
            from src.agents.registrar.classifier_hybrid import HybridClassifier
            result = HybridClassifier.shared().predict(user_id, description)
            return {
                "area": result.area,
                "confidence": result.confidence,
                "mode": result.mode,
                "user_history_size": result.user_history_size,
            }
        except Exception as e:
            logger.warning(
                f"HybridClassifier no disponible ({type(e).__name__}: {e}); "
                "cayendo a clasificador legacy de forma permanente para "
                "este proceso.")
            _HYBRID_DISABLED = True
    return {
        "area": _legacy_classify(description),
        "confidence": 0.0,
        "mode": "legacy",
        "user_history_size": 0,
    }


def _classify_area(user_id: str, description: str) -> list[str]:
    """Predice el campo Area vía tool REST `/modules/p2/classify-area`.

    Mantiene firma `list[str]` por compatibilidad con el agente y los
    tests. Si la llamada al tool falla por red/auth, se usa el híbrido
    in-process (mismo resultado, sin túnel HTTP).
    """
    try:
        areas = registrar_tools.classify_area.invoke({
            "user_id": user_id, "description": description,
        })
        if areas:
            return list(areas)
    except Exception as e:
        logger.warning(f"classify_area tool falló, usando fallback in-process: {e}")
    return classify_area_full(user_id, description)["area"]


def _record_confirmed_transaction(user_id: str, description: str,
                                  area: list[str]) -> None:
    """Hook de entrenamiento incremental: actualiza el modelo personal.

    Se invoca tras cualquier alta `accepted` o `confirm_pending`. No
    propaga errores: si el HybridClassifier no está disponible, la
    operación es no-op (el alta de transacción no debe romper por un
    fallo de entrenamiento).
    """
    if _HYBRID_DISABLED:
        return
    try:
        from src.agents.registrar.classifier_hybrid import HybridClassifier
        state = HybridClassifier.shared().record_confirmed(
            user_id, description, area,
        )
        logger.debug(
            f"PersonalClassifier({user_id[:8]}…): record_confirmed → {state}")
    except Exception as e:
        logger.warning(
            f"record_confirmed_transaction falló para {user_id}: "
            f"{type(e).__name__}: {e}")


# Validación delegada al agente Security

def _security_validate(draft: TransactionDraft) -> tuple[bool, list[str]]:
    """
    Valida la transacción contra anomalías **vía tool REST** (P5).

    Llama a `/modules/p5/validate-transaction` en lugar del agente Security
    in-process. Si la llamada falla por red/auth, hace fallback al agente
    local (la validación nunca queda "muda" — bloquearía altas legítimas).

    Devuelve (ok, reasons):
      - ok=True   → Security devolvió 'allow'. Persistir.
      - ok=False  → Security devolvió 'challenge'/'deny'. Encolar para revisión.
    """
    try:
        verdict = security_tools.validate_transaction.invoke({
            "user_id": draft.user_id,
            "description": draft.description,
            "date": draft.date,
            "amount": float(draft.amount),
            "area": list(draft.area),
            "type": draft.type,
            "source": draft.source,
            "currency": draft.currency,
        })
        if verdict.decision == "allow":
            return True, []
        return False, verdict.anomaly_reasons or [verdict.reason]
    except Exception as e:
        logger.warning(f"validate_transaction tool falló, fallback in-process: {e}")

    # Fallback in-process (importación tardía para evitar ciclo registrar↔security)
    from src.agents.security import agent as security_agent
    verdict = security_agent.validate_transaction(draft)
    if verdict.decision == "allow":
        return True, []
    return False, verdict.anomaly_reasons or [verdict.reason]


def _persist(
    draft: TransactionDraft,
    *,
    status: str = "accepted",
    anomaly_reasons: Optional[list[str]] = None,
    extra_metadata: Optional[dict] = None,
) -> TransactionRecord:
    """
    Persiste la transacción con `status`, razones y metadata enriquecida (E2).

    1. Si la BD está configurada: INSERT en `transactions` y devuelve el record
       con id real generado por la BD.
    2. Si no: stub en memoria (id UUID aleatorio).
    """
    reasons = anomaly_reasons or []
    try:
        from src.data.database import get_session, is_database_configured
        from src.data.schema import Transaction
    except Exception as e:
        logger.warning(f"BD no disponible, persistencia en memoria: {e}")
        return _persist_in_memory(draft, status=status, anomaly_reasons=reasons,
                                  extra_metadata=extra_metadata)

    if not is_database_configured():
        return _persist_in_memory(draft, status=status, anomaly_reasons=reasons,
                                  extra_metadata=extra_metadata)

    try:
        with get_session() as session:
            row = Transaction(
                user_id=uuid.UUID(draft.user_id),
                description=draft.description,
                date=draft.date,
                amount=draft.amount,
                currency=draft.currency,
                area=list(draft.area),
                type=draft.type,
                source=draft.source,
                status=status,
                anomaly_reasons=reasons or None,
                extra_metadata=extra_metadata or None,
            )
            session.add(row)
            session.flush()  # asigna id y created_at
            return TransactionRecord(
                **draft.model_dump(),
                id=str(row.id),
                created_at=row.created_at,
                status=status,         # type: ignore[arg-type]
                anomaly_reasons=reasons,
            )
    except Exception as e:
        logger.error(f"INSERT falló, fallback a memoria: {e}")
        return _persist_in_memory(draft, status=status, anomaly_reasons=reasons,
                                  extra_metadata=extra_metadata)


def _persist_in_memory(
    draft: TransactionDraft,
    *,
    status: str = "accepted",
    anomaly_reasons: Optional[list[str]] = None,
    extra_metadata: Optional[dict] = None,
) -> TransactionRecord:
    """Stub: ID generado en memoria, sin tocar BD.

    El parámetro `extra_metadata` se acepta para mantener la firma con
    `_persist`. No se devuelve en el `TransactionRecord` (su contrato
    expone los campos como `InvoiceMetadata` separado en otros sitios).
    """
    del extra_metadata  # consumido por _persist real; ignorado en memoria
    return TransactionRecord(
        **draft.model_dump(),
        id=str(uuid.uuid4()),
        created_at=datetime.now(timezone.utc),
        status=status,                 # type: ignore[arg-type]
        anomaly_reasons=anomaly_reasons or [],
    )


# Operaciones expuestas al Orquestador

def add_manual_transaction(entry: ManualEntry) -> RegistryResult:
    """Alta manual: clasifica si falta area, valida y persiste."""
    area = entry.area or _classify_area(entry.user_id, entry.description)

    draft = TransactionDraft(
        user_id=entry.user_id,
        description=entry.description,
        date=entry.date,
        amount=entry.amount,
        area=area,
        type=entry.type,
        source="manual",
    )

    ok, reasons = _security_validate(draft)
    if not ok:
        # Persistimos con status='pending' para que el usuario pueda revisarla
        # más tarde. Antes solo devolvíamos el draft y se perdía al cerrar el turno.
        record = _persist(draft, status="pending", anomaly_reasons=reasons)
        return RegistryResult(pending_review=[
            ReviewItem(record=record, anomaly_reasons=reasons),
        ])

    record = _persist(draft, status="accepted")
    _record_confirmed_transaction(entry.user_id, entry.description, area)
    return RegistryResult(accepted=[record])


def add_from_image(upload: ImageUpload) -> RegistryResult:
    """Alta desde imagen con OCR enriquecido (E2).

    Pipeline:
      1. Decodifica la imagen.
      2. `EnrichedOCRExtractor.extract_all_from_image` → total + fecha
         + `InvoiceMetadata` (NIF, comercio, IVA, método de pago).
      3. Descripción: hint del usuario si llega, si no se genera a
         partir de comercio + total.
      4. Validación Security + persistencia (con `extra_metadata`).
    """
    try:
        image_array = _decode_image(upload.image)
    except Exception as e:
        logger.error(f"No se pudo decodificar la imagen: {e}")
        return RegistryResult(rejected=[RejectedItem(
            reason="Imagen inválida o corrupta",
            raw_input={"hint": upload.description_hint or ""},
        )])

    try:
        ocr_result = EnrichedOCRExtractor.shared().extract_all_from_image(image_array)
    except Exception as e:
        logger.exception(f"OCR enriquecido falló: {e}")
        # Fallback al motor legacy: garantiza que un OCR fallido no
        # rompe el alta si al menos el total es extraíble.
        try:
            total = OCRTotalExtractor.shared().extract_total_from_image(image_array)
            ocr_result = {"total": total, "date": None,
                          "metadata": InvoiceMetadata()}
        except Exception:
            return RegistryResult(rejected=[RejectedItem(
                reason=f"OCR falló: {type(e).__name__}",
                raw_input={"hint": upload.description_hint or ""},
            )])

    total = ocr_result["total"]
    if total is None:
        return RegistryResult(rejected=[RejectedItem(
            reason="No se pudo extraer un total de la imagen",
            raw_input={"hint": upload.description_hint or ""},
        )])

    md: InvoiceMetadata = ocr_result.get("metadata") or InvoiceMetadata()
    description = generate_description(md.merchant, total, upload.description_hint)
    area = _classify_area(upload.user_id, description)
    tx_date = (upload.date_hint or ocr_result.get("date")
               or datetime.now(timezone.utc).date())
    draft = TransactionDraft(
        user_id=upload.user_id,
        description=description,
        date=tx_date,
        amount=Decimal(str(round(total, 2))),
        area=area,
        type="Expenses",
        source="ocr",
    )

    extra_meta = md.model_dump(mode="json", exclude_none=True) if md else None

    ok, reasons = _security_validate(draft)
    if not ok:
        record = _persist(draft, status="pending", anomaly_reasons=reasons,
                          extra_metadata=extra_meta)
        return RegistryResult(pending_review=[
            ReviewItem(record=record, anomaly_reasons=reasons),
        ])

    record = _persist(draft, status="accepted", extra_metadata=extra_meta)
    _record_confirmed_transaction(upload.user_id, description, area)
    return RegistryResult(accepted=[record])


def extract_from_image(upload: ImageUpload) -> OCRExtractResult:
    """
    OCR-only enriquecido (E2): extrae total + fecha + metadatos (NIF,
    comercio, IVA, método de pago) y sugiere descripción/área, pero
    NO persiste. La UI muestra los campos al usuario, este los
    confirma/edita y luego se crea la transacción vía
    `add_manual_transaction`.
    """
    try:
        image_array = _decode_image(upload.image)
    except Exception as e:
        logger.error(f"No se pudo decodificar la imagen: {e}")
        return OCRExtractResult(reason="Imagen inválida o corrupta")

    try:
        ocr_result = EnrichedOCRExtractor.shared().extract_all_from_image(image_array)
    except Exception as e:
        logger.exception(f"OCR enriquecido falló: {e}")
        return OCRExtractResult(reason=f"OCR falló: {type(e).__name__}")

    total = ocr_result["total"]
    if total is None:
        return OCRExtractResult(
            reason="No se pudo extraer un total de la imagen",
        )

    md: InvoiceMetadata = ocr_result.get("metadata") or InvoiceMetadata()
    description = generate_description(md.merchant, total, upload.description_hint)
    tx_date = (upload.date_hint or ocr_result.get("date")
               or datetime.now(timezone.utc).date())
    extracted = ExtractedTransaction(
        amount=Decimal(str(round(total, 2))),
        description_suggested=description,
        date_suggested=tx_date,
        area_suggested=_classify_area(upload.user_id, description),
        type_suggested="Expenses",
        metadata=md,
    )
    return OCRExtractResult(extracted=extracted)


# Operaciones de revisión de transacciones pendientes

def list_pending_reviews(user_id: str) -> RegistryResult:
    """
    Devuelve las transacciones del usuario con `status='pending'` (las marcadas
    por Security como anómalas y aún no revisadas).

    El `record` incluye el id; el usuario puede confirmar/rechazar por id.
    """
    try:
        from src.data.database import get_session, is_database_configured
        from src.data.schema import Transaction
        from sqlalchemy import select
    except Exception as e:
        logger.warning(f"BD no disponible para list_pending_reviews: {e}")
        return RegistryResult()

    if not is_database_configured():
        return RegistryResult()

    try:
        user_uuid = uuid.UUID(user_id)
    except ValueError:
        return RegistryResult()

    items: list[ReviewItem] = []
    try:
        with get_session() as session:
            stmt = (
                select(Transaction)
                .where(Transaction.user_id == user_uuid)
                .where(Transaction.status == "pending")
                .order_by(Transaction.created_at.desc())
            )
            for row in session.execute(stmt).scalars().all():
                record = TransactionRecord(
                    id=str(row.id),
                    user_id=str(row.user_id),
                    description=row.description,
                    date=row.date,
                    amount=row.amount,
                    currency=row.currency,
                    area=list(row.area or []),
                    type=row.type,                          # type: ignore[arg-type]
                    source=row.source,                       # type: ignore[arg-type]
                    created_at=row.created_at,
                    status="pending",
                    anomaly_reasons=list(row.anomaly_reasons or []),
                )
                items.append(ReviewItem(
                    record=record,
                    anomaly_reasons=list(row.anomaly_reasons or []),
                ))
    except Exception as e:
        logger.exception(f"list_pending_reviews falló: {e}")

    return RegistryResult(pending_review=items)


def confirm_pending(user_id: str, transaction_id: str) -> RegistryResult:
    """
    El usuario aprueba una transacción pendiente: status='pending' → 'accepted'.
    A partir de ese momento contabiliza en analytics y se usa como muestra
    de entrenamiento para el `PersonalClassifier` del usuario (E1).
    """
    record_or_error = _update_pending_status(user_id, transaction_id, "accepted")
    if isinstance(record_or_error, str):
        return RegistryResult(rejected=[RejectedItem(reason=record_or_error)])
    _record_confirmed_transaction(
        user_id, record_or_error.description, record_or_error.area,
    )
    return RegistryResult(accepted=[record_or_error])


def reject_pending(user_id: str, transaction_id: str) -> RegistryResult:
    """
    El usuario rechaza una transacción pendiente: status='pending' → 'rejected'.
    Se mantiene como traza pero no se contabiliza.
    """
    record_or_error = _update_pending_status(user_id, transaction_id, "rejected")
    if isinstance(record_or_error, str):
        return RegistryResult(rejected=[RejectedItem(reason=record_or_error)])
    return RegistryResult(rejected=[RejectedItem(
        reason=f"Transacción rechazada por el usuario (id={record_or_error.id}).",
        raw_input={"transaction_id": record_or_error.id},
    )])


def _update_pending_status(
    user_id: str, transaction_id: str, new_status: str,
) -> "TransactionRecord | str":
    """Helper compartido por confirm_pending y reject_pending."""
    try:
        from src.data.database import get_session, is_database_configured
        from src.data.schema import Transaction
        from sqlalchemy import select
    except Exception as e:
        return f"BD no disponible: {e}"

    if not is_database_configured():
        return "BD no configurada."

    try:
        user_uuid = uuid.UUID(user_id)
        tx_uuid = uuid.UUID(transaction_id)
    except ValueError:
        return "Identificadores inválidos (no son UUID)."

    try:
        with get_session() as session:
            row = session.execute(
                select(Transaction)
                .where(Transaction.id == tx_uuid)
                .where(Transaction.user_id == user_uuid)
            ).scalar_one_or_none()
            if row is None:
                return f"Transacción {transaction_id} no encontrada para este usuario."
            if row.status != "pending":
                return (f"La transacción ya está en status '{row.status}'; "
                        "solo se puede confirmar/rechazar si está 'pending'.")

            row.status = new_status
            return TransactionRecord(
                id=str(row.id),
                user_id=str(row.user_id),
                description=row.description,
                date=row.date,
                amount=row.amount,
                currency=row.currency,
                area=list(row.area or []),
                type=row.type,                              # type: ignore[arg-type]
                source=row.source,                           # type: ignore[arg-type]
                created_at=row.created_at,
                status=new_status,                           # type: ignore[arg-type]
                anomaly_reasons=list(row.anomaly_reasons or []),
            )
    except Exception as e:
        logger.exception(f"_update_pending_status falló: {e}")
        return f"Error: {type(e).__name__}: {e}"


# Helpers internos

def _decode_image(image_bytes: bytes) -> np.ndarray:
    """Decodifica bytes de imagen a array BGR OpenCV."""
    import cv2
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("imdecode devolvió None")
    return img

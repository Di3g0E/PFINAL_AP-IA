"""
Smoke tests del Agente Registrar:
  - Clasificador entrenado de P2 carga y predice categorías razonables.
  - OCR extrae totales de texto (sin invocar PaddleOCR; usa
    `extract_total_from_text` directamente).
  - `add_manual_transaction` clasifica + persiste (stub) + devuelve `accepted`.
  - `add_from_image` con bytes inválidos devuelve `rejected`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from src.agents.contracts import ImageUpload, ManualEntry
from src.agents.registrar import agent as registrar
from src.agents.registrar.classifier import FinancialClassifier
from src.agents.registrar.ocr_engine import OCRTotalExtractor
from src.agents.registrar.preprocessing import preprocess_text


def test_preprocess_strips_accents_and_lowercases():
    assert preprocess_text("Café con LECHE") == "cafe con leche"
    assert preprocess_text("¡Hola, mundo!") == "hola mundo"
    assert preprocess_text(None) == ""        # type: ignore[arg-type]


def test_classifier_loads_and_predicts():
    clf = FinancialClassifier.load("models/area_classifier.joblib")
    assert clf.classes_ is not None
    samples = [
        "Cena en restaurante con amigos",
        "Transferencia recibida nomina",
        "Compra suscripcion Netflix mensual",
        "Inversion en bolsa Apple acciones",
    ]
    preds = clf.predict([preprocess_text(s) for s in samples])
    # No exigimos categorías concretas, sólo que devuelva strings de la lista entrenada
    assert len(preds) == len(samples)
    assert all(p in clf.classes_ for p in preds)


def test_ocr_extracts_eur_total_from_text():
    """Sin invocar PaddleOCR: solo el scoring sobre texto OCR ya disponible."""
    ocr = OCRTotalExtractor.shared()
    text = "Restaurant ABC Subtotal 23,80 IVA 1,70 Total 25,50"
    total = ocr.extract_total_from_text(text)
    assert total is not None
    assert 20 < total < 30   # margen amplio porque el GB de P3 fue entrenado en KRW


def test_ocr_returns_none_on_empty_text():
    ocr = OCRTotalExtractor.shared()
    assert ocr.extract_total_from_text("") is None
    assert ocr.extract_total_from_text("   ") is None


def test_add_manual_transaction_classifies_and_accepts():
    entry = ManualEntry(
        user_id="user-1",
        description="Cena en pizzeria con amigos",
        date=date(2026, 4, 15),
        amount=Decimal("18.50"),
        type="Expenses",
        area=None,            # → autoclasificar
    )
    result = registrar.add_manual_transaction(entry)

    assert len(result.accepted) == 1
    assert len(result.pending_review) == 0
    assert len(result.rejected) == 0

    record = result.accepted[0]
    assert record.id is not None
    assert record.user_id == "user-1"
    assert record.amount == Decimal("18.50")
    assert record.type == "Expenses"
    assert record.source == "manual"
    assert isinstance(record.area, list) and len(record.area) >= 1


def test_add_manual_transaction_respects_explicit_area():
    entry = ManualEntry(
        user_id="user-1",
        description="Algo raro que el clasificador podría confundir",
        date=date(2026, 4, 15),
        amount=Decimal("100.00"),
        type="Expenses",
        area=["Leisure"],     # área explícita → no se invoca clasificador
    )
    result = registrar.add_manual_transaction(entry)
    assert result.accepted[0].area == ["Leisure"]


def test_add_from_image_rejects_invalid_bytes():
    upload = ImageUpload(
        user_id="user-1",
        image=b"this is not a valid image",
    )
    result = registrar.add_from_image(upload)
    assert len(result.accepted) == 0
    assert len(result.rejected) == 1
    assert "inválida" in result.rejected[0].reason.lower() or \
           "invalid" in result.rejected[0].reason.lower()

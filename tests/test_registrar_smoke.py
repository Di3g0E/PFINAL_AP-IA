"""
Smoke tests del Agente Registrar.

Verificamos:
  - El preprocesado de texto quita tildes y minusculiza.
  - El clasificador de P2 carga y devuelve etiquetas válidas.
  - `add_manual_transaction` clasifica y persiste.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from src.agents.contracts import ManualEntry
from src.agents.registrar import agent as registrar
from src.agents.registrar.classifier import FinancialClassifier
from src.agents.registrar.preprocessing import preprocess_text


def test_preprocess_strips_accents_and_lowercases():
    assert preprocess_text("Café con LECHE") == "cafe con leche"
    assert preprocess_text("¡Hola, mundo!") == "hola mundo"


def test_classifier_loads_and_predicts():
    """El modelo entrenado de P2 debe cargar y devolver clases conocidas."""
    clf = FinancialClassifier.load("models/area_classifier.joblib")
    samples = [
        "Cena en restaurante",
        "Transferencia nómina",
        "Netflix mensual",
    ]
    preds = clf.predict([preprocess_text(s) for s in samples])
    assert len(preds) == 3
    assert all(p in clf.classes_ for p in preds)


def test_add_manual_transaction_classifies_and_accepts():
    """Una entrada manual sin area se autoclasifica y queda aceptada."""
    entry = ManualEntry(
        user_id="user-1",
        description="Cena en pizzería con amigos",
        date=date(2026, 4, 15),
        amount=Decimal("18.50"),
        type="Expenses",
        area=None,  # autoclasificar
    )
    result = registrar.add_manual_transaction(entry)

    assert len(result.accepted) == 1
    record = result.accepted[0]
    assert record.amount == Decimal("18.50")
    assert record.source == "manual"
    assert isinstance(record.area, list) and len(record.area) >= 1

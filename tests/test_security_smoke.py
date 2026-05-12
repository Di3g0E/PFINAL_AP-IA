"""
Smoke tests del Agente Security:
  - El detector se entrena con el CSV demo (887 filas).
  - Una transacción "normal" pasa con `decision='allow'`.
  - Una transacción claramente anómala (importe extremo) devuelve
    `decision='challenge'` con razones.
  - Sin histórico, devuelve `allow` por defecto (no bloquea al usuario nuevo).
  - El Registrar honra el verdict: persiste si allow, encola si challenge.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from src.agents.analyst.data_source import load_from_csv
from src.agents.contracts import ManualEntry, TransactionDraft
from src.agents.registrar import agent as registrar
from src.agents.security import agent as security
from src.agents.security.anomaly_detector import FinancialAnomalyDetector


@pytest.fixture(scope="module")
def df():
    return load_from_csv("data/raw/db_mod_descript.csv")


# --- Detector aislado -----------------------------------------------------

def test_detector_trains_with_csv(df):
    detector = FinancialAnomalyDetector(df)
    assert detector.n_history > 100
    assert detector._iforest_ready is True       # con 887 filas siempre se entrena
    assert "Food" in detector.stats_by_area or "Salary" in detector.stats_by_area


def test_detector_normal_transaction_not_anomalous(df):
    """Una compra normal de 25 EUR en Food no debería marcarse como anómala."""
    detector = FinancialAnomalyDetector(df)
    is_anom, reasons = detector.predict(
        date=date(2026, 4, 30), amount=25.0, area="Food", type_val="Expenses",
    )
    assert is_anom is False
    assert reasons == []


def test_detector_extreme_amount_is_anomalous(df):
    """Un gasto de 50000 EUR en Leisure se sale de cualquier σ."""
    detector = FinancialAnomalyDetector(df)
    is_anom, reasons = detector.predict(
        date=date(2026, 4, 30), amount=50_000.0, area="Leisure", type_val="Expenses",
    )
    assert is_anom is True
    assert any("3-Sigma" in r or "Isolation" in r for r in reasons)


def test_detector_unknown_area_flagged(df):
    """Una categoría no vista antes se marca."""
    detector = FinancialAnomalyDetector(df)
    is_anom, reasons = detector.predict(
        date=date(2026, 4, 30), amount=10.0, area="Crypto-Casino", type_val="Expenses",
    )
    assert is_anom is True
    assert any("nunca antes vista" in r for r in reasons)


def test_detector_empty_history():
    """Con DataFrame vacío no se entrena nada y no se predice anomalía."""
    detector = FinancialAnomalyDetector(pd.DataFrame())
    assert detector.n_history == 0
    is_anom, reasons = detector.predict(
        date=date(2026, 4, 30), amount=999.0, area="X", type_val="Expenses",
    )
    # Sin estadísticas: solo el "categoría nunca vista" puede dispararse
    # (si no se pobló nada, NO se evalúa ninguna regla → no anomaly)
    assert is_anom is False
    assert reasons == []


# --- Operaciones del Security agent --------------------------------------

def test_validate_transaction_no_history_allows():
    """Usuario sin histórico (UUID inválido o BD vacía) → allow por defecto.

    Razón: validar contra el histórico de OTRO usuario sería un sinsentido.
    Hasta que tenga historial propio, todas pasan.
    """
    draft = TransactionDraft(
        user_id="not-a-uuid",
        description="Yate de segunda mano",
        date=date(2026, 4, 30),
        amount=Decimal("99999.99"),
        area=["Leisure"],
        type="Expenses",
        source="manual",
    )
    verdict = security.validate_transaction(draft)
    assert verdict.decision == "allow"
    assert "Sin histórico" in verdict.reason


# --- Integración Registrar ↔ Security ------------------------------------

def test_registrar_persists_when_security_allows():
    """Una transacción normal pasa por Security y acaba en accepted."""
    entry = ManualEntry(
        user_id="not-a-uuid",
        description="Cena en pizzeria",
        date=date(2026, 4, 30),
        amount=Decimal("18.50"),
        area=["Food"],
        type="Expenses",
    )
    result = registrar.add_manual_transaction(entry)
    assert len(result.accepted) == 1
    assert len(result.pending_review) == 0


def test_registrar_queues_when_security_challenges(monkeypatch, df):
    """
    Importe extremo: Security dice challenge, Registrar encola en pending_review.

    Forzamos que `validate_transaction` tenga histórico (CSV demo) inyectándolo
    a través de `load_user_history_db_only`, ya que en tests no hay BD.
    """
    monkeypatch.setattr(
        "src.agents.security.agent.load_user_history_db_only",
        lambda _user_id: df,
    )
    entry = ManualEntry(
        user_id="not-a-uuid",
        description="Yate inesperado",
        date=date(2026, 4, 30),
        amount=Decimal("75000.00"),
        area=["Leisure"],
        type="Expenses",
    )
    result = registrar.add_manual_transaction(entry)
    assert len(result.accepted) == 0
    assert len(result.pending_review) == 1
    review = result.pending_review[0]
    # Tras el cambio en ReviewItem (draft → record), la transacción ya está
    # persistida con status='pending' y disponible por id.
    assert review.record.amount == Decimal("75000.00")
    assert review.record.status == "pending"
    assert review.record.id != ""
    assert len(review.anomaly_reasons) >= 1


def test_registrar_persists_when_no_history(monkeypatch):
    """
    Sin histórico (caso usuario nuevo en BD): validate_transaction → allow,
    aunque el importe sea extremo. Es el comportamiento defensivo deseado.
    """
    import pandas as pd
    monkeypatch.setattr(
        "src.agents.security.agent.load_user_history_db_only",
        lambda _user_id: pd.DataFrame(),
    )
    entry = ManualEntry(
        user_id="not-a-uuid",
        description="Primera compra de un usuario nuevo",
        date=date(2026, 4, 30),
        amount=Decimal("50000.00"),
        area=["Leisure"],
        type="Expenses",
    )
    result = registrar.add_manual_transaction(entry)
    assert len(result.accepted) == 1
    assert len(result.pending_review) == 0

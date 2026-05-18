"""
Tests del flujo de transacciones pendientes de revisión.

Cuando el Security detecta una anomalía (importe muy fuera de lo normal),
la transacción se marca como `pending` en vez de `accepted`. El usuario
puede confirmarla (→ accepted) o rechazarla (→ rejected).
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from src.agents.contracts import ManualEntry
from src.agents.registrar import agent as registrar
from src.data.database import get_engine, get_session, init_db, reset_engine
from src.data.schema import Transaction, User


@pytest.fixture
def user_with_history(monkeypatch, tmp_path):
    """SQLite limpio + un usuario con histórico para que el detector se entrene."""
    monkeypatch.setattr("src.utils.config.settings.database_url",
                        f"sqlite:///{tmp_path / 'test.db'}")
    reset_engine()
    get_engine()
    init_db()

    user_id = uuid.uuid4()
    with get_session() as s:
        s.add(User(id=user_id, email=f"u-{user_id}@test.local",
                   passphrase_hash="!test", biometric_consent=False))
        # 15 gastos pequeños + 15 ingresos para que el detector tenga datos
        for i in range(15):
            s.add(Transaction(user_id=user_id, description=f"Café {i}",
                              date=date(2026, 3, 1 + (i % 28)),
                              amount=Decimal("20.00"), area=["Food"],
                              type="Expenses", source="import", status="accepted"))
            s.add(Transaction(user_id=user_id, description=f"Nómina {i}",
                              date=date(2026, 1 + (i % 4), 1),
                              amount=Decimal("2000.00"), area=["Salary"],
                              type="Income", source="import", status="accepted"))

    yield str(user_id)
    reset_engine()


def test_normal_transaction_persists_as_accepted(user_with_history):
    """Una transacción razonable debe entrar como 'accepted' directamente."""
    entry = ManualEntry(
        user_id=user_with_history, description="Cena en pizzería",
        date=date(2026, 4, 15), amount=Decimal("18.50"),
        area=["Food"], type="Expenses",
    )
    result = registrar.add_manual_transaction(entry)

    assert len(result.accepted) == 1
    assert result.accepted[0].status == "accepted"
    assert len(result.pending_review) == 0


def test_anomalous_transaction_goes_to_pending(user_with_history):
    """80 000 € en Leisure es claramente anómalo → debe quedar como 'pending'."""
    entry = ManualEntry(
        user_id=user_with_history, description="Yate inesperado",
        date=date(2026, 4, 30), amount=Decimal("80000.00"),
        area=["Leisure"], type="Expenses",
    )
    result = registrar.add_manual_transaction(entry)

    assert len(result.pending_review) == 1
    assert result.pending_review[0].record.status == "pending"
    # El detector debería decirnos el motivo
    assert len(result.pending_review[0].anomaly_reasons) > 0

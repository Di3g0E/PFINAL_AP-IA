"""
Tests end-to-end del flujo de revisión de transacciones pendientes:

  1. add_manual_transaction con un draft que pasa Security → status='accepted'.
  2. add_manual_transaction con un draft anómalo → status='pending'.
  3. list_pending_reviews → devuelve solo las pendientes con id real.
  4. confirm_pending → status='pending' → 'accepted'.
  5. reject_pending → status='pending' → 'rejected'.
  6. Las pendientes/rechazadas NO aparecen en analytics (filtradas por
     `load_from_db(only_accepted=True)`).
  7. Una transacción ya en 'accepted' no se puede confirmar/rechazar dos veces.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from src.agents.analyst.data_source import load_from_db
from src.agents.contracts import ManualEntry
from src.agents.registrar import agent as registrar
from src.data.database import get_engine, get_session, init_db, reset_engine
from src.data.schema import Transaction, User


@pytest.fixture
def fresh_db_with_history(monkeypatch, tmp_path):
    """
    Aísla cada test con SQLite en tmp_path y siembra al usuario con
    suficiente histórico para que el detector de anomalías esté entrenado.
    """
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("src.utils.config.settings.database_url",
                        f"sqlite:///{db_path}")
    reset_engine()
    get_engine()
    init_db()

    user_id = uuid.uuid4()
    with get_session() as session:
        session.add(User(
            id=user_id, email=f"u-{user_id}@test.local",
            passphrase_hash="!test", biometric_consent=False,
        ))
        # Sembramos 30 transacciones razonables en Food / Salary para que
        # el detector tenga datos suficientes (MIN_HISTORY_FOR_RULES=5 y
        # min_iforest_rows=30 en el detector).
        for i in range(15):
            session.add(Transaction(
                user_id=user_id,
                description=f"Compra rutinaria {i}",
                date=date(2026, 3, 1 + (i % 28)),
                amount=Decimal("20.00"),
                area=["Food"],
                type="Expenses",
                source="import",
                status="accepted",
            ))
        for i in range(15):
            session.add(Transaction(
                user_id=user_id,
                description=f"Ingreso nómina {i}",
                date=date(2026, 1 + (i % 4), 1),
                amount=Decimal("2000.00"),
                area=["Salary"],
                type="Income",
                source="import",
                status="accepted",
            ))

    yield str(user_id)
    reset_engine()


def _count_status(user_uuid: uuid.UUID) -> dict[str, int]:
    """Helper: cuenta cuántas filas hay por status en la BD."""
    counts = {"accepted": 0, "pending": 0, "rejected": 0}
    with get_session() as session:
        rows = session.execute(
            select(Transaction).where(Transaction.user_id == user_uuid)
        ).scalars().all()
        for r in rows:
            counts[r.status] = counts.get(r.status, 0) + 1
    return counts


# Tests

def test_normal_transaction_persisted_as_accepted(fresh_db_with_history):
    user_id = fresh_db_with_history
    entry = ManualEntry(
        user_id=user_id, description="Cena en pizzeria",
        date=date(2026, 4, 15), amount=Decimal("18.50"),
        area=["Food"], type="Expenses",
    )
    result = registrar.add_manual_transaction(entry)
    assert len(result.accepted) == 1
    assert result.accepted[0].status == "accepted"


def test_anomalous_transaction_persisted_as_pending(fresh_db_with_history):
    """Importe extremo → status='pending', visible en list_pending_reviews."""
    user_id = fresh_db_with_history
    entry = ManualEntry(
        user_id=user_id, description="Yate de segunda mano",
        date=date(2026, 4, 15), amount=Decimal("75000.00"),
        area=["Leisure"], type="Expenses",
    )
    result = registrar.add_manual_transaction(entry)
    assert len(result.pending_review) == 1
    review = result.pending_review[0]
    assert review.record.status == "pending"
    assert review.record.id != ""
    assert len(review.anomaly_reasons) >= 1

    # Comprobación directa en BD
    counts = _count_status(uuid.UUID(user_id))
    assert counts["pending"] == 1


def test_list_pending_reviews_returns_only_pending(fresh_db_with_history):
    user_id = fresh_db_with_history
    # Una transacción aceptada (no debe aparecer)
    registrar.add_manual_transaction(ManualEntry(
        user_id=user_id, description="Café",
        date=date(2026, 4, 15), amount=Decimal("3.00"),
        area=["Food"], type="Expenses",
    ))
    # Una anómala (sí debe aparecer)
    anom = registrar.add_manual_transaction(ManualEntry(
        user_id=user_id, description="Compra rara",
        date=date(2026, 4, 15), amount=Decimal("99999.00"),
        area=["Leisure"], type="Expenses",
    ))
    pending_id = anom.pending_review[0].record.id

    listing = registrar.list_pending_reviews(user_id)
    assert len(listing.pending_review) == 1
    assert listing.pending_review[0].record.id == pending_id


def test_confirm_pending_promotes_to_accepted(fresh_db_with_history):
    user_id = fresh_db_with_history
    anom = registrar.add_manual_transaction(ManualEntry(
        user_id=user_id, description="Vacaciones inesperadas",
        date=date(2026, 4, 15), amount=Decimal("88888.00"),
        area=["Leisure"], type="Expenses",
    ))
    pending_id = anom.pending_review[0].record.id

    confirmed = registrar.confirm_pending(user_id, pending_id)
    assert len(confirmed.accepted) == 1
    assert confirmed.accepted[0].id == pending_id
    assert confirmed.accepted[0].status == "accepted"

    counts = _count_status(uuid.UUID(user_id))
    assert counts["pending"] == 0


def test_reject_pending_marks_as_rejected(fresh_db_with_history):
    user_id = fresh_db_with_history
    anom = registrar.add_manual_transaction(ManualEntry(
        user_id=user_id, description="Error de tecleo",
        date=date(2026, 4, 15), amount=Decimal("99999.00"),
        area=["Leisure"], type="Expenses",
    ))
    pending_id = anom.pending_review[0].record.id

    rejected_result = registrar.reject_pending(user_id, pending_id)
    assert len(rejected_result.rejected) == 1
    assert rejected_result.rejected[0].raw_input.get("transaction_id") == pending_id

    counts = _count_status(uuid.UUID(user_id))
    assert counts["pending"] == 0
    assert counts["rejected"] == 1


def test_pending_not_counted_in_analytics(fresh_db_with_history):
    """
    Una transacción 'pending' NO debe aparecer en `load_from_db` por defecto.
    Esto evita que los analytics narren cifras todavía no confirmadas.
    """
    user_id = fresh_db_with_history
    user_uuid = uuid.UUID(user_id)

    # Insertamos una pendiente extrema
    registrar.add_manual_transaction(ManualEntry(
        user_id=user_id, description="Yate inesperado",
        date=date(2026, 4, 15), amount=Decimal("99999.00"),
        area=["Leisure"], type="Expenses",
    ))

    with get_session() as session:
        df_default = load_from_db(session, user_uuid)
        df_all = load_from_db(session, user_uuid, only_accepted=False)

    # 30 sembradas + 0 pending visibles
    assert len(df_default) == 30
    # 30 sembradas + 1 pending = 31
    assert len(df_all) == 31


def test_double_confirm_is_rejected(fresh_db_with_history):
    """No se puede confirmar dos veces la misma transacción."""
    user_id = fresh_db_with_history
    anom = registrar.add_manual_transaction(ManualEntry(
        user_id=user_id, description="Compra grande",
        date=date(2026, 4, 15), amount=Decimal("88000.00"),
        area=["Leisure"], type="Expenses",
    ))
    pending_id = anom.pending_review[0].record.id

    first = registrar.confirm_pending(user_id, pending_id)
    assert len(first.accepted) == 1

    second = registrar.confirm_pending(user_id, pending_id)
    assert len(second.accepted) == 0
    assert len(second.rejected) == 1
    assert "pending" in second.rejected[0].reason.lower()


def test_confirm_unknown_transaction_returns_error(fresh_db_with_history):
    user_id = fresh_db_with_history
    fake_id = str(uuid.uuid4())
    result = registrar.confirm_pending(user_id, fake_id)
    assert len(result.accepted) == 0
    assert len(result.rejected) == 1
    assert "no encontrada" in result.rejected[0].reason.lower()


def test_confirm_with_invalid_uuid_string(fresh_db_with_history):
    user_id = fresh_db_with_history
    result = registrar.confirm_pending(user_id, "not-a-uuid")
    assert len(result.rejected) == 1
    assert "uuid" in result.rejected[0].reason.lower()

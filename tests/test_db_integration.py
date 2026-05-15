"""
Tests de integración con SQLite in-memory.

Verifican que:
  - El esquema crea correctamente.
  - El Registrar persiste un draft en BD y devuelve un Record con ID real.
  - El Analyst (`load_from_db`) recupera transacciones del usuario y produce
    un AnalysisReport correcto.

Estrategia: cada test usa una BD SQLite in-memory limpia. Reseteamos el
engine global con `reset_engine()` y forzamos `database_url` con monkeypatch.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from src.agents.analyst import agent as analyst
from src.agents.analyst.data_source import load_from_db
from src.agents.contracts import ManualEntry
from src.agents.registrar import agent as registrar
from src.data.database import (
    get_engine, get_session, init_db, is_database_configured, reset_engine,
)
from src.data.schema import Transaction, User


@pytest.fixture
def fresh_sqlite_db(monkeypatch, tmp_path):
    """Aísla cada test en una BD SQLite real (fichero temporal)."""
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("src.utils.config.settings.database_url",
                        f"sqlite:///{db_path}")
    reset_engine()
    get_engine()
    init_db()

    # Crea un usuario válido para satisfacer la FK
    user_id = uuid.uuid4()
    with get_session() as session:
        session.add(User(
            id=user_id,
            email=f"u-{user_id}@test.local",
            passphrase_hash="!test",
            biometric_consent=False,
        ))

    yield str(user_id)

    reset_engine()


def test_database_configured_picks_sqlite(fresh_sqlite_db):
    assert is_database_configured() is True


def test_persist_writes_to_real_db(fresh_sqlite_db):
    """Registrar.add_manual_transaction debe acabar en la tabla `transactions`."""
    user_id = fresh_sqlite_db
    entry = ManualEntry(
        user_id=user_id,
        description="Cena en pizzeria con amigos",
        date=date(2026, 4, 15),
        amount=Decimal("18.50"),
        type="Expenses",
        area=["Leisure"],
    )
    result = registrar.add_manual_transaction(entry)

    assert len(result.accepted) == 1
    record = result.accepted[0]
    assert record.id != ""
    # La id debe parsear como UUID válido
    uuid.UUID(record.id)

    # Verificación directa en la BD: la fila existe
    with get_session() as session:
        row = session.execute(
            select(Transaction).where(Transaction.id == uuid.UUID(record.id))
        ).scalar_one()
        assert row.amount == Decimal("18.50")
        assert row.area == ["Leisure"]
        assert row.type == "Expenses"
        assert row.source == "manual"


def test_analyst_reads_from_db(fresh_sqlite_db):
    """`load_from_db` debe devolver las transacciones persistidas con el esquema esperado."""
    user_id = fresh_sqlite_db
    # Insertamos 3 transacciones a través del Registrar
    entries = [
        ManualEntry(user_id=user_id, description="Sueldo", date=date(2026, 3, 1),
                    amount=Decimal("2000.00"), type="Income", area=["Salary"]),
        ManualEntry(user_id=user_id, description="Cena", date=date(2026, 3, 10),
                    amount=Decimal("25.50"), type="Expenses", area=["Leisure"]),
        ManualEntry(user_id=user_id, description="Factura luz", date=date(2026, 3, 15),
                    amount=Decimal("45.00"), type="Expenses", area=["Invoice"]),
    ]
    for e in entries:
        registrar.add_manual_transaction(e)

    with get_session() as session:
        df = load_from_db(session, user_id)

    expected_cols = {"Description", "Date", "Amount_clean", "Area", "Type",
                     "Date_parsed", "Year", "Month", "YearMonth"}
    assert expected_cols.issubset(df.columns)
    assert len(df) == 3
    # El analytics debe funcionar sobre estos datos
    rep = analyst.monthly_summary(df, year=2026, month=3)
    assert rep.metrics["income"] == 2000.0
    assert rep.metrics["expenses"] == 70.5
    assert rep.metrics["n_transactions"] == 3


def test_persist_unknown_user_falls_back_to_memory(fresh_sqlite_db):
    """
    Si el `user_id` no es UUID válido, el INSERT lanza ValueError; el código
    debe capturarlo y caer al stub en memoria sin levantar excepción.
    """
    entry = ManualEntry(
        user_id="not-a-uuid",
        description="ejemplo",
        date=date(2026, 4, 1),
        amount=Decimal("10.00"),
        type="Expenses",
        area=["Other"],
    )
    result = registrar.add_manual_transaction(entry)

    # Debe devolver accepted con un ID generado en memoria
    assert len(result.accepted) == 1
    record = result.accepted[0]
    uuid.UUID(record.id)   # ID válido aunque no se haya persistido


def test_analyst_falls_back_to_csv_when_user_has_no_data(fresh_sqlite_db):
    """
    Un usuario válido sin transacciones en BD: el data_source debe caer al
    CSV demo (no devolver DataFrame vacío).

    Tras la Fase 2, `_load_user_dataframe` se eliminó del orchestrator
    (ahora la carga de datos vive en `/modules/p4/*` REST que llaman a
    `data_source.load_user_transactions` directamente). El equivalente
    actual del test es verificar `load_user_transactions` end-to-end.
    """
    from src.agents.analyst.data_source import load_user_transactions
    user_id = fresh_sqlite_db
    df = load_user_transactions(user_id)
    # El CSV de demo tiene 887 filas → confirmamos que el fallback funcionó
    assert len(df) > 100

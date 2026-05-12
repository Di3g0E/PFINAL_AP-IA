"""
Carga de transacciones para el agente Analyst.

Origen: P4_AP-IA/src/data/loader.py — adaptado para P6:
  - `load_from_csv(path)`: usa el formato del CSV de P5 (Description, Date,
    Amount con '€' y coma decimal, Area, Type).
  - `load_from_db(user_id)`: lee la tabla `transactions` de Postgres y la
    transforma al mismo esquema con columnas derivadas (Year, Month, YearMonth,
    Amount_clean, Date_parsed) que esperan los analytics.

Las dos funciones devuelven un DataFrame compatible con `analytics.py`.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path
from typing import Optional

import pandas as pd
from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.agents.contracts import Goal as GoalContract
from src.data.schema import Goal as GoalRow, Transaction


# Path al CSV de demo (mismo que usa init_db.py)
_DEFAULT_CSV = Path(__file__).resolve().parents[3] / "data" / "raw" / "db_mod_descript.csv"

_EMPTY_DF_COLUMNS = [
    "Description", "Date", "Amount_clean", "Area", "Type",
    "Date_parsed", "Year", "Month", "YearMonth",
]


def _parse_amount_eur(series: pd.Series) -> pd.Series:
    """Convierte '10,00€' o '1.234,50€' a float (10.00, 1234.50)."""
    return (
        series.astype(str)
        .str.replace("€", "", regex=False)
        .str.replace(".", "", regex=False)   # separador de miles
        .str.replace(",", ".", regex=False)  # decimal
        .astype(float)
    )


def _add_derived_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Añade Year, Month, YearMonth y ordena por fecha descendente."""
    df["Year"] = df["Date_parsed"].dt.year
    df["Month"] = df["Date_parsed"].dt.month
    df["YearMonth"] = df["Date_parsed"].dt.to_period("M")
    return df.sort_values("Date_parsed", ascending=False).reset_index(drop=True)


def load_from_csv(path: str | Path) -> pd.DataFrame:
    """
    Carga el CSV con formato P5 (`Description, Date, Amount, Area, Type`).

    Útil para arranque sin BD (smoke tests, modo demo).
    """
    df = pd.read_csv(path)
    df["Amount_clean"] = _parse_amount_eur(df["Amount"])
    df["Date_parsed"] = pd.to_datetime(df["Date"], dayfirst=True)
    return _add_derived_columns(df)


def load_from_db(
    session: Session,
    user_id: str | uuid.UUID,
    *,
    only_accepted: bool = True,
) -> pd.DataFrame:
    """
    Carga las transacciones de un usuario desde la BD y las transforma al
    esquema esperado por `analytics.py`.

    Acepta `user_id` como string o UUID; convierte si hace falta porque la
    columna `transactions.user_id` es de tipo `Uuid`.

    El campo `area` en BD es JSON (lista) — lo unimos con coma para
    mantener compatibilidad con los analytics que esperan `Area: str`.

    Args:
        only_accepted: si True (default) solo lee transacciones con
            `status='accepted'`. Las pendientes y rechazadas NO se cuentan
            en analytics hasta que el usuario las confirme.
    """
    if isinstance(user_id, str):
        try:
            user_id = uuid.UUID(user_id)
        except ValueError:
            return pd.DataFrame(columns=_EMPTY_DF_COLUMNS)

    stmt = select(Transaction).where(Transaction.user_id == user_id)
    if only_accepted:
        stmt = stmt.where(Transaction.status == "accepted")
    rows = session.execute(stmt).scalars().all()

    if not rows:
        # DataFrame vacío con las columnas esperadas
        return pd.DataFrame(columns=[
            "Description", "Date", "Amount_clean", "Area", "Type",
            "Date_parsed", "Year", "Month", "YearMonth",
        ])

    records = [{
        "Description": r.description,
        "Date": r.date.strftime("%d/%m/%Y"),
        "Amount_clean": float(r.amount),
        "Area": ", ".join(r.area) if r.area else "",
        "Type": r.type,
        "Date_parsed": pd.Timestamp(r.date),
    } for r in rows]

    df = pd.DataFrame(records)
    return _add_derived_columns(df)


def _safe_csv_fallback() -> pd.DataFrame:
    """Carga el CSV demo si existe; si no, DataFrame vacío con el esquema esperado."""
    if not _DEFAULT_CSV.exists():
        logger.warning(f"CSV demo no existe en {_DEFAULT_CSV}; devolviendo DataFrame vacío")
        return pd.DataFrame(columns=_EMPTY_DF_COLUMNS)
    return load_from_csv(_DEFAULT_CSV)


def load_user_history_db_only(user_id: str) -> pd.DataFrame:
    """
    Lee SOLO de la BD las transacciones del usuario. Sin fallback al CSV.

    Útil cuando consumir datos ajenos al usuario daría una respuesta
    incorrecta (p. ej. el detector de anomalías del Security: validar contra
    el histórico de otro usuario sería un sinsentido). Si no hay datos del
    usuario, devuelve un DataFrame vacío y el llamador decide qué hacer.
    """
    try:
        from src.data.database import get_session, is_database_configured
    except Exception as e:
        logger.debug(f"BD no importable: {e}")
        return pd.DataFrame(columns=_EMPTY_DF_COLUMNS)

    if not is_database_configured() or not user_id:
        return pd.DataFrame(columns=_EMPTY_DF_COLUMNS)

    try:
        uuid.UUID(user_id)
    except ValueError:
        return pd.DataFrame(columns=_EMPTY_DF_COLUMNS)

    try:
        with get_session() as session:
            return load_from_db(session, user_id)
    except Exception as e:
        logger.warning(f"Lectura DB-only falló: {e}")
        return pd.DataFrame(columns=_EMPTY_DF_COLUMNS)


def load_user_transactions(user_id: str) -> pd.DataFrame:
    """
    Helper compartido entre agentes: carga las transacciones de un usuario.

      1. Si la BD está configurada y tiene datos para `user_id` → DB.
      2. Si no → CSV demo si existe.
      3. Si tampoco hay CSV → DataFrame vacío con el esquema correcto.

    Centraliza la lógica para que `nodes.py` (Analyst) y `security/agent.py`
    (validate_transaction) compartan exactamente el mismo origen de datos.
    """
    try:
        from src.data.database import get_session, is_database_configured
    except Exception as e:
        logger.debug(f"BD no importable, usando CSV: {e}")
        return _safe_csv_fallback()

    if not is_database_configured() or not user_id:
        return _safe_csv_fallback()

    try:
        uuid.UUID(user_id)
    except ValueError:
        return _safe_csv_fallback()

    try:
        with get_session() as session:
            df = load_from_db(session, user_id)
        if not df.empty:
            return df
    except Exception as e:
        logger.warning(f"Lectura de BD falló, fallback a CSV: {e}")

    return _safe_csv_fallback()


# Helpers de objetivos (tabla `goals`)

def _row_to_contract(row: GoalRow) -> GoalContract:
    return GoalContract(
        id=str(row.id),
        user_id=str(row.user_id),
        area=row.area,
        max_amount=row.max_amount,
        period=row.period,           # type: ignore[arg-type]
        active=row.active,
    )


def load_user_goals(user_id: str, *, only_active: bool = True) -> list[GoalContract]:
    """
    Devuelve los objetivos del usuario desde la tabla `goals`.

    Si la BD no está configurada, el `user_id` no es UUID válido o falla la
    consulta, devuelve una lista vacía (los llamadores deben tolerarlo).
    """
    try:
        from src.data.database import get_session, is_database_configured
    except Exception as e:
        logger.debug(f"BD no importable para load_user_goals: {e}")
        return []

    if not is_database_configured() or not user_id:
        return []

    try:
        user_uuid = uuid.UUID(user_id)
    except ValueError:
        return []

    try:
        with get_session() as session:
            stmt = select(GoalRow).where(GoalRow.user_id == user_uuid)
            if only_active:
                stmt = stmt.where(GoalRow.active.is_(True))
            rows = session.execute(stmt).scalars().all()
            return [_row_to_contract(r) for r in rows]
    except Exception as e:
        logger.warning(f"load_user_goals falló: {e}")
        return []


def upsert_user_goal(
    user_id: str, area: str, max_amount, period: str = "monthly",
) -> Optional[GoalContract]:
    """
    Crea o actualiza el objetivo (user_id, area). Semántica P4: hay como
    máximo un objetivo activo por (user_id, area); si ya existe, se sobreescribe.

    Devuelve el `Goal` resultante, o None si la BD no está disponible.
    """
    try:
        from src.data.database import get_session, is_database_configured
    except Exception as e:
        logger.debug(f"BD no importable para upsert_user_goal: {e}")
        return None

    if not is_database_configured():
        return None

    try:
        user_uuid = uuid.UUID(user_id)
    except ValueError:
        return None

    try:
        with get_session() as session:
            existing = session.execute(
                select(GoalRow)
                .where(GoalRow.user_id == user_uuid)
                .where(GoalRow.area == area)
                .where(GoalRow.active.is_(True))
            ).scalar_one_or_none()

            if existing is not None:
                existing.max_amount = Decimal(str(max_amount))
                existing.period = period
                row = existing
            else:
                row = GoalRow(
                    user_id=user_uuid,
                    area=area,
                    max_amount=Decimal(str(max_amount)),
                    period=period,
                    active=True,
                )
                session.add(row)
            session.flush()
            return _row_to_contract(row)
    except Exception as e:
        logger.warning(f"upsert_user_goal falló: {e}")
        return None


def delete_user_goal(user_id: str, area: str) -> bool:
    """
    Marca como inactivo el objetivo (user_id, area). Soft-delete: conserva
    histórico para auditoría. Devuelve True si se borró alguno.
    """
    try:
        from src.data.database import get_session, is_database_configured
    except Exception as e:
        logger.debug(f"BD no importable para delete_user_goal: {e}")
        return False

    if not is_database_configured():
        return False

    try:
        user_uuid = uuid.UUID(user_id)
    except ValueError:
        return False

    try:
        with get_session() as session:
            existing = session.execute(
                select(GoalRow)
                .where(GoalRow.user_id == user_uuid)
                .where(GoalRow.area == area)
                .where(GoalRow.active.is_(True))
            ).scalar_one_or_none()
            if existing is None:
                return False
            existing.active = False
            return True
    except Exception as e:
        logger.warning(f"delete_user_goal falló: {e}")
        return False

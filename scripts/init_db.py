"""
Inicializa la base de datos de P6.

Acciones:
  1. Crea el fichero SQLite (o la BD Postgres) si no existe.
  2. Crea todas las tablas declaradas en `src/data/schema.py`.
  3. Crea el usuario demo (`DEMO_USER_ID`).
  4. Migra el CSV `data/raw/db_mod_descript.csv` a la tabla `transactions`.

Uso:
    python scripts/init_db.py            # crea o complementa
    python scripts/init_db.py --reset    # borra tablas y reconstruye desde cero

La URL de la BD se lee de `settings.database_url` (config.py / .env). Si no se
ha configurado, usa SQLite en `./data/p6.db` por defecto.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

# Permite ejecutar `python scripts/init_db.py` directamente sin instalar el paquete.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect, select, text  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402

from src.agents.analyst.data_source import load_from_csv  # noqa: E402
from src.data.database import (  # noqa: E402
    get_engine, get_session, init_db, is_database_configured,
)
from src.data.schema import Base, Transaction, User  # noqa: E402
from src.utils.config import DEMO_USER_ID, settings  # noqa: E402
from src.utils.logging_config import configure_logging  # noqa: E402


CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "raw" / "db_mod_descript.csv"
DEMO_EMAIL = "demo@p6.local"

# Migraciones ligeras: columna → DDL ADD COLUMN.
# Cuando aparezcan más cambios incrementales, añadir aquí. Si esto crece más
# allá de unas pocas entradas, conviene migrar a Alembic.
_PENDING_MIGRATIONS = {
    "transactions": {
        "status": "VARCHAR(16) NOT NULL DEFAULT 'accepted'",
        "anomaly_reasons": "JSON",
    },
}


def _apply_lightweight_migrations(engine: Engine) -> None:
    """
    Detecta columnas declaradas en `schema.py` que aún NO existen en la BD
    y las añade con `ALTER TABLE ADD COLUMN`. Idempotente y no-destructivo.

    SQLAlchemy.create_all() no toca tablas existentes, así que sin esto los
    cambios incrementales del esquema solo se aplican con `--reset` (perdiendo
    datos). Esta función cubre el 90 % de los casos comunes (añadir columna
    con default) sin necesidad de Alembic. Para cambios más complejos
    (rename, drop, type change) sí hará falta Alembic.
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    for table_name, expected_cols in _PENDING_MIGRATIONS.items():
        if table_name not in existing_tables:
            continue   # `init_db` la creará completa desde el modelo
        actual_cols = {c["name"] for c in inspector.get_columns(table_name)}
        missing = {c: ddl for c, ddl in expected_cols.items() if c not in actual_cols}
        if not missing:
            continue
        with engine.begin() as conn:
            for col, col_ddl in missing.items():
                conn.execute(text(
                    f'ALTER TABLE {table_name} ADD COLUMN {col} {col_ddl}'
                ))
                print(f"Migración aplicada: ALTER TABLE {table_name} ADD COLUMN {col}")


def _create_demo_user(session) -> User:
    """Crea (o devuelve) el usuario demo con UUID fijo."""
    demo_uuid = uuid.UUID(DEMO_USER_ID)
    user = session.execute(select(User).where(User.id == demo_uuid)).scalar_one_or_none()
    if user is not None:
        print(f"Usuario demo ya existe: {DEMO_USER_ID}")
        return user

    user = User(
        id=demo_uuid,
        email=DEMO_EMAIL,
        passphrase_hash="!demo-no-password",   # placeholder hasta que exista Security
        biometric_consent=False,
    )
    session.add(user)
    session.flush()
    print(f"Usuario demo creado: {DEMO_USER_ID} ({DEMO_EMAIL})")
    return user


def _migrate_csv_to_demo_user(session) -> int:
    """Vuelca el CSV de demo a `transactions` para el usuario demo. Idempotente."""
    if not CSV_PATH.exists():
        print(f"CSV no encontrado en {CSV_PATH}; saltando migración.")
        return 0

    demo_uuid = uuid.UUID(DEMO_USER_ID)
    existing = session.execute(
        select(Transaction).where(Transaction.user_id == demo_uuid).limit(1)
    ).scalar_one_or_none()
    if existing is not None:
        n = session.execute(
            select(Transaction).where(Transaction.user_id == demo_uuid)
        ).scalars().all()
        print(f"Ya hay {len(n)} transacciones para el usuario demo; saltando migración.")
        return 0

    df = load_from_csv(CSV_PATH)
    rows = []
    for _, row in df.iterrows():
        # `Area` puede ser 'Leisure' o 'Leisure, Vacations' → list[str]
        areas = [a.strip() for a in str(row["Area"]).split(",") if a.strip()]
        rows.append(Transaction(
            user_id=demo_uuid,
            description=row["Description"],
            date=row["Date_parsed"].date(),
            amount=row["Amount_clean"],
            currency="EUR",
            area=areas,
            type=row["Type"],
            source="import",
        ))

    session.add_all(rows)
    print(f"Migradas {len(rows)} transacciones del CSV al usuario demo.")
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Inicializa la BD de P6.")
    parser.add_argument("--reset", action="store_true",
                        help="Borra todas las tablas antes de recrearlas.")
    args = parser.parse_args()

    configure_logging()

    if not is_database_configured():
        print(f"DATABASE_URL inválida o ausente: {settings.database_url!r}", file=sys.stderr)
        return 1

    print(f"Conectando a {settings.database_url}")
    engine = get_engine()

    if args.reset:
        Base.metadata.drop_all(engine)
        print("Tablas eliminadas.")

    init_db()
    print("Tablas creadas.")

    # Migraciones ligeras: añade columnas nuevas declaradas en el modelo a
    # tablas que ya existían en BDs antiguas.
    _apply_lightweight_migrations(engine)

    with get_session() as session:
        _create_demo_user(session)
        _migrate_csv_to_demo_user(session)

    print("Inicialización completa.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

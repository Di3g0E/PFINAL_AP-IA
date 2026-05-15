"""
Engine SQLAlchemy y factory de sesiones.

La URL se lee de `settings.database_url`. Ejemplos válidos:
  - SQLite (dev, default):  sqlite:///./data/p6.db
  - SQLite in-memory (test): sqlite:///:memory:
  - Postgres local docker:  postgresql+psycopg://app:app@db:5432/p6
  - Supabase:               postgresql+psycopg://postgres.xxx:pwd@aws-0-eu-central-1.pooler.supabase.com:6543/postgres

`is_database_configured()` permite a otros módulos (LLM factory, agentes) saber
si la BD es usable y, si no, hacer fallback elegante.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from loguru import logger
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from src.data.schema import Base
from src.utils.config import settings


_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def is_database_configured() -> bool:
    """
    Heurística simple: la URL es usable si tiene un esquema reconocido y
    no es el placeholder '...'.
    """
    url = (settings.database_url or "").strip()
    if not url or url == "..." or url.endswith("://"):
        return False
    return url.startswith(("postgresql://", "postgresql+psycopg://",
                           "postgresql+asyncpg://", "sqlite://",
                           "sqlite:///"))


def _ensure_sqlite_dir(url: str) -> None:
    """Crea el directorio padre del fichero SQLite si no existe."""
    if not url.startswith("sqlite:///") or url == "sqlite:///:memory:":
        return
    db_path = Path(url.replace("sqlite:///", "", 1))
    db_path.parent.mkdir(parents=True, exist_ok=True)


def get_engine() -> Engine:
    """Devuelve el engine singleton, inicializándolo en el primer uso."""
    global _engine, _SessionLocal
    if _engine is None:
        url = settings.database_url
        if not is_database_configured():
            raise RuntimeError(f"DATABASE_URL inválida o ausente: {url!r}")
        _ensure_sqlite_dir(url)

        # SQLite no soporta los kwargs de pool de connection
        if url.startswith("sqlite"):
            _engine = create_engine(url, future=True)
        else:
            # `prepare_threshold=None` deshabilita los prepared statements
            # de psycopg3. Imprescindible para el pooler de Supabase en modo
            # Transaction (puerto 6543) — recicla conexiones entre queries y
            # los nombres de prepared statements colisionan
            # ("prepared statement '_pg3_0' already exists").
            connect_args: dict = {}
            if url.startswith(("postgresql://", "postgresql+psycopg://")):
                connect_args["prepare_threshold"] = None
            _engine = create_engine(
                url, pool_pre_ping=True, pool_size=5, max_overflow=10,
                future=True, connect_args=connect_args,
            )

        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False,
                                     expire_on_commit=False)
        logger.info(f"Engine inicializado contra {url.split('@')[-1]}")
    return _engine


def reset_engine() -> None:
    """Cierra y olvida el engine actual (útil entre tests)."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def init_db() -> None:
    """Crea todas las tablas declaradas en schema.py si no existen.

    También aplica migraciones ligeras (`ALTER TABLE ADD COLUMN`) para
    columnas nuevas en tablas que ya existen — SQLAlchemy create_all no
    toca tablas existentes.
    """
    engine = get_engine()
    Base.metadata.create_all(engine)
    _apply_lightweight_migrations(engine)
    logger.info("Esquema creado en la base de datos")


# Columnas añadidas tras el primer despliegue que no existen en BDs antiguas.
# Cada entrada es {tabla: {columna: ddl_que_acompaña_al_ADD}}. Idempotente:
# si la columna ya existe se salta. Las cláusulas DEFAULT en `ALTER TABLE
# ADD COLUMN` rellenan filas existentes.
_PENDING_MIGRATIONS: dict[str, dict[str, str]] = {
    "transactions": {
        "status": "VARCHAR(16) NOT NULL DEFAULT 'accepted'",
        "anomaly_reasons": "JSON",
    },
    "users": {
        # role para Fase 3: 'basic' default, 'advanced' por opt-in en /settings.
        "role": "VARCHAR(16) NOT NULL DEFAULT 'basic'",
    },
}


def _apply_lightweight_migrations(engine: Engine) -> None:
    """ALTER TABLE para columnas nuevas declaradas en schema.py.

    Cubre el 90% de los cambios de esquema (añadir columna con default).
    Para cambios complejos (rename, drop, type change) hace falta Alembic.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    for table_name, expected_cols in _PENDING_MIGRATIONS.items():
        if table_name not in existing_tables:
            continue  # init_db.create_all la habrá creado completa
        actual_cols = {c["name"] for c in inspector.get_columns(table_name)}
        missing = {c: ddl for c, ddl in expected_cols.items() if c not in actual_cols}
        if not missing:
            continue
        with engine.begin() as conn:
            for col, col_ddl in missing.items():
                conn.execute(text(
                    f"ALTER TABLE {table_name} ADD COLUMN {col} {col_ddl}"
                ))
                logger.info(f"Migración: ALTER TABLE {table_name} ADD COLUMN {col}")


@contextmanager
def get_session() -> Iterator[Session]:
    """Context manager para uso manual con 'with get_session() as session:'."""
    if _SessionLocal is None:
        get_engine()
    assert _SessionLocal is not None
    session: Session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """
    Dependency de FastAPI: generador puro (sin @contextmanager) que cede una
    sesión y la cierra al terminar la request.
    """
    with get_session() as session:
        yield session

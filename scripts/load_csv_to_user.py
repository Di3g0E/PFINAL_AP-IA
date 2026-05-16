"""Carga el CSV base (data/raw/db_mod_descript.csv) en la cuenta de un usuario.

Borra primero TODAS las transacciones existentes del usuario destino (incluidas
pendientes y rechazadas) y luego inserta las del CSV con `source='import'`.
No toca al usuario demo ni a ningún otro.

Uso:
    .venv/Scripts/python.exe scripts/load_csv_to_user.py <email>
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, func, select  # noqa: E402

from src.agents.analyst.data_source import load_from_csv  # noqa: E402
from src.data.database import get_session  # noqa: E402
from src.data.schema import Transaction, User  # noqa: E402


CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "raw" / "db_mod_descript.csv"


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2

    email = sys.argv[1].strip().lower()
    if not CSV_PATH.exists():
        print(f"ERROR: no encuentro el CSV en {CSV_PATH}")
        return 1

    with get_session() as s:
        user = s.execute(
            select(User).where(func.lower(User.email) == email)
        ).scalar_one_or_none()
        if user is None:
            print(f"ERROR: no existe usuario con email {email!r}.")
            return 1

        n_before = s.execute(
            select(func.count()).select_from(Transaction).where(
                Transaction.user_id == user.id
            )
        ).scalar() or 0
        print(f"Destino: {user.email} ({user.id}) | {n_before} transacciones antes.")

        deleted = s.execute(
            delete(Transaction).where(Transaction.user_id == user.id)
        ).rowcount
        print(f"Borradas {deleted} transacciones existentes.")

        df = load_from_csv(CSV_PATH)
        rows = []
        for _, row in df.iterrows():
            areas = [a.strip() for a in str(row["Area"]).split(",") if a.strip()]
            rows.append(Transaction(
                user_id=user.id,
                description=row["Description"],
                date=row["Date_parsed"].date(),
                amount=row["Amount_clean"],
                currency="EUR",
                area=areas,
                type=row["Type"],
                source="import",
                status="accepted",
            ))
        s.add_all(rows)
        print(f"Insertadas {len(rows)} transacciones desde {CSV_PATH.name}.")

    with get_session() as s:
        n_after = s.execute(
            select(func.count()).select_from(Transaction).where(
                Transaction.user_id == user.id
            )
        ).scalar() or 0
        print(f"OK: {email} tiene ahora {n_after} transacciones.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

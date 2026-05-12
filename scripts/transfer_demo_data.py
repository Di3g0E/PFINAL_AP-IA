"""
Transfiere las transacciones del usuario demo a un usuario real (registrado vía web).

Borra primero las transacciones existentes del usuario destino y mueve las del
demo a su `user_id`. Pensado para preparar una cuenta real con datos de muestra.

Uso:
    python scripts/transfer_demo_data.py <email_destino>

Ejemplo:
    python scripts/transfer_demo_data.py d.esclarin.2022@alumnos.urjc.es
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, func, select, update  # noqa: E402

from src.data.database import get_session  # noqa: E402
from src.data.schema import Transaction, User  # noqa: E402
from src.utils.config import DEMO_USER_ID  # noqa: E402


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 1

    target_email = sys.argv[1].strip().lower()
    demo_uuid = uuid.UUID(DEMO_USER_ID)

    with get_session() as session:
        target_user = session.execute(
            select(User).where(func.lower(User.email) == target_email)
        ).scalar_one_or_none()
        if target_user is None:
            print(f"ERROR: no existe usuario con email {target_email!r}.")
            print("Revisa la lista con `python scripts/inspect_db.py users`.")
            return 2
        if target_user.id == demo_uuid:
            print("ERROR: el destino ES el usuario demo. No tiene sentido.")
            return 3

        n_demo = session.execute(
            select(func.count()).select_from(Transaction).where(
                Transaction.user_id == demo_uuid
            )
        ).scalar() or 0
        n_target_before = session.execute(
            select(func.count()).select_from(Transaction).where(
                Transaction.user_id == target_user.id
            )
        ).scalar() or 0

        print(f"Origen (demo {DEMO_USER_ID}): {n_demo} transacciones.")
        print(f"Destino ({target_user.email} / {target_user.id}): "
              f"{n_target_before} transacciones existentes (se borrarán).")

        if n_demo == 0:
            print("ABORT: el usuario demo no tiene transacciones que mover. "
                  "Lanza `python scripts/init_db.py` antes para cargar el CSV.")
            return 4

        # 1. Borrar transacciones existentes del destino.
        deleted = session.execute(
            delete(Transaction).where(Transaction.user_id == target_user.id)
        ).rowcount
        print(f"Borradas {deleted} transacciones del destino.")

        # 2. Reasignar las del demo al destino.
        moved = session.execute(
            update(Transaction)
            .where(Transaction.user_id == demo_uuid)
            .values(user_id=target_user.id)
        ).rowcount
        print(f"Transferidas {moved} transacciones demo -> destino.")

    # 3. Verificación post-commit.
    with get_session() as session:
        n_target_after = session.execute(
            select(func.count()).select_from(Transaction).where(
                Transaction.user_id == target_user.id
            )
        ).scalar() or 0
        n_demo_after = session.execute(
            select(func.count()).select_from(Transaction).where(
                Transaction.user_id == demo_uuid
            )
        ).scalar() or 0
        print(f"\nResultado final:")
        print(f"  Destino: {n_target_after} transacciones.")
        print(f"  Demo:    {n_demo_after} transacciones.")

    return 0


if __name__ == "__main__":
    sys.exit(main())

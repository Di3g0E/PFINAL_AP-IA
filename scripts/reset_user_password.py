"""Reset de passphrase de un usuario existente.

Uso:
    .venv/Scripts/python.exe scripts/reset_user_password.py <email> <new_passphrase>

Hashea la passphrase con bcrypt y hace UPDATE sobre `users.passphrase_hash`.
No toca el embedding biométrico ni los settings del usuario.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bcrypt  # noqa: E402
from sqlalchemy import select  # noqa: E402

from src.data.database import get_session  # noqa: E402
from src.data.schema import User  # noqa: E402


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2

    email, new_passphrase = sys.argv[1], sys.argv[2]
    if len(new_passphrase) < 6:
        print("ERROR: la passphrase debe tener al menos 6 caracteres.")
        return 1

    new_hash = bcrypt.hashpw(new_passphrase.encode(), bcrypt.gensalt()).decode()

    with get_session() as s:
        user = s.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if user is None:
            print(f"ERROR: no existe ningún usuario con email {email!r}.")
            return 1
        user.passphrase_hash = new_hash
        print(f"OK: passphrase de {email} reseteada (user_id={user.id}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

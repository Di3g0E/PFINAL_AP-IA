"""Crea un usuario admin (sin biometría) en la BD.

Uso:
    .venv/Scripts/python.exe scripts/create_admin_user.py <email> <passphrase>

El usuario queda con `is_admin=True` y SIN embedding biométrico asociado.
Solo podrá autenticarse vía `POST /auth/login-admin` (passphrase only).
Rechaza si el email ya existe (no machaca cuentas).
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bcrypt  # noqa: E402
from sqlalchemy import select  # noqa: E402

from src.data.database import get_session  # noqa: E402
from src.data.schema import User, UserSettings  # noqa: E402


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2

    email, passphrase = sys.argv[1], sys.argv[2]
    if len(passphrase) < 6:
        print("ERROR: la passphrase debe tener al menos 6 caracteres.")
        return 1

    pwd_hash = bcrypt.hashpw(passphrase.encode(), bcrypt.gensalt()).decode()

    with get_session() as s:
        if s.execute(select(User).where(User.email == email)).scalar_one_or_none():
            print(f"ERROR: ya existe un usuario con email {email!r}.")
            return 1

        user = User(
            id=uuid.uuid4(),
            email=email,
            passphrase_hash=pwd_hash,
            biometric_consent=False,
            biometric_consent_at=None,
            role="advanced",
            is_admin=True,
            created_at=datetime.now(timezone.utc),
        )
        s.add(user)
        s.flush()
        s.add(UserSettings(
            user_id=user.id,
            notifications_enabled=False,
            notification_level="redacted",
        ))
        print(f"OK: admin creado (user_id={user.id}, email={email}, is_admin=True).")
        print("    Login -> POST /auth/login-admin con email + passphrase (sin foto).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

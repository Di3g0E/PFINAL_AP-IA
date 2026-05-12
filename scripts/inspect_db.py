"""
Vistazo rápido al estado de la base de datos.

Uso:
    python scripts/inspect_db.py                 # resumen general
    python scripts/inspect_db.py users           # detalle de la tabla users
    python scripts/inspect_db.py transactions    # detalle (paginado)
    python scripts/inspect_db.py pending         # solo status='pending'
    python scripts/inspect_db.py events          # últimos 30 eventos
    python scripts/inspect_db.py transactions <user_id>   # por usuario
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from src.data.database import get_session  # noqa: E402
from src.data.schema import (  # noqa: E402
    Event, Goal, Transaction, User, UserSettings,
)


def cmd_summary():
    with get_session() as s:
        n_users = s.execute(select(func.count()).select_from(User)).scalar() or 0
        n_settings = s.execute(select(func.count()).select_from(UserSettings)).scalar() or 0
        n_goals = s.execute(select(func.count()).select_from(Goal)).scalar() or 0
        n_events = s.execute(select(func.count()).select_from(Event)).scalar() or 0

        print("RESUMEN DE LA BD")
        print(f"  users         : {n_users}")
        print(f"  user_settings : {n_settings}")
        print(f"  goals         : {n_goals}")
        print(f"  events        : {n_events}")

        print("\nTRANSACCIONES POR STATUS")
        for status, n in s.execute(
            select(Transaction.status, func.count())
            .group_by(Transaction.status)
            .order_by(Transaction.status)
        ):
            print(f"  {status:9s} : {n}")

        print("\nULTIMOS 5 USUARIOS")
        for u in s.execute(
            select(User).order_by(User.created_at.desc()).limit(5)
        ).scalars():
            consent = "si" if u.biometric_consent else "no"
            print(f"  {u.id}  {u.email:30s}  consent={consent}  {u.created_at}")


def cmd_users():
    with get_session() as s:
        rows = s.execute(select(User).order_by(User.created_at.desc())).scalars().all()
        print(f"USUARIOS ({len(rows)})")
        for u in rows:
            consent = "si" if u.biometric_consent else "no"
            print(f"  {u.id}  {u.email:30s}  consent={consent}  {u.created_at}")


def cmd_transactions(user_id: str | None = None, limit: int = 30):
    with get_session() as s:
        stmt = select(Transaction).order_by(Transaction.created_at.desc()).limit(limit)
        if user_id:
            import uuid as _uuid
            try:
                stmt = stmt.where(Transaction.user_id == _uuid.UUID(user_id))
            except ValueError:
                print(f"user_id invalido: {user_id!r}")
                return

        rows = s.execute(stmt).scalars().all()
        print(f"TRANSACCIONES (ultimas {len(rows)})")
        for t in rows:
            area = ", ".join(t.area or [])
            desc = (t.description or "")[:50]
            print(
                f"  {t.id} | {t.date} | {str(t.amount):>10}€ "
                f"| {t.type:8s} | {t.status:9s} | {area:30s} | {desc}"
            )


def cmd_pending():
    with get_session() as s:
        rows = s.execute(
            select(Transaction)
            .where(Transaction.status == "pending")
            .order_by(Transaction.created_at.desc())
        ).scalars().all()
        print(f"PENDIENTES DE REVISION ({len(rows)})")
        for t in rows:
            area = ", ".join(t.area or [])
            reasons = "; ".join(t.anomaly_reasons or [])
            print(f"  id        : {t.id}")
            print(f"  fecha     : {t.date}")
            print(f"  importe   : {t.amount} {t.currency}")
            print(f"  area      : {area}")
            print(f"  tipo      : {t.type}")
            print(f"  motivos   : {reasons}")
            print(f"  desc      : {t.description}")
            print()


def cmd_events(limit: int = 30):
    with get_session() as s:
        rows = s.execute(
            select(Event).order_by(Event.ts.desc()).limit(limit)
        ).scalars().all()
        print(f"ULTIMOS EVENTOS ({len(rows)})")
        for e in rows:
            lat = f"{e.latency_ms}ms" if e.latency_ms else "-"
            uid = (str(e.user_id)[:8] + "...") if e.user_id else "-"
            print(
                f"  {e.ts} | {e.agent:12s} | {e.action:30s} "
                f"| {e.status:8s} | {lat:>6s} | user={uid}"
            )


def main():
    args = sys.argv[1:]
    if not args:
        cmd_summary()
        return

    cmd = args[0]
    if cmd == "users":
        cmd_users()
    elif cmd == "transactions":
        cmd_transactions(args[1] if len(args) > 1 else None)
    elif cmd == "pending":
        cmd_pending()
    elif cmd == "events":
        cmd_events()
    else:
        print(f"Comando desconocido: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()

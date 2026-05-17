from pathlib import Path
import sys
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

from sqlalchemy import inspect, text
from src.data.database import get_engine

engine = get_engine()
inspector = inspect(engine)
cols = {c['name'] for c in inspector.get_columns('users')}
if 'is_admin' in cols:
    print('is_admin exists')
else:
    print('Adding is_admin column to users')
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE users ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT 0"))
    print('Added is_admin')

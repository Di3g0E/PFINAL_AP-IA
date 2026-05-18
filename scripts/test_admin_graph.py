"""Smoke-test del endpoint admin del grafo agéntico.

Lee credenciales del entorno para no filtrar la passphrase al repo:

    set ADMIN_EMAIL=admin@pfinal.local
    set ADMIN_PASSWORD=********
    .venv\\Scripts\\python.exe scripts\\test_admin_graph.py

Si las variables no están, el script aborta con mensaje claro.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests


BASE = os.environ.get("API_BASE", "http://127.0.0.1:8000")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")


def main() -> int:
    if not ADMIN_EMAIL or not ADMIN_PASSWORD:
        print("ERROR: define ADMIN_EMAIL y ADMIN_PASSWORD en el entorno.",
              file=sys.stderr)
        return 2

    print(f"POST {BASE}/auth/login-admin")
    resp = requests.post(
        f"{BASE}/auth/login-admin",
        json={"email": ADMIN_EMAIL, "passphrase": ADMIN_PASSWORD},
        timeout=30,
    )
    print("status", resp.status_code)
    print(resp.text)
    if resp.status_code != 200:
        return 1

    token = resp.json()["access_token"]
    print("\nGET /admin/agent-graph")
    resp = requests.get(
        f"{BASE}/admin/agent-graph",
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    print("status", resp.status_code)
    print(resp.text[:500] + ("..." if len(resp.text) > 500 else ""))
    if resp.status_code != 200:
        return 1

    out = Path("artifacts/admin_graph_test.json")
    out.parent.mkdir(exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        json.dump(resp.json(), f, indent=2, ensure_ascii=False)
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

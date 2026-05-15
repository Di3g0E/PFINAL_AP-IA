"""
Diagnóstico de Langfuse: lee las keys de .env, crea una trace de prueba y
fuerza el flush. Si todo va bien, en unos segundos la verás en
cloud.langfuse.com → Tracing.

Uso (desde la raíz del proyecto):
    .venv\\Scripts\\python.exe scripts/test_langfuse.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Permite ejecutar `python scripts/test_langfuse.py` directamente.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.utils.config import settings  # noqa: E402

print("=" * 60)
print("Diagnóstico de Langfuse")
print("=" * 60)

print(f"\nValores leídos de .env:")
print(f"  LANGFUSE_PUBLIC_KEY: {settings.langfuse_public_key[:15]}..." if settings.langfuse_public_key else "  LANGFUSE_PUBLIC_KEY: (vacío)")
print(f"  LANGFUSE_SECRET_KEY: {settings.langfuse_secret_key[:15]}..." if settings.langfuse_secret_key else "  LANGFUSE_SECRET_KEY: (vacío)")
print(f"  LANGFUSE_BASE_URL:   {settings.langfuse_base_url}")
print(f"  langfuse_enabled:    {settings.langfuse_enabled}")

if not settings.langfuse_enabled:
    print("\n[ERROR] LANGFUSE_SECRET_KEY no está configurada. Aborta.")
    sys.exit(1)

# Exporta env vars como hace `_env_passthrough` en runtime
os.environ["LANGFUSE_PUBLIC_KEY"] = settings.langfuse_public_key
os.environ["LANGFUSE_SECRET_KEY"] = settings.langfuse_secret_key
os.environ["LANGFUSE_BASE_URL"] = settings.langfuse_base_url

print("\nInicializando cliente Langfuse...")
try:
    from langfuse import Langfuse, get_client
except ImportError as e:
    print(f"[ERROR] langfuse no instalado: {e}")
    sys.exit(1)

try:
    client = get_client()
    print(f"  Cliente: {client}")
except Exception as e:
    print(f"[ERROR] get_client() falló: {e}")
    sys.exit(1)

# Verifica autenticación
print("\nVerificando autenticación (auth_check)...")
try:
    auth_ok = client.auth_check()
    print(f"  auth_check: {auth_ok}")
except Exception as e:
    print(f"[ERROR] auth_check falló: {e}")
    sys.exit(1)

if not auth_ok:
    print("\n[ERROR] Autenticación con Langfuse falló. Las keys no son válidas para")
    print("  el proyecto en LANGFUSE_BASE_URL. Comprueba en cloud.langfuse.com →")
    print("  Settings → API Keys que las keys del .env coinciden con el proyecto.")
    sys.exit(1)

print("\nCreando trace de prueba (vía client.start_as_current_observation)...")
try:
    with client.start_as_current_observation(
        name="test_langfuse_smoke",
        as_type="span",
        input={"hello": "world"},
        metadata={"source": "scripts/test_langfuse.py"},
    ) as obs:
        if hasattr(obs, "update"):
            obs.update(output={"status": "ok"})
        print(f"  Observación creada: {obs}")
except Exception as e:
    import traceback
    traceback.print_exc()
    print(f"\n[ERROR] start_as_current_observation falló: {e}")
    sys.exit(1)

print("\nForzando flush al servidor...")
try:
    client.flush()
    print("  flush() completado")
except Exception as e:
    print(f"[WARNING] flush() falló: {e}")

print("\n" + "=" * 60)
print("Test completado. Comprueba cloud.langfuse.com → Tracing.")
print("Deberías ver una trace llamada 'test_langfuse_smoke' en 5-10 segundos.")
print("=" * 60)

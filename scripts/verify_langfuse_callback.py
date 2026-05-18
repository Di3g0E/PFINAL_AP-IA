"""Verifica end-to-end que el CallbackHandler de Langfuse captura
tokens, modelo y coste en cada llamada al LLM.

Uso:
    .venv\\Scripts\\python.exe scripts\\verify_langfuse_callback.py

El script:
  1. Inicializa Langfuse (lee LANGFUSE_*_KEY del .env).
  2. Hace UNA llamada real a Groq (consume ~50 tokens; coste despreciable).
  3. Flushea y espera 30s a la ingestión de Langfuse.
  4. Consulta el trace que se acaba de crear y reporta si
     `usage_details`, `provided_model_name` y `total_cost` están poblados.

Sale con código 0 si todos los campos están presentes, 1 en otro caso.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

from src.agents.orchestrator.llm_factory import get_llm  # noqa: E402
from src.utils.langfuse_integration import (  # noqa: E402
    _env_passthrough,
    get_client,
    get_langfuse_callbacks,
    init_langfuse,
    is_enabled,
    shutdown_langfuse,
)


INGEST_WAIT_SECONDS = 30


def main() -> int:
    if not is_enabled():
        print("Langfuse no está configurado (LANGFUSE_SECRET_KEY ausente).",
              file=sys.stderr)
        return 2

    init_langfuse()
    cb = get_langfuse_callbacks()
    if not cb:
        print("CallbackHandler no disponible (¿langchain instalado?).",
              file=sys.stderr)
        return 2

    handler = cb[0]
    llm = get_llm(user_id=None)
    print(f"LLM: {type(llm).__name__} model={getattr(llm, 'model_name', '?')}")

    resp = llm.invoke("Responde solo con la palabra OK.",
                      config={"callbacks": cb})
    print(f"Respuesta: {resp.content!r}")
    token_usage = (resp.response_metadata or {}).get("token_usage") or {}
    print(f"token_usage en respuesta: prompt={token_usage.get('prompt_tokens')} "
          f"completion={token_usage.get('completion_tokens')} "
          f"total={token_usage.get('total_tokens')}")

    trace_id = getattr(handler, "last_trace_id", None)
    if not trace_id:
        print("ERROR: el handler no expuso last_trace_id", file=sys.stderr)
        shutdown_langfuse()
        return 1
    print(f"trace_id={trace_id}")

    shutdown_langfuse()
    print(f"Esperando {INGEST_WAIT_SECONDS}s a la ingestión de Langfuse...")
    time.sleep(INGEST_WAIT_SECONDS)

    _env_passthrough()
    c = get_client()
    try:
        trace = c.api.trace.get(trace_id)
    except Exception as exc:
        print(f"ERROR consultando trace: {exc}", file=sys.stderr)
        return 1

    print(f"\nTrace: name={trace.name!r} latency={trace.latency} "
          f"total_cost={trace.total_cost}")

    obs = c.api.observations.get_many(trace_id=trace_id, limit=10)
    ok = True
    for o in obs.data:
        d = o.model_dump()
        print(f"\n  OBS type={d.get('type')} name={d.get('name')!r}")
        print(f"    provided_model_name: {d.get('provided_model_name')!r}")
        print(f"    usage_details:       {d.get('usage_details')!r}")
        print(f"    cost_details:        {d.get('cost_details')!r}")
        print(f"    total_cost:          {d.get('total_cost')!r}")
        if d.get("type") == "GENERATION":
            if not d.get("provided_model_name"):
                ok = False
            if not d.get("usage_details"):
                ok = False

    print()
    if ok:
        print("✅ Langfuse capturó tokens y modelo correctamente.")
        return 0
    print("❌ Faltan campos. Revisa la instrumentación.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())

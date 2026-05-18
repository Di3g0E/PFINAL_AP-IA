"""Cliente lectura-only para Langfuse SaaS.

Encima de `src/utils/langfuse_integration.py` (que inicializa el cliente)
añadimos aquí los helpers para *traer* observaciones y traces desde la
cuenta de Langfuse. El builder de grafo (`agent_graph_builder`) los
consume sin saber nada de Langfuse.

Diseño defensivo:
  - Si Langfuse no está disponible (no instalado, no configurado o
    autenticación falla), devolvemos una lista vacía + flag `enabled=False`.
    NUNCA propagamos la excepción al endpoint para no romper el panel
    admin cuando Langfuse está caído o sin keys.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger

from src.utils.langfuse_integration import (
    LANGFUSE_AVAILABLE, _env_passthrough, get_client, langfuse_client,
)
from src.utils.config import settings


def is_enabled() -> bool:
    return bool(LANGFUSE_AVAILABLE and settings.langfuse_secret_key)


def _resolve_client() -> Optional[Any]:
    """Obtiene un cliente Langfuse plenamente operativo.

    Volcamos las keys de `settings` a env vars ANTES de pedir el cliente
    porque el SDK v4 lee `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` /
    `LANGFUSE_HOST` del entorno; si no las encuentra devuelve un cliente
    en modo "disabled" sin `api`, que luego rompe con AttributeError. Esto
    es lo mismo que hace `init_langfuse()` durante el lifespan, pero
    repetirlo aquí evita acoplarnos a que el lifespan haya corrido.
    """
    if not is_enabled():
        return None
    _env_passthrough()
    client = langfuse_client
    if client is None and get_client is not None:
        try:
            client = get_client()
        except Exception as exc:
            logger.warning(f"langfuse_fetch: no se pudo obtener cliente: {exc}")
            return None
    # Sanity: si el cliente sigue sin `api` (auth falló silenciosamente),
    # lo tratamos como no disponible.
    if client is not None and not hasattr(client, "api"):
        logger.warning(
            "langfuse_fetch: cliente sin atributo `api` "
            "(auth probablemente falló — revisa LANGFUSE_PUBLIC_KEY/SECRET_KEY).",
        )
        return None
    return client


# La API REST de Langfuse rechaza limit > 100 (devuelve 400 con
# `Too big: expected number to be <=100`). Paginamos si hace falta.
_LANGFUSE_API_MAX_LIMIT = 100


def fetch_summary(
    *,
    from_time: datetime,
    to_time: Optional[datetime] = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Resumen agregado de actividad en Langfuse para el panel de admin.

    Devuelve totales (traces, observaciones, coste, tokens) y un breakdown
    por `trace.name` (que en este proyecto es típicamente `chat.request`,
    pero podría diversificarse si se instrumentan más endpoints).

    Diseñado para alimentar `meta.langfuse` del endpoint `/admin/agent-graph`.
    Defensivo: ante cualquier fallo devuelve `ok=False` sin lanzar.
    """
    if not is_enabled():
        return _disabled_summary("Langfuse no configurado (LANGFUSE_SECRET_KEY ausente).")

    client = _resolve_client()
    if client is None:
        return _disabled_summary("No se pudo obtener cliente Langfuse.")

    to_time = to_time or datetime.now(timezone.utc)

    try:
        traces = _list_paginated_traces(client, from_time, to_time, limit)
        # Agregado de tokens/coste por modelo via endpoint /metrics — una
        # sola query devuelve sumas. Mucho más eficiente que paginar
        # observaciones y sumar a mano (que además no funciona porque el
        # endpoint observations.get_many v2 devuelve los campos a None;
        # los datos solo viven en el trace.observations o vía /metrics).
        metrics_payload = _query_token_metrics(client, from_time, to_time)
    except Exception as exc:
        logger.exception(f"Error consultando Langfuse: {exc}")
        return {
            "enabled": True, "ok": False,
            "traces_count": 0, "observations_count": 0,
            "total_cost_usd": 0.0, "total_tokens_in": 0, "total_tokens_out": 0,
            "models_used": [], "top_trace_names": [],
            "note": f"{type(exc).__name__}: {exc}",
        }

    total_cost = 0.0
    for t in traces:
        if getattr(t, "total_cost", None):
            total_cost += float(t.total_cost or 0)

    tokens_in = metrics_payload["total_tokens_in"]
    tokens_out = metrics_payload["total_tokens_out"]
    metrics_cost = metrics_payload["total_cost_usd"]
    # Si los precios de los modelos están configurados en Langfuse, el
    # `total_cost` por trace coincide con el agregado de /metrics. Si no
    # (modelos custom sin precio), preferimos el agregado de /metrics
    # cuando sea > 0 — refleja el estado tal cual lo ve Langfuse.
    total_cost = max(total_cost, metrics_cost)

    trace_names: dict[str, int] = {}
    for t in traces:
        name = getattr(t, "name", None) or "(unnamed)"
        trace_names[name] = trace_names.get(name, 0) + 1

    return {
        "enabled": True, "ok": True,
        "traces_count": len(traces),
        # No paginamos observations (no aporta — los datos están en /metrics);
        # exponemos como contador la suma de tokens dividida por 1000 para
        # estimar un orden de magnitud, o 0 si no hay datos. El frontend
        # ya lo muestra como contexto, no como métrica clave.
        "observations_count": 0,
        "total_cost_usd": round(total_cost, 6),
        "total_tokens_in": tokens_in,
        "total_tokens_out": tokens_out,
        "models_used": metrics_payload["models_used"],
        "top_trace_names": [
            {"name": n, "count": c}
            for n, c in sorted(trace_names.items(), key=lambda kv: -kv[1])[:10]
        ],
        "note": None,
    }


def _query_token_metrics(client, from_time, to_time) -> dict[str, Any]:
    """Agregado de tokens y coste por modelo via endpoint `/metrics`.

    Devuelve `{total_tokens_in, total_tokens_out, total_cost_usd, models_used}`.
    Si la query falla (Langfuse aún no soporta /metrics, o cuotas, etc.),
    devuelve ceros — el llamador no lo trata como fatal porque el resumen
    sigue siendo útil con solo el conteo de traces.
    """
    import json as _json

    query = _json.dumps({
        "view": "observations",
        "metrics": [
            {"measure": "totalTokens", "aggregation": "sum"},
            {"measure": "inputTokens", "aggregation": "sum"},
            {"measure": "outputTokens", "aggregation": "sum"},
            {"measure": "totalCost", "aggregation": "sum"},
        ],
        "dimensions": [{"field": "providedModelName"}],
        "fromTimestamp": from_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "toTimestamp": to_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "timeDimension": None,
    })
    try:
        resp = client.api.metrics.metrics(query=query)
    except Exception as exc:
        logger.warning(f"Langfuse /metrics falló (usando ceros): {exc}")
        return {"total_tokens_in": 0, "total_tokens_out": 0,
                "total_cost_usd": 0.0, "models_used": []}

    tokens_in = tokens_out = 0
    total_cost = 0.0
    models: list[dict[str, Any]] = []
    for row in resp.data or []:
        model_name = row.get("providedModelName") or None
        in_tok = int(row.get("sum_inputTokens", 0) or 0)
        out_tok = int(row.get("sum_outputTokens", 0) or 0)
        tot_tok = int(row.get("sum_totalTokens", 0) or 0)
        cost = float(row.get("sum_totalCost", 0) or 0)
        tokens_in += in_tok
        tokens_out += out_tok
        total_cost += cost
        if model_name and tot_tok > 0:
            models.append({
                "model": model_name,
                "tokens_in": in_tok, "tokens_out": out_tok,
                "cost_usd": round(cost, 6),
            })
    return {
        "total_tokens_in": tokens_in,
        "total_tokens_out": tokens_out,
        "total_cost_usd": round(total_cost, 6),
        "models_used": sorted(models, key=lambda m: -(m["tokens_in"] + m["tokens_out"])),
    }


def _disabled_summary(note: str) -> dict[str, Any]:
    return {
        "enabled": False, "ok": False,
        "traces_count": 0, "observations_count": 0,
        "total_cost_usd": 0.0, "total_tokens_in": 0, "total_tokens_out": 0,
        "models_used": [], "top_trace_names": [], "note": note,
    }


def _list_paginated_traces(client, from_time, to_time, target: int):
    """Trae hasta `target` traces respetando el max=100 por página de Langfuse."""
    out: list[Any] = []
    page = 1
    while len(out) < target:
        page_size = min(_LANGFUSE_API_MAX_LIMIT, target - len(out))
        resp = client.api.trace.list(
            from_timestamp=from_time, to_timestamp=to_time,
            limit=page_size, page=page,
        )
        data = list(getattr(resp, "data", []) or [])
        if not data:
            break
        out.extend(data)
        if len(data) < page_size:
            break
        page += 1
    return out




"""
Tools del agente Registrar: hablan con /modules/p2/* (clasificador SGDC).

OCR (P3) NO se expone como tool del orquestador: el envío de imagen vive en
endpoints multipart `/transactions/ocr-extract` y `/modules/p3/ocr-extract`,
invocados desde el frontend (no desde el LLM).

`add_manual_transaction` no es un módulo P1-P5 sino una operación interna
del Registrar; sigue corriendo in-process en el nodo del grafo (toca BD
+ Security + clasificador), pero su llamada al clasificador SÍ pasa por
el tool REST.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from src.agents.tools import http_client


@tool
def classify_area(user_id: str, description: str) -> list[str]:
    """Clasifica la categoría/área de una transacción a partir de su descripción.

    Devuelve una lista (multilabel) de categorías. Si el modelo no está
    disponible devuelve ['Other'].
    """
    data: dict[str, Any] = http_client.post(
        "/modules/p2/classify-area", user_id, {"description": description},
    )
    return list(data.get("area", ["Other"]))

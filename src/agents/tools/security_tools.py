"""
Tools del agente Security: hablan con /modules/p5/* (anti-anomalía financiera).

Biometría (register/login) NO se expone como tool del orquestador: requiere
subir foto multipart desde el frontend y se gestiona en `/auth/*`.
"""

from __future__ import annotations

from datetime import date as dt_date
from decimal import Decimal
from typing import Any

from langchain_core.tools import tool

from src.agents.contracts import SecurityVerdict
from src.agents.tools import http_client


@tool
def validate_transaction(user_id: str, description: str, date: dt_date,
                         amount: float, area: list[str],
                         type: str, source: str = "manual",
                         currency: str = "EUR") -> SecurityVerdict:
    """Valida una transacción candidata contra el histórico (anti-anomalía).

    Devuelve `decision` ∈ {'allow','deny','challenge'}, con `anomaly_reasons`
    si la transacción se sale del patrón normal del usuario.
    """
    body: dict[str, Any] = {
        "description": description,
        "date": date.isoformat() if hasattr(date, "isoformat") else str(date),
        "amount": str(Decimal(str(amount))),
        "area": list(area),
        "type": type,
        "source": source,
        "currency": currency,
    }
    data = http_client.post("/modules/p5/validate-transaction", user_id, body)
    return SecurityVerdict(**data)

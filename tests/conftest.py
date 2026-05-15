"""
Configuración global de pytest para PFINAL_AP-IA.

Tras la Fase 2 (microservicios + tools REST), los agentes Registrar y
Analyst delegan en `src.agents.tools.http_client` que hace POSTs a
`/modules/*` del propio uvicorn (loopback). En tests no levantamos uvicorn.

Solución: redirigir las llamadas del http_client a las funciones
in-process correspondientes (lo que harían los routers REST si uvicorn
estuviera arriba). De esta forma los tests existentes siguen verificando
la lógica de los módulos sin necesidad de un servidor HTTP — mismo
contrato, distinto transporte.
"""

from __future__ import annotations

from typing import Any

import pytest


# Tablas de despacho path → callable. Se rellenan perezosamente para evitar
# que el import de pytest arrastre todo el sistema (paddle, torch, etc.) hasta
# el primer test que realmente lo necesita.

def _import_in_process():
    """Importa los módulos in-process bajo demanda."""
    from src.agents.analyst import agent as analyst
    from src.agents.analyst.data_source import load_user_transactions
    from src.agents.contracts import TransactionDraft
    from src.agents.registrar import agent as registrar
    from src.agents.security import agent as security_agent
    return analyst, load_user_transactions, TransactionDraft, registrar, security_agent


def _dispatch_post(path: str, user_id: str, body: dict[str, Any]) -> Any:
    """Despacho de POST /modules/* → función in-process equivalente."""
    analyst, load_user_transactions, TransactionDraft, registrar, security_agent = _import_in_process()

    # P1 — predicción temporal
    if path == "/modules/p1/predict-next-month":
        df = load_user_transactions(user_id)
        report = analyst.predict_next_month(
            df, area=body.get("area"), method=body.get("method", "rf"),
        )
        return report.model_dump(mode="json")

    # P2 — clasificador
    if path == "/modules/p2/classify-area":
        area = registrar._classify_area(user_id, body["description"])
        return {"description": body["description"], "area": area}

    # P4 — analytics
    if path == "/modules/p4/monthly-summary":
        df = load_user_transactions(user_id)
        return analyst.monthly_summary(df, year=body.get("year"), month=body.get("month")).model_dump(mode="json")
    if path == "/modules/p4/category-breakdown":
        df = load_user_transactions(user_id)
        return analyst.category_breakdown(df, period=body.get("period")).model_dump(mode="json")
    if path == "/modules/p4/spending-trends":
        df = load_user_transactions(user_id)
        return analyst.spending_trends(df, n_months=body.get("n_months", 6)).model_dump(mode="json")
    if path == "/modules/p4/savings-rate":
        df = load_user_transactions(user_id)
        return analyst.savings_rate(df, n_months=body.get("n_months", 6)).model_dump(mode="json")
    if path == "/modules/p4/detect-anomalies":
        df = load_user_transactions(user_id)
        return analyst.detect_anomalies(df).model_dump(mode="json")
    if path == "/modules/p4/recurring-expenses":
        df = load_user_transactions(user_id)
        return analyst.recurring_expenses(df).model_dump(mode="json")
    if path == "/modules/p4/recent-transactions":
        df = load_user_transactions(user_id)
        return analyst.recent_transactions(df, n=body.get("n", 10)).model_dump(mode="json")
    if path == "/modules/p4/check-goals":
        df = load_user_transactions(user_id)
        return analyst.check_goals(df, user_id=user_id).model_dump(mode="json")
    if path == "/modules/p4/goals":
        return analyst.set_goal(
            user_id, area=body["area"], max_amount=body["max_amount"],
            period=body.get("period", "monthly"),
        ).model_dump(mode="json")

    # P5 — security
    if path == "/modules/p5/validate-transaction":
        from decimal import Decimal
        from datetime import date as _date
        d = body.get("date")
        if isinstance(d, str):
            d = _date.fromisoformat(d)
        draft = TransactionDraft(
            user_id=user_id,
            description=body["description"], date=d,
            amount=Decimal(str(body["amount"])),
            area=list(body.get("area", [])),
            type=body["type"], source=body.get("source", "manual"),
            currency=body.get("currency", "EUR"),
        )
        return security_agent.validate_transaction(draft).model_dump(mode="json")

    raise NotImplementedError(f"conftest: dispatch POST no implementado para {path}")


def _dispatch_get(path: str, user_id: str, params: dict[str, Any] | None) -> Any:
    analyst, *_ = _import_in_process()
    if path == "/modules/p4/goals":
        return analyst.list_goals(user_id).model_dump(mode="json")
    raise NotImplementedError(f"conftest: dispatch GET no implementado para {path}")


def _dispatch_delete(path: str, user_id: str) -> Any:
    analyst, *_ = _import_in_process()
    if path.startswith("/modules/p4/goals/"):
        area = path.rsplit("/", 1)[-1]
        return analyst.remove_goal(user_id, area=area).model_dump(mode="json")
    raise NotImplementedError(f"conftest: dispatch DELETE no implementado para {path}")


@pytest.fixture(autouse=True)
def _stub_http_client_with_inprocess(monkeypatch):
    """Reemplaza `http_client.post/get/delete` por dispatches in-process.

    Los routers REST de `/modules/*` quedan cortocircuitados a las funciones
    Python que ellos llaman internamente. Mismo resultado JSON, sin túnel
    HTTP loopback (que requeriría uvicorn corriendo).
    """
    from src.agents.tools import http_client

    monkeypatch.setattr(http_client, "post", _dispatch_post)
    monkeypatch.setattr(http_client, "get", _dispatch_get)
    monkeypatch.setattr(http_client, "delete", _dispatch_delete)

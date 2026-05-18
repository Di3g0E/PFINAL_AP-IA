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

E1 — HybridClassifier (transformer + SGD personal): para que los tests
no necesiten descargar `sentence-transformers` (~120 MB), inyectamos un
`FakeEmbedder` determinista. Esto valida la lógica de orquestación
(zero-shot vs personal, bootstrap, partial_fit, persistencia) sin
modelo real. Tests dedicados (test_classifier_hybrid.py) verifican la
calidad semántica con el modelo real cuando está disponible.
"""

from __future__ import annotations

import hashlib
from typing import Any

import numpy as np
import pytest


@pytest.fixture(autouse=True)
def _disable_langfuse_for_tests(monkeypatch):
    """Langfuse OFF durante toda la suite.

    Motivo: si las keys reales están en `.env` (lo habitual en local),
    cada `llm.invoke(...)` con el CallbackHandler abriría conexiones a
    cloud.langfuse.com — ralentiza la suite y ensucia el dashboard con
    runs sintéticos. `get_langfuse_callbacks()` y `start_observation()`
    ya manejan el caso "sin secret key" devolviendo lista vacía /
    `nullcontext` respectivamente, así que vaciar el setting basta.
    """
    monkeypatch.setattr("src.utils.config.settings.langfuse_secret_key", "")
    monkeypatch.setattr("src.utils.config.settings.langfuse_public_key", "")


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

    # P2 — clasificador (E1: HybridClassifier con metadatos)
    if path == "/modules/p2/classify-area":
        result = registrar.classify_area_full(user_id, body["description"])
        return {"description": body["description"], **result}

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


# E1 — Fake embedder determinista para tests sin descargar el transformer real


class FakeEmbedder:
    """Embedder determinista: hash de cada token → vector L2-normalizado.

    Suficiente para validar la lógica del HybridClassifier (orquestación,
    persistencia, partial_fit, bootstrap) sin necesidad de descargar
    el modelo de sentence-transformers. NO mide calidad semántica.

    Estrategia: para cada texto, sumamos vectores deterministas obtenidos
    de los tokens (token hash → posición en el espacio). Textos
    "similares" comparten tokens → vectores parecidos. La firma `encode`
    coincide con la del backend real.
    """

    DIM = 384

    def __init__(self, seed: int = 0):
        self._seed = seed

    def _token_vec(self, token: str) -> np.ndarray:
        h = hashlib.sha256(f"{self._seed}:{token}".encode()).digest()
        # Construye un vector de DIM bytes partiendo del hash repetido
        raw = (h * (self.DIM // len(h) + 1))[: self.DIM]
        vec = np.frombuffer(raw, dtype=np.uint8).astype(np.float32)
        return vec - 127.5  # centra alrededor de 0

    def encode(self, texts, normalize_embeddings: bool = True) -> np.ndarray:
        out = np.zeros((len(texts), self.DIM), dtype=np.float32)
        for i, text in enumerate(texts):
            tokens = (text or "").lower().split()
            if not tokens:
                out[i] = np.ones(self.DIM, dtype=np.float32) / self.DIM ** 0.5
                continue
            vec = sum(self._token_vec(t) for t in tokens) / len(tokens)
            if normalize_embeddings:
                norm = np.linalg.norm(vec)
                if norm > 0:
                    vec = vec / norm
            out[i] = vec
        return out


@pytest.fixture(autouse=True)
def _inject_fake_embedder_for_e1(tmp_path, monkeypatch):
    """Inyecta el embedder fake y aísla la persistencia de modelos personales.

    Cada test recibe:
      - TransformerEmbedder.shared() → FakeEmbedder (no descarga nada)
      - models/personal/ apuntando a tmp_path/personal/ (no contamina disco)
      - HybridClassifier + Registry reseteados (sin caché entre tests)
    """
    from src.agents.registrar import classifier_personal
    from src.agents.registrar.classifier_hybrid import HybridClassifier
    from src.agents.registrar.classifier_personal import PersonalClassifierRegistry
    from src.agents.registrar.embedder import TransformerEmbedder

    TransformerEmbedder.reset()
    TransformerEmbedder.set_for_tests(FakeEmbedder())

    personal_dir = tmp_path / "personal"
    personal_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(classifier_personal, "PERSONAL_DIR", personal_dir)

    PersonalClassifierRegistry.reset()
    HybridClassifier.reset()
    # Restablece la bandera `_HYBRID_DISABLED` del registrar entre tests
    from src.agents.registrar import agent as _reg_agent
    _reg_agent._HYBRID_DISABLED = False
    # Limpia la cache del FinancialAnomalyDetector. Sin esto, un test que
    # mockea `load_user_history_db_only` puede ver un detector entrenado
    # con datos del test anterior (el cache se hace por user_id, y los
    # tests reutilizan ids como 'not-a-uuid').
    from src.agents.security import agent as _sec_agent
    _sec_agent._DETECTOR_CACHE.clear()
    yield
    TransformerEmbedder.reset()
    PersonalClassifierRegistry.reset()
    HybridClassifier.reset()
    _sec_agent._DETECTOR_CACHE.clear()

"""
Configuración compartida de pytest.

Como los tools de los agentes hacen POST a /modules/* via HTTP, en los tests
los redirigimos a las funciones equivalentes in-process. Así no necesitamos
levantar uvicorn para que pasen los tests.

También desactivamos Langfuse (evita que cada llm.invoke abra una conexión
a cloud.langfuse.com) e inyectamos un embedder falso (evita descargar el
modelo de sentence-transformers de ~120 MB).
"""
from __future__ import annotations

import hashlib
from typing import Any

import numpy as np
import pytest


# Sin Langfuse en los tests — si las claves reales están en .env, cada llamada
# al LLM intentaría abrir HTTPS a cloud.langfuse.com.
@pytest.fixture(autouse=True)
def _disable_langfuse(monkeypatch):
    monkeypatch.setattr("src.utils.config.settings.langfuse_secret_key", "")
    monkeypatch.setattr("src.utils.config.settings.langfuse_public_key", "")


# --- Dispatch in-process de /modules/* (para no tener que levantar uvicorn) ---

def _imports():
    from src.agents.analyst import agent as analyst
    from src.agents.analyst.data_source import load_user_transactions
    from src.agents.contracts import TransactionDraft
    from src.agents.registrar import agent as registrar
    from src.agents.security import agent as security_agent
    return analyst, load_user_transactions, TransactionDraft, registrar, security_agent


def _post(path: str, user_id: str, body: dict[str, Any]) -> Any:
    analyst, load_user_transactions, TransactionDraft, registrar, security_agent = _imports()

    if path == "/modules/p2/classify-area":
        result = registrar.classify_area_full(user_id, body["description"])
        return {"description": body["description"], **result}

    if path == "/modules/p4/monthly-summary":
        df = load_user_transactions(user_id)
        return analyst.monthly_summary(df, year=body.get("year"),
                                       month=body.get("month")).model_dump(mode="json")
    if path == "/modules/p4/category-breakdown":
        df = load_user_transactions(user_id)
        return analyst.category_breakdown(df, period=body.get("period")).model_dump(mode="json")
    if path == "/modules/p4/detect-anomalies":
        df = load_user_transactions(user_id)
        return analyst.detect_anomalies(df).model_dump(mode="json")

    if path == "/modules/p5/validate-transaction":
        from datetime import date as _date
        from decimal import Decimal
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

    raise NotImplementedError(f"dispatch POST no implementado: {path}")


@pytest.fixture(autouse=True)
def _stub_http_client(monkeypatch):
    """Mockea http_client.post para que los tools de los agentes
    llamen a las funciones in-process en vez de hacer HTTP a localhost."""
    from src.agents.tools import http_client
    monkeypatch.setattr(http_client, "post", _post)


# --- Embedder falso (E1 — no descargar el modelo real) ---

class FakeEmbedder:
    """Devuelve vectores deterministas (hash → posición en el espacio).

    No mide calidad semántica pero sí permite probar la lógica del
    HybridClassifier (zero-shot, partial_fit, persistencia).
    """
    DIM = 384

    def _token_vec(self, token: str) -> np.ndarray:
        h = hashlib.sha256(token.encode()).digest()
        raw = (h * (self.DIM // len(h) + 1))[: self.DIM]
        return np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 127.5

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
def _fake_embedder(tmp_path, monkeypatch):
    from src.agents.registrar import classifier_personal
    from src.agents.registrar.classifier_hybrid import HybridClassifier
    from src.agents.registrar.classifier_personal import PersonalClassifierRegistry
    from src.agents.registrar.embedder import TransformerEmbedder

    TransformerEmbedder.reset()
    TransformerEmbedder.set_for_tests(FakeEmbedder())
    monkeypatch.setattr(classifier_personal, "PERSONAL_DIR", tmp_path / "personal")
    PersonalClassifierRegistry.reset()
    HybridClassifier.reset()

    # Reset detector cache (otros tests pueden haber entrenado con datos distintos)
    from src.agents.security import agent as _sec
    _sec._DETECTOR_CACHE.clear()
    yield
    TransformerEmbedder.reset()
    PersonalClassifierRegistry.reset()
    HybridClassifier.reset()
    _sec._DETECTOR_CACHE.clear()

"""
Tests de integración de la API FastAPI.

Estrategia:
  - SQLite real en tmp_path (BD aislada por test).
  - BiometricPipeline mockeado (sin descargar 150 MB de modelos).
  - LLM mockeado a nivel de orquestador (sin pegar a Groq).
  - JWT real: el flujo register → login → chat usa tokens auténticos.

Cubre:
  - /auth/register (consent + biometría)
  - /auth/login (passphrase + biometría)
  - /auth con credenciales malas
  - /chat con y sin token
  - /transactions: alta manual normal y anómala
  - /transactions/pending: list + confirm + reject
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import numpy as np
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from src.agents.contracts import OrchestratorDecision
from src.agents.security import agent as security_agent
from src.agents.security.biometrics import FaceFeatures
from src.api.main import app
from src.data.database import get_engine, init_db, reset_engine
from src.utils.security import AccessController, EncryptedEmbeddingStore


# Helpers

def _unit_embedding(rng: np.random.Generator) -> np.ndarray:
    v = rng.standard_normal(512).astype(np.float32)
    return v / np.linalg.norm(v)


class _FakeStructuredLLM:
    def __init__(self, decisions: list[OrchestratorDecision]):
        self._decisions = list(decisions)

    def invoke(self, _messages):
        if not self._decisions:
            raise RuntimeError("FakeStructuredLLM: sin decisiones")
        return self._decisions.pop(0)


class _FakeLLM:
    def __init__(self, decisions=None, narrations=None):
        self._structured = _FakeStructuredLLM(decisions or [])
        self._narrations = list(narrations or [])

    def with_structured_output(self, _schema):
        return self._structured

    def invoke(self, _messages):
        if not self._narrations:
            raise RuntimeError("FakeLLM: sin narraciones")
        return AIMessage(content=self._narrations.pop(0))


# Fixtures

@pytest.fixture
def api_env(monkeypatch, tmp_path):
    """
    Aísla cada test:
      - SQLite limpio en tmp_path
      - JWT_SECRET aleatorio
      - MASTER_FERNET_KEY aleatoria (cifrado de API keys de LLM)
      - BiometricPipeline mockeado (no descarga modelos)
      - EncryptedEmbeddingStore en tmp_path (no contamina otras pruebas)
      - AccessController limpio
      - Reset del singleton del grafo (chat) para que cada test pueda mockear
        el LLM sin contaminación previa
    """
    # BD aislada
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("src.utils.config.settings.database_url",
                        f"sqlite:///{db_path}")
    monkeypatch.setattr("src.utils.config.settings.jwt_secret",
                        "test-secret-" + uuid.uuid4().hex)

    os.environ["MASTER_FERNET_KEY"] = Fernet.generate_key().decode()

    reset_engine()
    get_engine()
    init_db()

    # Embedding store en tmp_path
    fresh_store = EncryptedEmbeddingStore(
        db_path=tmp_path / "face_embeddings.bin",
        passphrase="test-pass",
    )
    monkeypatch.setattr("src.agents.security.agent._EMBEDDING_STORE", fresh_store)
    monkeypatch.setattr("src.agents.security.agent._get_embedding_store",
                        lambda: fresh_store)

    # Lockout limpio
    monkeypatch.setattr("src.agents.security.agent._ACCESS_CONTROLLER",
                        AccessController(max_attempts=3, lockout_seconds=60))

    # Reset del grafo singleton del chat
    monkeypatch.setattr("src.api.routers.chat._GRAPH", None, raising=False)

    yield {
        "rng": np.random.default_rng(42),
        "tmp_path": tmp_path,
    }

    reset_engine()


def _patch_biometrics(monkeypatch, *, embedding: np.ndarray, liveness: float = 0.99):
    """Sustituye el pipeline biométrico por uno fake (sin descargas)."""
    fake = FaceFeatures(
        embedding=embedding, liveness_score=liveness,
        is_live=liveness >= 0.95, face_confidence=0.99,
    )

    class _FakePipeline:
        @staticmethod
        def shared():
            return _FakePipeline()

        def extract(self, _image):
            return fake

        @staticmethod
        def cosine_similarity(a, b):
            return float(np.dot(a, b))

    monkeypatch.setattr("src.agents.security.biometrics.BiometricPipeline", _FakePipeline)
    monkeypatch.setattr(
        "src.agents.security.biometrics.decode_image_bytes",
        lambda _b: np.zeros((10, 10, 3), dtype=np.uint8),
    )


def _patch_llm(monkeypatch, *, decisions: list[OrchestratorDecision], narrations: list[str]):
    """Mockea el LLM de los nodos del orquestador."""
    fake = _FakeLLM(decisions=decisions, narrations=narrations)
    monkeypatch.setattr("src.agents.orchestrator.nodes.get_llm", lambda **kw: fake)


@pytest.fixture
def client(api_env):
    return TestClient(app)


def _register_and_login(client: TestClient, monkeypatch, rng) -> tuple[str, str]:
    """Helper común: register + login con la misma cara → devuelve (token, user_id)."""
    emb = _unit_embedding(rng)
    _patch_biometrics(monkeypatch, embedding=emb)

    r = client.post(
        "/auth/register",
        data={"email": "alice@p6.local", "passphrase": "hunter22",
              "biometric_consent": "true"},
        files={"face": ("face.jpg", b"x", "image/jpeg")},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    return body["access_token"], body["user_id"]


# Tests de healthcheck y auth

def test_healthcheck(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_register_requires_consent(api_env, monkeypatch):
    _patch_biometrics(monkeypatch, embedding=_unit_embedding(api_env["rng"]))
    client = TestClient(app)
    r = client.post(
        "/auth/register",
        data={"email": "x@p6.local", "passphrase": "hunter2",
              "biometric_consent": "false"},
        files={"face": ("face.jpg", b"x", "image/jpeg")},
    )
    assert r.status_code == 400
    assert "consent" in r.json()["detail"].lower() or "consenti" in r.json()["detail"].lower()


def test_register_then_login_returns_jwt(api_env, monkeypatch):
    rng = api_env["rng"]
    emb = _unit_embedding(rng)
    _patch_biometrics(monkeypatch, embedding=emb)
    client = TestClient(app)

    r = client.post(
        "/auth/register",
        data={"email": "alice@p6.local", "passphrase": "hunter22",
              "biometric_consent": "true"},
        files={"face": ("face.jpg", b"x", "image/jpeg")},
    )
    assert r.status_code == 201, r.text
    register_body = r.json()
    assert register_body["access_token"]
    uuid.UUID(register_body["user_id"])    # debe ser UUID válido

    # Login con la misma cara
    r = client.post(
        "/auth/login",
        data={"email": "alice@p6.local", "passphrase": "hunter22"},
        files={"face": ("face.jpg", b"x", "image/jpeg")},
    )
    assert r.status_code == 200, r.text
    login_body = r.json()
    assert login_body["user_id"] == register_body["user_id"]
    assert login_body["similarity"] is not None


def test_login_wrong_passphrase_returns_401(api_env, monkeypatch):
    rng = api_env["rng"]
    _patch_biometrics(monkeypatch, embedding=_unit_embedding(rng))
    client = TestClient(app)
    client.post(
        "/auth/register",
        data={"email": "bob@p6.local", "passphrase": "real-pwd",
              "biometric_consent": "true"},
        files={"face": ("face.jpg", b"x", "image/jpeg")},
    )

    r = client.post(
        "/auth/login",
        data={"email": "bob@p6.local", "passphrase": "WRONG"},
        files={"face": ("face.jpg", b"x", "image/jpeg")},
    )
    assert r.status_code == 401


# Tests de protección con JWT

def test_chat_without_token_returns_401(client):
    r = client.post("/chat", json={"message": "hola"})
    assert r.status_code == 401


def test_chat_with_invalid_token_returns_401(client):
    r = client.post(
        "/chat",
        headers={"Authorization": "Bearer not-a-real-jwt"},
        json={"message": "hola"},
    )
    assert r.status_code == 401


def test_chat_with_valid_token(api_env, monkeypatch):
    rng = api_env["rng"]
    client = TestClient(app)
    token, _user_id = _register_and_login(client, monkeypatch, rng)

    # LLM mock: dice respond_final directamente con un mensaje
    _patch_llm(
        monkeypatch,
        decisions=[OrchestratorDecision(
            action="respond_final",
            user_message="Hola, ¿en qué te ayudo?",
        )],
        narrations=[],
    )

    r = client.post(
        "/chat",
        headers={"Authorization": f"Bearer {token}"},
        json={"message": "hola"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "ayudo" in body["response"].lower()
    assert body["last_action"] == "respond_final"
    assert body["session_id"]


# Tests de transacciones

def test_post_transaction_normal_returns_accepted(api_env, monkeypatch):
    rng = api_env["rng"]
    client = TestClient(app)
    token, _ = _register_and_login(client, monkeypatch, rng)

    r = client.post(
        "/transactions",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "description": "Café del lunes",
            "date": "2026-04-30",
            "amount": "3.50",
            "type": "Expenses",
            "area": ["Food"],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["accepted"]) == 1
    assert body["accepted"][0]["status"] == "accepted"
    assert body["accepted"][0]["amount"] == "3.50"


def test_pending_review_lifecycle(api_env, monkeypatch):
    """
    Flujo completo:
      1. Sembramos histórico para que el detector funcione.
      2. Insertamos transacción anómala → status='pending'.
      3. GET /transactions/pending → la lista la devuelve.
      4. POST /pending/{id}/confirm → status='accepted'.
      5. Listado vacío.
    """
    from datetime import date as _date
    from decimal import Decimal as _D
    from sqlalchemy import select

    from src.agents.contracts import ManualEntry
    from src.agents.registrar import agent as registrar
    from src.data.database import get_session
    from src.data.schema import Transaction, User

    rng = api_env["rng"]
    client = TestClient(app)
    token, user_id = _register_and_login(client, monkeypatch, rng)

    # Sembramos histórico directamente (30 transacciones razonables)
    user_uuid = uuid.UUID(user_id)
    with get_session() as s:
        for i in range(20):
            s.add(Transaction(
                user_id=user_uuid, description=f"Café {i}",
                date=_date(2026, 3, 1 + (i % 28)),
                amount=_D("3.00"), area=["Food"],
                type="Expenses", source="import", status="accepted",
            ))
        for i in range(10):
            s.add(Transaction(
                user_id=user_uuid, description=f"Nómina {i}",
                date=_date(2026, 1 + (i % 4), 1),
                amount=_D("2000.00"), area=["Salary"],
                type="Income", source="import", status="accepted",
            ))

    # Anómala: 80 000 EUR en Leisure
    r = client.post(
        "/transactions",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "description": "Yate inesperado",
            "date": "2026-04-30",
            "amount": "80000.00",
            "type": "Expenses",
            "area": ["Leisure"],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["pending_review"]) == 1
    pending_id = body["pending_review"][0]["record"]["id"]

    # GET pending
    r = client.get("/transactions/pending",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    listed = r.json()
    assert len(listed) == 1
    assert listed[0]["record"]["id"] == pending_id

    # Confirm
    r = client.post(f"/transactions/pending/{pending_id}/confirm",
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["status"] == "accepted"

    # Lista ya vacía
    r = client.get("/transactions/pending",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.json() == []


def test_reject_pending_unknown_returns_404(api_env, monkeypatch):
    rng = api_env["rng"]
    client = TestClient(app)
    token, _ = _register_and_login(client, monkeypatch, rng)

    fake_id = str(uuid.uuid4())
    r = client.delete(f"/transactions/pending/{fake_id}",
                      headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 404


def test_confirm_invalid_uuid_returns_400(api_env, monkeypatch):
    rng = api_env["rng"]
    client = TestClient(app)
    token, _ = _register_and_login(client, monkeypatch, rng)

    r = client.post("/transactions/pending/not-a-uuid/confirm",
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 400

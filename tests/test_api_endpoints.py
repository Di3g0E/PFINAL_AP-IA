"""
Tests de integración de la API.

Cubren el flujo básico: healthcheck, registro/login con biometría mockeada,
y un chat simple. La biometría real necesitaría descargar ~150 MB de
modelos y el LLM real exigiría API key; por eso van mockeados.
"""
from __future__ import annotations

import os
import uuid

import numpy as np
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from src.agents.contracts import OrchestratorDecision
from src.agents.security.biometrics import FaceFeatures
from src.api.main import app
from src.data.database import get_engine, init_db, reset_engine
from src.utils.security import AccessController, EncryptedEmbeddingStore


# Helpers

def _embedding(rng):
    v = rng.standard_normal(512).astype(np.float32)
    return v / np.linalg.norm(v)


class _FakeLLM:
    """LLM falso: devuelve decisiones y narraciones programadas."""

    def __init__(self, decisions=None, narrations=None):
        self._decisions = list(decisions or [])
        self._narrations = list(narrations or [])

    def with_structured_output(self, _schema):
        return self  # devolvemos self para que .invoke() siga funcionando

    def invoke(self, _messages, **_kw):
        if self._decisions:
            return self._decisions.pop(0)
        if self._narrations:
            return AIMessage(content=self._narrations.pop(0))
        raise RuntimeError("FakeLLM: sin respuestas programadas")


# Fixtures

@pytest.fixture
def env(monkeypatch, tmp_path):
    """SQLite limpio + JWT/Fernet aleatorios + biometría mockeada."""
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("src.utils.config.settings.database_url", f"sqlite:///{db_path}")
    monkeypatch.setattr("src.utils.config.settings.jwt_secret", "test-" + uuid.uuid4().hex)
    os.environ["MASTER_FERNET_KEY"] = Fernet.generate_key().decode()

    reset_engine()
    get_engine()
    init_db()

    store = EncryptedEmbeddingStore(
        db_path=tmp_path / "face_embeddings.bin", passphrase="test-pass",
    )
    monkeypatch.setattr("src.agents.security.agent._EMBEDDING_STORE", store)
    monkeypatch.setattr("src.agents.security.agent._get_embedding_store",
                        lambda: store)
    monkeypatch.setattr("src.agents.security.agent._ACCESS_CONTROLLER",
                        AccessController(max_attempts=3, lockout_seconds=60))
    monkeypatch.setattr("src.api.routers.chat._GRAPH", None, raising=False)

    rng = np.random.default_rng(42)
    yield rng
    reset_engine()


def _mock_biometrics(monkeypatch, embedding):
    """Sustituye el pipeline biométrico por uno que devuelve siempre el mismo embedding."""
    fake = FaceFeatures(embedding=embedding, liveness_score=0.99,
                        is_live=True, face_confidence=0.99)

    class _FakePipe:
        @staticmethod
        def shared():
            return _FakePipe()

        def extract(self, _img):
            return fake

        @staticmethod
        def cosine_similarity(a, b):
            return float(np.dot(a, b))

    monkeypatch.setattr("src.agents.security.biometrics.BiometricPipeline", _FakePipe)
    monkeypatch.setattr("src.agents.security.biometrics.decode_image_bytes",
                        lambda _b: np.zeros((10, 10, 3), dtype=np.uint8))


def _register(client, monkeypatch, rng, email="alice@p6.local"):
    """Registra un usuario y devuelve (token, user_id)."""
    _mock_biometrics(monkeypatch, _embedding(rng))
    r = client.post(
        "/auth/register",
        data={"email": email, "passphrase": "hunter22", "biometric_consent": "true"},
        files={"face": ("face.jpg", b"x", "image/jpeg")},
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"], r.json()["user_id"]


# Tests

def test_healthcheck(env):
    r = TestClient(app).get("/")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_register_and_login_returns_jwt(env, monkeypatch):
    client = TestClient(app)
    token, user_id = _register(client, monkeypatch, env)
    assert token
    uuid.UUID(user_id)  # debe ser UUID válido

    # Login con la misma cara
    r = client.post(
        "/auth/login",
        data={"email": "alice@p6.local", "passphrase": "hunter22"},
        files={"face": ("face.jpg", b"x", "image/jpeg")},
    )
    assert r.status_code == 200
    assert r.json()["user_id"] == user_id


def test_register_without_consent_returns_400(env, monkeypatch):
    _mock_biometrics(monkeypatch, _embedding(env))
    client = TestClient(app)
    r = client.post(
        "/auth/register",
        data={"email": "x@p6.local", "passphrase": "hunter22", "biometric_consent": "false"},
        files={"face": ("face.jpg", b"x", "image/jpeg")},
    )
    assert r.status_code == 400


def test_chat_without_token_returns_401(env):
    r = TestClient(app).post("/chat", json={"message": "hola"})
    assert r.status_code == 401


def test_chat_basic_response(env, monkeypatch):
    """El usuario manda un mensaje y el chat devuelve la respuesta del LLM."""
    client = TestClient(app)
    token, _ = _register(client, monkeypatch, env)

    # Mockeamos el LLM para que el router decida "respond_final" directamente
    fake = _FakeLLM(
        decisions=[OrchestratorDecision(action="respond_final",
                                        user_message="Hola, ¿en qué te ayudo?")],
    )
    monkeypatch.setattr("src.agents.orchestrator.nodes.get_llm", lambda **kw: fake)

    r = client.post(
        "/chat",
        headers={"Authorization": f"Bearer {token}"},
        json={"message": "hola"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "ayudo" in body["response"].lower()
    assert body["session_id"]

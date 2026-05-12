"""
Tests del flujo register_user / login_user del agente Security.

Estrategia: NO descargamos los modelos reales (~150 MB de FaceNet + DenseNet201).
Mockeamos `BiometricPipeline.shared().extract` con `FakeFeatures` que simula:
  - rostro detectado con confianza alta
  - liveness > umbral (live)
  - embedding L2-normalizado de 512-D

Cada test usa una BD SQLite limpia + master Fernet key generada al vuelo.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import numpy as np
import pytest
from cryptography.fernet import Fernet

from src.agents.contracts import LoginRequest, RegisterRequest
from src.agents.security import agent as security
from src.agents.security.biometrics import FaceFeatures
from src.data.database import get_engine, init_db, reset_engine


def _random_unit_embedding(rng: np.random.Generator) -> np.ndarray:
    """Vector aleatorio L2-normalizado de 512-D."""
    v = rng.standard_normal(512).astype(np.float32)
    return v / np.linalg.norm(v)


@pytest.fixture
def biometric_env(monkeypatch, tmp_path):
    """
    Aísla cada test con:
      - SQLite real en tmp_path (BD)
      - data_dir en tmp_path (fichero face_embeddings.bin no contamina otras pruebas)
      - MASTER_FERNET_KEY generada al vuelo
      - BiometricPipeline mockeado para no descargar modelos
      - AccessController limpio
      - EmbeddingStore limpio
    """
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("src.utils.config.settings.database_url",
                        f"sqlite:///{db_path}")

    # Fernet key para cifrado de API keys (módulo lo importa al cargar)
    os.environ["MASTER_FERNET_KEY"] = Fernet.generate_key().decode()

    # Reset BD
    reset_engine()
    get_engine()
    init_db()

    # Apuntamos el almacén de embeddings a un fichero del tmp_path para que
    # cada test parta de un store limpio. `data_dir` es read-only en Settings;
    # parchamos `_get_embedding_store` para que use esta ruta.
    from src.utils.security import EncryptedEmbeddingStore as _Store
    fresh_store = _Store(
        db_path=tmp_path / "face_embeddings.bin",
        passphrase="test-passphrase",
    )
    monkeypatch.setattr("src.agents.security.agent._EMBEDDING_STORE", fresh_store)
    monkeypatch.setattr("src.agents.security.agent._get_embedding_store",
                        lambda: fresh_store)

    # Reset del lockout (nuevo controller con max_attempts=3 para tests rápidos)
    monkeypatch.setattr("src.agents.security.agent._ACCESS_CONTROLLER",
                        security.AccessController(max_attempts=3, lockout_seconds=60))

    rng = np.random.default_rng(42)
    yield rng

    reset_engine()


def _patch_pipeline(monkeypatch, *, embedding: np.ndarray, liveness: float = 0.99):
    """Reemplaza el pipeline biométrico por una versión que no toca la red ni los modelos."""
    fake = FaceFeatures(
        embedding=embedding,
        liveness_score=liveness,
        is_live=liveness >= 0.95,
        face_confidence=0.99,
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

    monkeypatch.setattr("src.agents.security.agent.BiometricPipeline", _FakePipeline,
                        raising=False)
    # Sustituir también la importación tardía dentro de register_user/login_user.
    # Lo hacemos vía sys.modules: el agente las importa con `from ... import` dentro
    # de la función, así que monkeypatcheamos el módulo entero `biometrics`.
    monkeypatch.setattr("src.agents.security.biometrics.BiometricPipeline", _FakePipeline)

    # `decode_image_bytes` también se importa dentro de las funciones; lo
    # convertimos en no-op porque los tests usan bytes ficticios (b"fakebytes").
    monkeypatch.setattr(
        "src.agents.security.biometrics.decode_image_bytes",
        lambda _b: np.zeros((10, 10, 3), dtype=np.uint8),
    )


# Tests

def test_register_requires_consent(biometric_env, monkeypatch):
    rng = biometric_env
    _patch_pipeline(monkeypatch, embedding=_random_unit_embedding(rng))

    req = RegisterRequest(
        email="alice@p6.local", passphrase="hunter2",
        face_image=b"fakebytes", biometric_consent=False,
    )
    verdict = security.register_user(req)
    assert verdict.decision == "deny"
    assert "consent" in verdict.reason.lower() or "consenti" in verdict.reason.lower()


def test_register_rejects_low_liveness(biometric_env, monkeypatch):
    rng = biometric_env
    _patch_pipeline(monkeypatch, embedding=_random_unit_embedding(rng), liveness=0.10)

    req = RegisterRequest(
        email="bob@p6.local", passphrase="hunter2",
        face_image=b"fakebytes", biometric_consent=True,
    )
    verdict = security.register_user(req)
    assert verdict.decision == "deny"
    assert "liveness" in verdict.reason.lower()


def test_register_success_creates_user(biometric_env, monkeypatch):
    rng = biometric_env
    _patch_pipeline(monkeypatch, embedding=_random_unit_embedding(rng))

    req = RegisterRequest(
        email="charlie@p6.local", passphrase="hunter2",
        face_image=b"fakebytes", biometric_consent=True,
    )
    verdict = security.register_user(req)
    assert verdict.decision == "allow"
    assert verdict.user_id is not None
    uuid.UUID(verdict.user_id)


def test_register_duplicate_email_rejected(biometric_env, monkeypatch):
    rng = biometric_env
    _patch_pipeline(monkeypatch, embedding=_random_unit_embedding(rng))
    req = RegisterRequest(
        email="dup@p6.local", passphrase="hunter2",
        face_image=b"fakebytes", biometric_consent=True,
    )
    assert security.register_user(req).decision == "allow"
    second = security.register_user(req)
    assert second.decision == "deny"
    assert "registrado" in second.reason.lower()


def test_login_success_with_same_face(biometric_env, monkeypatch):
    rng = biometric_env
    emb = _random_unit_embedding(rng)
    _patch_pipeline(monkeypatch, embedding=emb)

    reg = security.register_user(RegisterRequest(
        email="dave@p6.local", passphrase="hunter2",
        face_image=b"x", biometric_consent=True,
    ))
    assert reg.decision == "allow"

    login = security.login_user(LoginRequest(
        email="dave@p6.local", passphrase="hunter2", face_image=b"x",
    ))
    assert login.decision == "allow"
    assert login.similarity is not None
    assert login.similarity >= 0.6


def test_login_rejects_wrong_passphrase(biometric_env, monkeypatch):
    rng = biometric_env
    _patch_pipeline(monkeypatch, embedding=_random_unit_embedding(rng))

    security.register_user(RegisterRequest(
        email="eve@p6.local", passphrase="hunter2",
        face_image=b"x", biometric_consent=True,
    ))
    login = security.login_user(LoginRequest(
        email="eve@p6.local", passphrase="WRONG", face_image=b"x",
    ))
    assert login.decision == "deny"
    assert "credencial" in login.reason.lower()


def test_login_rejects_different_face(biometric_env, monkeypatch):
    rng = biometric_env

    # 1. Registrar con cara A
    emb_a = _random_unit_embedding(rng)
    _patch_pipeline(monkeypatch, embedding=emb_a)
    security.register_user(RegisterRequest(
        email="frank@p6.local", passphrase="hunter2",
        face_image=b"x", biometric_consent=True,
    ))

    # 2. Login con cara B (otro embedding aleatorio, no debería parecerse)
    emb_b = _random_unit_embedding(rng)
    _patch_pipeline(monkeypatch, embedding=emb_b)
    login = security.login_user(LoginRequest(
        email="frank@p6.local", passphrase="hunter2", face_image=b"x",
    ))
    assert login.decision == "deny"
    assert "rostro" in login.reason.lower() or "sim=" in login.reason.lower()


def test_login_lockout_after_repeated_failures(biometric_env, monkeypatch):
    rng = biometric_env
    _patch_pipeline(monkeypatch, embedding=_random_unit_embedding(rng))
    security.register_user(RegisterRequest(
        email="grace@p6.local", passphrase="real-pwd",
        face_image=b"x", biometric_consent=True,
    ))

    # 3 intentos con passphrase mala (max_attempts=3 en el fixture)
    for _ in range(3):
        v = security.login_user(LoginRequest(
            email="grace@p6.local", passphrase="bad", face_image=b"x",
        ))
        assert v.decision == "deny"

    # El 4º intento (incluso con passphrase correcta) debe estar bloqueado
    v = security.login_user(LoginRequest(
        email="grace@p6.local", passphrase="real-pwd", face_image=b"x",
    ))
    assert v.decision == "deny"
    assert "bloquea" in v.reason.lower()


def test_login_unknown_email_returns_generic_deny(biometric_env, monkeypatch):
    """No filtrar si el email existe: el mensaje debe ser genérico."""
    rng = biometric_env
    _patch_pipeline(monkeypatch, embedding=_random_unit_embedding(rng))
    v = security.login_user(LoginRequest(
        email="nobody@p6.local", passphrase="x", face_image=b"x",
    ))
    assert v.decision == "deny"
    assert "credencial" in v.reason.lower()

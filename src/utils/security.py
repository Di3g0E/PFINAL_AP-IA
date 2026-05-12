"""
Utilidades de seguridad para la gestión de embeddings biométricos en reposo.

Origen: P5_AP-IA/src/utils/security.py — copiado a P6 por requisito de
auto-contención (no se importa de otros directorios de prácticas).

Principios:
  1. Los embeddings NO se almacenan en texto plano; se cifran con Fernet
     (AES-128-CBC + HMAC-SHA256) antes de escribirse a disco.
  2. La clave de cifrado se deriva de una passphrase usando PBKDF2-HMAC-SHA256
     (310.000 iteraciones, recomendación NIST SP 800-132 para 2024).
  3. Se añade un hash de integridad (SHA-256) al registro para detectar
     manipulación de la base de datos de vectores.
  4. El contador de intentos fallidos se mantiene en memoria; en producción
     debe persistirse en una caché distribuida (Redis con TTL).
"""

from __future__ import annotations

import base64
import hashlib
import os
import time
import uuid as _uuid
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from loguru import logger


# Derivación de clave

def derive_key(passphrase: str, salt: Optional[bytes] = None) -> tuple[bytes, bytes]:
    """
    Deriva una clave Fernet de 32 bytes desde una passphrase usando PBKDF2.

    Returns:
        (fernet_key_b64, salt) — guardar el salt junto a los datos cifrados.
    """
    if salt is None:
        salt = os.urandom(16)
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=310_000,   # NIST 2024 mínimo para PBKDF2-SHA256
    )
    key = base64.urlsafe_b64encode(kdf.derive(passphrase.encode()))
    return key, salt


# Almacén seguro de embeddings

@dataclass
class EncryptedEmbeddingStore:
    """
    Almacén cifrado de embeddings en Postgres (tabla `biometric_embeddings`).

    Reemplaza el backend de fichero local de P5/P6: en HF Spaces free no hay
    almacenamiento persistente, así que cualquier reinicio del Space borraba
    los embeddings. Ahora cada fila vive en Supabase con su propio salt y
    queda asociada al usuario por `user_id` (FK con cascade).

    Esquema de cifrado por usuario:
      - salt:           16 bytes aleatorios (PBKDF2-HMAC-SHA256, 310k iter).
      - ciphertext:     Fernet (AES-128-CBC + HMAC-SHA256) del embedding plano.
      - integrity_hash: SHA-256 del embedding plano; detecta tampering sin
                        descifrar todas las filas.

    El salt es por fila (no compartido) para que el compromiso de una clave
    derivada no facilite el ataque sobre las demás. El coste extra de derivar
    la Fernet en cada operación (~300 ms) es aceptable: las operaciones de
    biometría ocurren solo en login/register, no en hot paths.

    `db_path` se mantiene en la firma por compatibilidad con tests antiguos
    (que pasaban un Path al constructor); se ignora por completo.
    """

    passphrase: str
    db_path: Optional[Path] = field(default=None)   # ignorado, solo compat

    def _derive_fernet(self, salt: bytes) -> Fernet:
        key, _ = derive_key(self.passphrase, salt)
        return Fernet(key)

    def _coerce_uuid(self, user_id: str) -> Optional[_uuid.UUID]:
        try:
            return _uuid.UUID(user_id)
        except (ValueError, AttributeError, TypeError):
            logger.warning(f"user_id no convertible a UUID: {user_id!r}")
            return None

    def store(self, user_id: str, embedding: np.ndarray) -> None:
        """Cifra y persiste el embedding del usuario en Postgres."""
        # Imports locales para evitar ciclo schema → security al cargar.
        from src.data.database import get_session
        from src.data.schema import BiometricEmbedding

        user_uuid = self._coerce_uuid(user_id)
        if user_uuid is None:
            raise ValueError(f"user_id inválido: {user_id!r}")

        raw = embedding.astype(np.float32).tobytes()
        salt = os.urandom(16)
        fernet = self._derive_fernet(salt)
        ciphertext = fernet.encrypt(raw)
        integrity_hash = hashlib.sha256(raw).hexdigest()

        with get_session() as session:
            existing = session.get(BiometricEmbedding, user_uuid)
            if existing is None:
                session.add(BiometricEmbedding(
                    user_id=user_uuid,
                    salt=salt,
                    ciphertext=ciphertext,
                    integrity_hash=integrity_hash,
                ))
            else:
                existing.salt = salt
                existing.ciphertext = ciphertext
                existing.integrity_hash = integrity_hash
        logger.debug(f"Embedding almacenado para usuario '{user_id}'.")

    def retrieve(self, user_id: str) -> Optional[np.ndarray]:
        """Descifra y devuelve el embedding, verificando la integridad."""
        from src.data.database import get_session
        from src.data.schema import BiometricEmbedding

        user_uuid = self._coerce_uuid(user_id)
        if user_uuid is None:
            return None

        with get_session() as session:
            row = session.get(BiometricEmbedding, user_uuid)
            if row is None:
                return None
            salt = bytes(row.salt)
            ciphertext = bytes(row.ciphertext)
            integrity_hash = row.integrity_hash

        fernet = self._derive_fernet(salt)
        try:
            raw = fernet.decrypt(ciphertext)
        except InvalidToken:
            logger.error(
                f"Error de descifrado para '{user_id}'. "
                "¿Passphrase del servidor cambiada o datos corruptos?"
            )
            return None

        if hashlib.sha256(raw).hexdigest() != integrity_hash:
            logger.critical(
                f"¡Integridad comprometida para usuario '{user_id}'! "
                "El hash no coincide. Posible manipulación de la base de datos."
            )
            return None

        return np.frombuffer(raw, dtype=np.float32).copy()

    def delete(self, user_id: str) -> bool:
        from src.data.database import get_session
        from src.data.schema import BiometricEmbedding

        user_uuid = self._coerce_uuid(user_id)
        if user_uuid is None:
            return False

        with get_session() as session:
            row = session.get(BiometricEmbedding, user_uuid)
            if row is None:
                return False
            session.delete(row)
        logger.info(f"Embedding eliminado para usuario '{user_id}'.")
        return True

    def list_users(self) -> list[str]:
        from sqlalchemy import select
        from src.data.database import get_session
        from src.data.schema import BiometricEmbedding

        with get_session() as session:
            uuids = session.execute(select(BiometricEmbedding.user_id)).scalars().all()
            return [str(u) for u in uuids]

    def __contains__(self, user_id: str) -> bool:
        from src.data.database import get_session
        from src.data.schema import BiometricEmbedding

        user_uuid = self._coerce_uuid(user_id)
        if user_uuid is None:
            return False
        with get_session() as session:
            return session.get(BiometricEmbedding, user_uuid) is not None

    def save(self) -> None:
        """No-op: la persistencia es transaccional en cada `store`/`delete`."""
        return None


# Control de acceso por intentos fallidos

class AccessController:
    """
    Controla los intentos de autenticación fallidos y aplica bloqueo temporal.

    En producción sustituir el dict en memoria por Redis con TTL automático.
    """

    def __init__(self, max_attempts: int = 5, lockout_seconds: int = 300):
        self.max_attempts = max_attempts
        self.lockout_seconds = lockout_seconds
        # {user_id: {"count": int, "locked_until": float}}
        self._state: dict = defaultdict(lambda: {"count": 0, "locked_until": 0.0})

    def is_locked(self, user_id: str) -> bool:
        state = self._state[user_id]
        if state["locked_until"] > time.time():
            remaining = int(state["locked_until"] - time.time())
            logger.warning(
                f"Usuario '{user_id}' bloqueado. "
                f"Desbloqueo en {remaining} segundos."
            )
            return True
        return False

    def record_failure(self, user_id: str) -> None:
        state = self._state[user_id]
        state["count"] += 1
        logger.warning(
            f"Intento fallido para '{user_id}': "
            f"{state['count']}/{self.max_attempts}"
        )
        if state["count"] >= self.max_attempts:
            state["locked_until"] = time.time() + self.lockout_seconds
            state["count"] = 0
            logger.error(
                f"Usuario '{user_id}' bloqueado por {self.lockout_seconds} s "
                "por superar el límite de intentos."
            )

    def record_success(self, user_id: str) -> None:
        self._state[user_id] = {"count": 0, "locked_until": 0.0}

    def remaining_attempts(self, user_id: str) -> int:
        return max(0, self.max_attempts - self._state[user_id]["count"])


# Cifrado de credenciales por usuario (API keys de LLM)

def get_master_fernet() -> Fernet:
    """
    Devuelve la instancia Fernet derivada del MASTER_KEY del servidor (.env).

    Se usa para cifrar/descifrar las API keys de LLM que el usuario configura
    en su perfil. Distinto de la passphrase del usuario (que cifra los
    embeddings biométricos).
    """
    master_key = os.getenv("MASTER_FERNET_KEY")
    if not master_key:
        raise RuntimeError(
            "MASTER_FERNET_KEY no configurada en .env. "
            "Generar con: python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\""
        )
    return Fernet(master_key.encode())


def encrypt_api_key(plaintext: str) -> str:
    """Cifra una API key de LLM con la clave maestra del servidor."""
    return get_master_fernet().encrypt(plaintext.encode()).decode()


def decrypt_api_key(ciphertext: str) -> str:
    """Descifra una API key de LLM con la clave maestra del servidor."""
    return get_master_fernet().decrypt(ciphertext.encode()).decode()

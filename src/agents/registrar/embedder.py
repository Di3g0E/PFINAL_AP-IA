"""Embedder de descripciones de transacciones (evolución E1 de P2).

Encapsula `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
(384 dimensiones, multilingüe, CPU-friendly) como singleton lazy-load.

Se usa como extractor de features para:
  - `ZeroShotClassifier`: embebe descripciones de etiquetas + del input,
    decide por similitud coseno.
  - `PersonalClassifier`: usa el embedding como input del SGDClassifier
    incremental por usuario.

Diseño:
  - Lazy-load (la descarga ~120 MB sólo se hace en el primer `encode`).
  - Singleton: una instancia compartida en proceso.
  - Sustituible en tests por `set_for_tests(fake)` para evitar descargar
    el modelo real.
"""

from __future__ import annotations

import threading
from typing import Optional, Protocol

import numpy as np
from loguru import logger

_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
_EMBED_DIM = 384


class EmbedderBackend(Protocol):
    """Protocolo mínimo que debe cumplir cualquier backend de embeddings.

    `sentence_transformers.SentenceTransformer` lo cumple por construcción.
    Para tests basta con devolver un ndarray (n, dim).
    """

    def encode(self, texts: list[str], normalize_embeddings: bool = True) -> np.ndarray: ...


class TransformerEmbedder:
    """Singleton lazy-load del modelo de embeddings."""

    _instance: Optional["TransformerEmbedder"] = None
    _lock = threading.Lock()

    def __init__(self, backend: Optional[EmbedderBackend] = None):
        self._backend: Optional[EmbedderBackend] = backend
        self.dim = _EMBED_DIM

    @classmethod
    def shared(cls) -> "TransformerEmbedder":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def set_for_tests(cls, backend: EmbedderBackend) -> None:
        """Inyecta un backend determinista para tests (sin descargar nada)."""
        with cls._lock:
            cls._instance = cls(backend=backend)

    @classmethod
    def reset(cls) -> None:
        """Limpia la instancia singleton — útil entre tests."""
        with cls._lock:
            cls._instance = None

    def _ensure_backend(self) -> EmbedderBackend:
        if self._backend is not None:
            return self._backend
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise RuntimeError(
                "sentence-transformers no está instalado. Añádelo a "
                "requirements.txt o usa `pip install sentence-transformers`."
            ) from e
        logger.info(f"Cargando TransformerEmbedder ({_MODEL_NAME})...")
        self._backend = SentenceTransformer(_MODEL_NAME)
        logger.info("TransformerEmbedder listo.")
        return self._backend

    def encode(self, texts: list[str]) -> np.ndarray:
        """Devuelve embeddings normalizados L2 (shape (n, dim))."""
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        backend = self._ensure_backend()
        vecs = backend.encode(texts, normalize_embeddings=True)
        return np.asarray(vecs, dtype=np.float32)

    def encode_one(self, text: str) -> np.ndarray:
        """Conveniencia: embedding de un único texto (shape (dim,))."""
        return self.encode([text])[0]

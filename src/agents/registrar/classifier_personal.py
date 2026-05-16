"""Clasificador personal por usuario (SGDClassifier sobre embeddings).

Cada usuario tiene su propio modelo entrenado incrementalmente con sus
transacciones confirmadas. Se persiste como `models/personal/{uuid}.joblib`.

Diseño:
  - **Inputs**: embeddings de transformer (dim=384), no texto crudo. Esto
    permite que dos descripciones semánticamente similares mapeen a la
    misma área aunque el vocabulario sea distinto.
  - **Modelo**: SGDClassifier(loss='log_loss'). Soporta partial_fit; las
    probabilidades vienen de la softmax interna.
  - **Bootstrap**: hasta que el usuario alcanza N=20 confirmadas, no se
    crea aún su modelo personal (lo cubre el zero-shot). El primer
    entrenamiento se hace en *batch* con todas sus N confirmadas; las
    posteriores son `partial_fit` con una sola muestra.
  - **Persistencia**: tras cada partial_fit, se guarda el joblib. Cero
    dependencias en BD (los embeddings ya son features auto-contenidas).
  - **Memoria**: LRU de hasta 64 usuarios cargados en RAM.
"""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import joblib
import numpy as np
from loguru import logger
from sklearn.linear_model import SGDClassifier

from src.agents.registrar.embedder import TransformerEmbedder

PERSONAL_DIR = Path(__file__).resolve().parents[3] / "models" / "personal"
COLD_START_THRESHOLD = 20
LRU_MAX = 64

SGD_PARAMS = {
    "loss": "log_loss",
    "penalty": "l2",
    "alpha": 1e-4,
    "random_state": 42,
    "tol": None,
    "learning_rate": "optimal",
}


def _safe_user_key(user_id: str) -> str:
    """Valida y normaliza el user_id para usarlo como nombre de fichero.

    Acepta cualquier UUID parseable. Si no lo es, hashea para evitar
    inyección en el path.
    """
    try:
        return str(uuid.UUID(user_id))
    except (ValueError, TypeError):
        import hashlib
        return hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:32]


class PersonalClassifier:
    """Encapsula el SGDClassifier de UN usuario + metadatos."""

    def __init__(self, user_id: str, classes: list[str]):
        self.user_id = user_id
        self.classes_ = list(classes)
        self.clf: Optional[SGDClassifier] = None
        self.n_samples = 0
        self.created_at: Optional[datetime] = None
        self.updated_at: Optional[datetime] = None

    # Entrenamiento

    def bootstrap(self, X: np.ndarray, y: list[str], n_epochs: int = 10) -> None:
        """Primer entrenamiento batch sobre N muestras.

        Usa `partial_fit` repetido en lugar de `fit` porque `fit` exige
        que `y` contenga ≥2 clases, lo cual no se garantiza en
        cold-start (un usuario nuevo puede registrar 20 transacciones
        todas de `Food`). `partial_fit(classes=...)` sí acepta y con
        una sola clase porque ya conoce el universo completo.
        """
        if X.shape[0] < 1:
            raise ValueError("Bootstrap necesita al menos 1 muestra.")
        clf = SGDClassifier(**SGD_PARAMS)
        # Varias pasadas con shuffle para mejorar convergencia (equivalente
        # a `fit` cuando hay ≥2 clases, pero también funciona con 1).
        rng = np.random.default_rng(SGD_PARAMS["random_state"])
        for _ in range(n_epochs):
            perm = rng.permutation(X.shape[0])
            clf.partial_fit(X[perm], [y[i] for i in perm], classes=self.classes_)
        self.clf = clf
        self.n_samples = X.shape[0]
        now = datetime.now(timezone.utc)
        self.created_at = now
        self.updated_at = now

    def partial_fit_one(self, x: np.ndarray, label: str) -> None:
        """Actualiza incrementalmente con UNA nueva muestra."""
        if self.clf is None:
            raise RuntimeError("Llama a bootstrap() antes de partial_fit_one().")
        x = x.reshape(1, -1)
        self.clf.partial_fit(x, [label], classes=self.classes_)
        self.n_samples += 1
        self.updated_at = datetime.now(timezone.utc)

    # Predicción

    def predict_with_confidence(self, x: np.ndarray) -> tuple[str, float]:
        if self.clf is None:
            raise RuntimeError("Modelo personal sin entrenar.")
        x = x.reshape(1, -1)
        probs = self.clf.predict_proba(x)[0]
        idx = int(np.argmax(probs))
        return self.classes_[idx], float(probs[idx])

    # Persistencia

    def path(self) -> Path:
        return PERSONAL_DIR / f"{_safe_user_key(self.user_id)}.joblib"

    def save(self) -> None:
        if self.clf is None:
            return
        PERSONAL_DIR.mkdir(parents=True, exist_ok=True)
        joblib.dump({
            "user_id": self.user_id,
            "classes_": self.classes_,
            "clf": self.clf,
            "n_samples": self.n_samples,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }, self.path())

    @classmethod
    def load(cls, user_id: str) -> Optional["PersonalClassifier"]:
        path = PERSONAL_DIR / f"{_safe_user_key(user_id)}.joblib"
        if not path.is_file():
            return None
        try:
            data = joblib.load(path)
        except Exception as e:
            logger.warning(f"PersonalClassifier.load({user_id}) falló: {e}")
            return None
        instance = cls(user_id=user_id, classes=list(data["classes_"]))
        instance.clf = data["clf"]
        instance.n_samples = int(data.get("n_samples", 0))
        instance.created_at = data.get("created_at")
        instance.updated_at = data.get("updated_at")
        return instance


class PersonalClassifierRegistry:
    """Singleton: cachea PersonalClassifiers en memoria con LRU.

    El registry encapsula la coordinación entre el embedder y los modelos
    por usuario. Métodos públicos:
      - get(user_id) → PersonalClassifier | None
      - record_sample(user_id, description, label) → entrena o
        actualiza el modelo del usuario
      - predict(user_id, description) → (label, confidence) | None si
        el usuario aún no tiene modelo (cold-start)
    """

    _instance: Optional["PersonalClassifierRegistry"] = None
    _lock = threading.Lock()

    def __init__(self, classes: list[str], embedder: Optional[TransformerEmbedder] = None,
                 cold_start_threshold: int = COLD_START_THRESHOLD):
        self.classes_ = list(classes)
        self.embedder = embedder or TransformerEmbedder.shared()
        self.cold_start_threshold = cold_start_threshold
        # LRU cache de modelos cargados
        self._cache: "OrderedDict[str, PersonalClassifier]" = OrderedDict()
        # Buffer de muestras de bootstrap por usuario (texts + labels), hasta
        # acumular `cold_start_threshold` muestras → primer entrenamiento.
        self._bootstrap_buffer: dict[str, list[tuple[str, str]]] = {}
        self._buf_lock = threading.Lock()

    @classmethod
    def shared(cls, classes: list[str]) -> "PersonalClassifierRegistry":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(classes=classes)
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Limpia la instancia singleton — útil entre tests."""
        with cls._lock:
            cls._instance = None

    # API pública

    def get(self, user_id: str) -> Optional[PersonalClassifier]:
        """Devuelve el clasificador personal del usuario, o None si no existe."""
        if user_id in self._cache:
            self._cache.move_to_end(user_id)
            return self._cache[user_id]
        loaded = PersonalClassifier.load(user_id)
        if loaded is not None:
            self._put_in_cache(user_id, loaded)
            return loaded
        return None

    def predict(self, user_id: str, description: str) -> Optional[tuple[str, float]]:
        """Predice con el modelo personal. None si el usuario aún no tiene modelo."""
        pc = self.get(user_id)
        if pc is None or pc.clf is None:
            return None
        emb = self.embedder.encode_one(description)
        return pc.predict_with_confidence(emb)

    def record_sample(self, user_id: str, description: str, label: str) -> str:
        """Registra una muestra confirmada del usuario para entrenamiento.

        Devuelve el estado tras procesar la muestra:
          - 'buffering': aún no hay modelo; muestra acumulada hasta umbral.
          - 'bootstrapped': se alcanzó el umbral; modelo recién creado.
          - 'updated': partial_fit aplicado sobre modelo existente.
          - 'ignored': label fuera del universo conocido.
        """
        if label not in self.classes_:
            logger.warning(
                f"record_sample({user_id}): label '{label}' fuera del universo "
                f"conocido {self.classes_}; muestra ignorada.")
            return "ignored"

        pc = self.get(user_id)
        if pc is not None and pc.clf is not None:
            emb = self.embedder.encode_one(description)
            pc.partial_fit_one(emb, label)
            pc.save()
            return "updated"

        # Cold-start: bufferizar la muestra
        with self._buf_lock:
            buf = self._bootstrap_buffer.setdefault(user_id, [])
            buf.append((description, label))
            if len(buf) >= self.cold_start_threshold:
                texts = [t for t, _ in buf]
                labels = [l for _, l in buf]
                embeddings = self.embedder.encode(texts)
                new_pc = PersonalClassifier(user_id=user_id, classes=self.classes_)
                new_pc.bootstrap(embeddings, labels)
                new_pc.save()
                self._put_in_cache(user_id, new_pc)
                self._bootstrap_buffer.pop(user_id, None)
                return "bootstrapped"
        return "buffering"

    def history_size(self, user_id: str) -> int:
        """Cuántas muestras ha visto este usuario (buffer + modelo entrenado)."""
        pc = self.get(user_id)
        n = pc.n_samples if pc is not None else 0
        with self._buf_lock:
            n += len(self._bootstrap_buffer.get(user_id, []))
        return n

    # Internos

    def _put_in_cache(self, user_id: str, pc: PersonalClassifier) -> None:
        self._cache[user_id] = pc
        self._cache.move_to_end(user_id)
        while len(self._cache) > LRU_MAX:
            self._cache.popitem(last=False)

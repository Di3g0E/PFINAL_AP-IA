"""
Pipeline biométrico facial (origen: P5_AP-IA/src/features/preprocessor.py +
P5_AP-IA/src/models/face_embedder.py + liveness_detector.py).

Adaptaciones para P6:
  - **Solo FaceNet** (facenet-pytorch). Eliminamos DeepFace/ArcFace para no
    arrastrar TensorFlow al stack.
  - **Liveness DenseNet201 con pesos ImageNet** (sin fine-tuning específico
    de antispoofing). Documentamos: en producción real haría falta entrenar
    una cabeza binaria con un dataset de ataques de presentación; aquí
    implementamos la API y el threshold operativo, suficiente para ejercitar
    el flujo end-to-end.
  - **Lazy loading**: nada se descarga ni se carga en memoria hasta el primer
    `embed()` o `is_live()`. Importante porque MTCNN + InceptionResnetV1 +
    DenseNet201 pesan ~150 MB y tardan en cold-start.
  - **Singleton**: una sola instancia compartida entre llamadas. Los modelos
    no se recargan por cada login.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np
from loguru import logger


@dataclass
class FaceFeatures:
    """Resultado del pipeline biométrico para una imagen."""
    embedding: np.ndarray       # float32, shape (512,), L2-normalizado
    liveness_score: float       # ∈ [0, 1] — probabilidad de ser rostro real
    is_live: bool               # liveness_score >= LIVENESS_THRESHOLD
    face_confidence: float      # confianza del detector MTCNN


# Constantes operativas — defaults sensatos. En runtime se leen de
# `settings.liveness_threshold` y `settings.face_similarity_threshold`
# (vía `.env`), así puedes ajustarlos sin tocar el código. Estas constantes
# se mantienen como fallback explícito por si `settings` no está disponible
# (p. ej. tests que importan biometrics aislado).
LIVENESS_THRESHOLD = 0.50
SIMILARITY_THRESHOLD = 0.60


# Coordenadas de referencia ArcFace 224x224 (mismas que P5)
_REFERENCE_LANDMARKS_224 = np.array([
    [73.55,  90.68],   # ojo izquierdo
    [150.45, 90.68],   # ojo derecho
    [112.0,  130.0],   # nariz
    [83.5,   162.0],   # comisura boca izquierda
    [140.5,  162.0],   # comisura boca derecha
], dtype=np.float32)


class BiometricPipeline:
    """Pipeline lazy: detector → embedder → liveness, todo en una llamada."""

    _instance: Optional["BiometricPipeline"] = None

    def __init__(self):
        # Todo se difiere hasta el primer uso
        self._mtcnn = None
        self._embedder = None
        self._liveness_model = None
        self._liveness_transform = None
        self._device = None

    @classmethod
    def shared(cls) -> "BiometricPipeline":
        """Singleton para evitar recargar modelos en cada llamada."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # Inicialización lazy de cada componente

    def _ensure_device(self):
        if self._device is None:
            import torch
            self._device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            logger.info(f"BiometricPipeline: device={self._device}")

    def _ensure_mtcnn(self):
        if self._mtcnn is None:
            self._ensure_device()
            from facenet_pytorch import MTCNN
            logger.info("Cargando MTCNN para detección facial (cold start)...")
            self._mtcnn = MTCNN(
                image_size=224,
                min_face_size=40,
                thresholds=[0.6, 0.7, 0.7],
                factor=0.709,
                keep_all=False,
                device=self._device,
            )

    def _ensure_embedder(self):
        if self._embedder is None:
            self._ensure_device()
            from facenet_pytorch import InceptionResnetV1
            logger.info("Cargando FaceNet (InceptionResnetV1, VGGFace2) — ~100 MB...")
            self._embedder = InceptionResnetV1(pretrained="vggface2").eval().to(self._device)

    def _ensure_liveness(self):
        if self._liveness_model is None:
            self._ensure_device()
            import torch
            import torch.nn as nn
            import torchvision.transforms as T
            from torchvision.models import DenseNet201_Weights, densenet201

            logger.info("Cargando DenseNet201 para liveness (ImageNet pretrain) — ~80 MB...")
            model = densenet201(weights=DenseNet201_Weights.IMAGENET1K_V1)
            in_features = model.classifier.in_features
            model.classifier = nn.Sequential(
                nn.Linear(in_features, 512),
                nn.ReLU(inplace=True),
                nn.Dropout(p=0.4),
                nn.Linear(512, 2),       # 0=spoof, 1=live
            )

            # Si el usuario ha colocado un checkpoint fine-tuneado en
            # `settings.liveness_model_path`, lo cargamos. Sin esto, el
            # modelo solo usa pesos ImageNet (modo desarrollo).
            try:
                from src.utils.config import settings
                model_path = settings.project_root / settings.liveness_model_path
            except Exception:
                from pathlib import Path
                model_path = Path("models/liveness_kaggle.pth")

            if model_path.exists():
                try:
                    state = torch.load(str(model_path), map_location=self._device,
                                       weights_only=False)
                    state_dict = state.get("state_dict", state) if isinstance(state, dict) else state
                    model.load_state_dict(state_dict)
                    logger.info(f"Pesos liveness fine-tuneados cargados desde: {model_path}")
                except Exception as e:
                    logger.error(f"No se pudo cargar {model_path}: {e}. "
                                 "Se usarán pesos ImageNet sin fine-tuning.")
            else:
                logger.warning(
                    f"Modelo de liveness no encontrado en '{model_path}'. "
                    "Se usarán pesos ImageNet sin fine-tuning. "
                    "Las puntuaciones serán ruidosas; baja LIVENESS_THRESHOLD."
                )

            self._liveness_model = model.eval().to(self._device)
            self._liveness_transform = T.Compose([
                T.ToPILImage(),
                T.Resize((224, 224)),
                T.ToTensor(),
                T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
            ])

    # Operaciones de bajo nivel

    def _detect_and_align(self, image_bgr: np.ndarray) -> Optional[tuple[np.ndarray, float]]:
        """MTCNN → bbox + landmarks → recorte alineado 224x224. None si no hay rostro."""
        self._ensure_mtcnn()
        image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        boxes, probs, landmarks = self._mtcnn.detect(image_rgb, landmarks=True)
        if boxes is None or probs is None or probs[0] is None:
            return None
        if probs[0] < 0.7:
            return None

        lm = landmarks[0].astype(np.float32)
        # Alineación afín parcial sobre los 5 landmarks
        M, _ = cv2.estimateAffinePartial2D(lm, _REFERENCE_LANDMARKS_224, method=cv2.LMEDS)
        if M is None:
            aligned_rgb = cv2.resize(image_rgb, (224, 224))
        else:
            aligned_rgb = cv2.warpAffine(
                image_rgb, M, (224, 224),
                flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT,
            )
        aligned_bgr = cv2.cvtColor(aligned_rgb, cv2.COLOR_RGB2BGR)
        return aligned_bgr, float(probs[0])

    def _embed(self, aligned_bgr: np.ndarray) -> np.ndarray:
        """FaceNet → embedding 512-D L2-normalizado."""
        self._ensure_embedder()
        import torch
        face_rgb = cv2.cvtColor(aligned_bgr, cv2.COLOR_BGR2RGB)
        face_160 = cv2.resize(face_rgb, (160, 160)).astype(np.float32)
        face_160 = (face_160 - 127.5) / 128.0   # [-1, 1] estándar FaceNet
        tensor = torch.from_numpy(face_160.transpose(2, 0, 1)).unsqueeze(0).to(self._device)
        with torch.no_grad():
            emb = self._embedder(tensor).squeeze().cpu().numpy().astype(np.float32)
        norm = np.linalg.norm(emb)
        if norm > 0:
            emb /= norm
        return emb

    def _liveness(self, aligned_bgr: np.ndarray) -> float:
        """DenseNet201 → probabilidad ∈ [0,1] de que el rostro sea real (no spoof)."""
        self._ensure_liveness()
        import torch
        tensor = self._liveness_transform(aligned_bgr).unsqueeze(0).to(self._device)
        with torch.no_grad():
            logits = self._liveness_model(tensor)
            probs = torch.softmax(logits, dim=1)[0]
        return float(probs[1].cpu())

    # API pública

    def extract(self, image_bgr: np.ndarray) -> FaceFeatures:
        """
        Pipeline completo: detección + alineación + embedding + liveness.

        Raises:
            ValueError si no se detecta un rostro válido.
        """
        det = self._detect_and_align(image_bgr)
        if det is None:
            raise ValueError("No se detectó ningún rostro en la imagen.")
        aligned_bgr, confidence = det

        liveness_score = self._liveness(aligned_bgr)
        embedding = self._embed(aligned_bgr)

        # El umbral se lee de settings en cada llamada para que cambios en
        # .env (vía LIVENESS_THRESHOLD=...) tomen efecto sin reiniciar el
        # singleton. Fallback a la constante si settings no está disponible.
        try:
            from src.utils.config import settings
            threshold = settings.liveness_threshold
        except Exception:
            threshold = LIVENESS_THRESHOLD

        return FaceFeatures(
            embedding=embedding,
            liveness_score=liveness_score,
            is_live=liveness_score >= threshold,
            face_confidence=confidence,
        )

    @staticmethod
    def cosine_similarity(emb_a: np.ndarray, emb_b: np.ndarray) -> float:
        """Producto punto entre embeddings L2-normalizados (∈ [-1, 1])."""
        return float(np.dot(emb_a, emb_b))


def decode_image_bytes(image_bytes: bytes) -> np.ndarray:
    """JPEG/PNG bytes → array BGR OpenCV. Lanza ValueError si no decodifica."""
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("La imagen no se pudo decodificar (bytes inválidos).")
    return img

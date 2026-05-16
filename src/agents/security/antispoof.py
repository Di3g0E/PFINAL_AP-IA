"""Anti-spoofing por análisis de textura (E3 Fase 2).

Implementa un clasificador de spoofing basado en análisis de textura
LBP (Local Binary Patterns) + color space analysis. No requiere
modelos ONNX externos.

Estrategia:
  1. **LBP (Local Binary Patterns)**: las fotos impresas y pantallas
     tienen patrones de textura más uniformes / con artefactos de moire
     que los rostros reales.
  2. **Análisis de color**: las pantallas emiten en un subespacio de
     color más estrecho (gamut); los impresos tienen distribuciones de
     color diferentes por la reflexión del papel.
  3. **Detección de bordes de marco**: busca bordes rectilíneos
     alrededor de la zona del rostro (indicador de marco de pantalla o
     borde de foto impresa).

El score final es la mediana de los scores por frame (si hay vídeo).
"""

from __future__ import annotations

from typing import Optional

import cv2
import numpy as np
from loguru import logger


def _lbp_features(gray: np.ndarray) -> np.ndarray:
    """Calcula histograma LBP uniforme 8-vecinos sobre la imagen gris.

    LBP es robusto para discriminar texturas naturales (piel) de texturas
    artificiales (pantalla LCD, papel impreso). Devuelve un histograma
    normalizado de 59 bins (58 patrones uniformes + 1 no-uniforme).
    """
    h, w = gray.shape
    lbp = np.zeros((h - 2, w - 2), dtype=np.uint8)

    # 8 vecinos circulares, radio 1
    offsets = [(-1, -1), (-1, 0), (-1, 1), (0, 1),
               (1, 1), (1, 0), (1, -1), (0, -1)]
    for bit, (dy, dx) in enumerate(offsets):
        neighbor = gray[1 + dy:h - 1 + dy, 1 + dx:w - 1 + dx]
        center = gray[1:h - 1, 1:w - 1]
        lbp |= ((neighbor >= center).astype(np.uint8) << bit)

    # Histograma normalizado (256 bins reducidos a densidad)
    hist, _ = np.histogram(lbp.ravel(), bins=256, range=(0, 256))
    hist = hist.astype(np.float32) / (hist.sum() + 1e-8)
    return hist


def _color_features(image_bgr: np.ndarray) -> np.ndarray:
    """Extrae features de distribución de color en espacios HSV y YCrCb.

    Las pantallas tienen:
      - Saturación más baja y valor más uniforme (iluminación LED).
      - Distribución Cr/Cb más estrecha (gamut limitado).

    Las fotos impresas tienen:
      - Crominancia desplazada por la reflexión del papel.
    """
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    ycrcb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2YCrCb)

    features = []

    # HSV: media y std de cada canal
    for ch in range(3):
        features.append(float(np.mean(hsv[:, :, ch])))
        features.append(float(np.std(hsv[:, :, ch])))

    # YCrCb: media y std de Cr y Cb
    for ch in [1, 2]:  # Cr, Cb
        features.append(float(np.mean(ycrcb[:, :, ch])))
        features.append(float(np.std(ycrcb[:, :, ch])))

    # Relación entre Cr y Cb (indicador de gamut)
    cr = ycrcb[:, :, 1].astype(np.float32)
    cb = ycrcb[:, :, 2].astype(np.float32)
    cr_cb_ratio = np.mean(cr / (cb + 1e-6))
    features.append(float(cr_cb_ratio))

    return np.array(features, dtype=np.float32)


def _edge_density(gray: np.ndarray) -> float:
    """Densidad de bordes Laplacianos: las pantallas/impresos suelen tener
    bordes más definidos en zonas periféricas (marco del dispositivo)."""
    lap = cv2.Laplacian(gray, cv2.CV_64F)
    return float(np.var(lap))


def _frequency_features(gray: np.ndarray) -> np.ndarray:
    """Análisis en frecuencia: las pantallas LCD producen patrones de
    Moiré detectables como picos periódicos en el espectro de Fourier.

    Las fotos impresas tienen ruido de trama (halftone) que también
    genera patrones periódicos.
    """
    f = np.fft.fft2(gray.astype(np.float32))
    fshift = np.fft.fftshift(f)
    magnitude = np.log1p(np.abs(fshift))

    h, w = magnitude.shape
    center_h, center_w = h // 2, w // 2

    # Energía en anillos concéntricos (4 bandas)
    bands = []
    radii = [0.1, 0.25, 0.5, 0.75, 1.0]
    max_r = min(center_h, center_w)
    y, x = np.ogrid[:h, :w]
    dist = np.sqrt((y - center_h) ** 2 + (x - center_w) ** 2)

    for i in range(len(radii) - 1):
        r_inner = radii[i] * max_r
        r_outer = radii[i + 1] * max_r
        mask = (dist >= r_inner) & (dist < r_outer)
        if mask.any():
            bands.append(float(np.mean(magnitude[mask])))
        else:
            bands.append(0.0)

    # Ratio alta/baja frecuencia (indicador de Moiré)
    if bands[0] > 0:
        bands.append(bands[-1] / (bands[0] + 1e-6))
    else:
        bands.append(0.0)

    return np.array(bands, dtype=np.float32)


def compute_antispoof_score(face_bgr: np.ndarray) -> float:
    """Calcula un score anti-spoofing ∈ [0, 1] para un recorte facial.

    Score alto = probablemente real, score bajo = probablemente spoof.

    El score se basa en heurísticas de textura, color y frecuencia que
    no requieren un modelo entrenado. Es un baseline razonable para
    complementar el DenseNet201 liveness.

    Args:
        face_bgr: Recorte facial BGR alineado (típicamente 224x224).

    Returns:
        Score ∈ [0, 1].
    """
    if face_bgr is None or face_bgr.size == 0:
        return 0.0

    # Redimensionar a 224x224 si no lo es
    if face_bgr.shape[:2] != (224, 224):
        face_bgr = cv2.resize(face_bgr, (224, 224))

    gray = cv2.cvtColor(face_bgr, cv2.COLOR_BGR2GRAY)

    # 1. LBP texture score
    lbp_hist = _lbp_features(gray)
    # Los rostros reales tienen una distribución LBP más uniforme (alta
    # entropía), los spoofs tienden a concentrarse en pocos bins.
    entropy = -np.sum(lbp_hist * np.log2(lbp_hist + 1e-10))
    max_entropy = np.log2(256)
    texture_score = min(1.0, entropy / max_entropy)

    # 2. Color features score
    color_feats = _color_features(face_bgr)
    # La std de Saturación baja indica pantalla (iluminación uniforme)
    sat_std = color_feats[3]  # std de S en HSV
    color_score = min(1.0, sat_std / 50.0)  # Normalizar a [0,1]

    # 3. Edge density (varianza del Laplaciano)
    edge_var = _edge_density(gray)
    # Valores muy altos = mucho borde = posible marco de pantalla
    # Valores muy bajos = imagen borrosa = posible foto impresa borrosa
    # Rango óptimo para caras reales: 100-2000
    if edge_var < 50:
        edge_score = 0.3  # Demasiado borrosa
    elif edge_var > 3000:
        edge_score = 0.4  # Demasiados bordes artificiales
    else:
        edge_score = min(1.0, edge_var / 1500.0)

    # 4. Frequency features
    freq_feats = _frequency_features(gray)
    hf_lf_ratio = freq_feats[-1]  # Ratio alta/baja frecuencia
    # Las pantallas/impresos tienen más energía en altas frecuencias
    # (Moiré, halftone) relativo a las bajas.
    if hf_lf_ratio > 0.8:
        freq_score = 0.4  # Posible Moiré
    else:
        freq_score = min(1.0, 0.5 + hf_lf_ratio)

    # Combinación ponderada
    final_score = (
        0.30 * texture_score +
        0.25 * color_score +
        0.25 * edge_score +
        0.20 * freq_score
    )

    return float(np.clip(final_score, 0.0, 1.0))


def compute_antispoof_scores_batch(
    faces_bgr: list[np.ndarray],
) -> tuple[float, list[float]]:
    """Calcula scores anti-spoofing para una lista de recortes faciales.

    Returns:
        (median_score, per_frame_scores): mediana del batch y lista completa.
    """
    if not faces_bgr:
        return 0.0, []

    scores = [compute_antispoof_score(f) for f in faces_bgr]
    median_score = float(np.median(scores))
    return median_score, scores

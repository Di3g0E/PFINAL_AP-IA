"""Tests del módulo anti-spoofing (E3 Fase 2).

Valida:
  - compute_antispoof_score: devuelve score ∈ [0, 1]
  - compute_antispoof_scores_batch: mediana correcta
  - Features internas: LBP, color, edge, frequency
  - Robustez: imágenes vacías, tamaños variados
  - Diferenciación: face real vs imagen uniforme (proxy de spoof)
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest


class TestLBPFeatures:
    def test_returns_histogram(self):
        from src.agents.security.antispoof import _lbp_features

        gray = np.random.randint(0, 256, (100, 100), dtype=np.uint8)
        hist = _lbp_features(gray)
        assert hist.shape == (256,)
        assert hist.sum() == pytest.approx(1.0, abs=0.01)

    def test_uniform_image_has_concentrated_bins(self):
        from src.agents.security.antispoof import _lbp_features

        # Imagen uniforme → LBP = 0 o 255 en todos los píxeles
        gray = np.full((100, 100), 128, dtype=np.uint8)
        hist = _lbp_features(gray)
        # La mayoría de la masa debe estar en 1-2 bins
        assert hist.max() > 0.5


class TestColorFeatures:
    def test_returns_11_features(self):
        from src.agents.security.antispoof import _color_features

        img = np.random.randint(0, 256, (100, 100, 3), dtype=np.uint8)
        feats = _color_features(img)
        # 6 (HSV mean+std x3) + 4 (Cr/Cb mean+std x2) + 1 (ratio) = 11
        assert feats.shape == (11,)
        assert feats.dtype == np.float32


class TestEdgeDensity:
    def test_uniform_image_low_density(self):
        from src.agents.security.antispoof import _edge_density

        gray = np.full((100, 100), 128, dtype=np.uint8)
        density = _edge_density(gray)
        assert density < 1.0  # Casi sin bordes

    def test_noisy_image_higher_density(self):
        from src.agents.security.antispoof import _edge_density

        gray = np.random.randint(0, 256, (100, 100), dtype=np.uint8)
        density = _edge_density(gray)
        assert density > 100  # Mucho ruido = muchos bordes


class TestFrequencyFeatures:
    def test_returns_5_features(self):
        from src.agents.security.antispoof import _frequency_features

        gray = np.random.randint(0, 256, (100, 100), dtype=np.uint8)
        feats = _frequency_features(gray)
        assert feats.shape == (5,)  # 4 bandas + 1 ratio

    def test_uniform_has_low_high_ratio(self):
        from src.agents.security.antispoof import _frequency_features

        gray = np.full((100, 100), 128, dtype=np.uint8)
        feats = _frequency_features(gray)
        # Imagen uniforme → toda la energía en DC (baja frecuencia)
        assert feats[-1] < 0.5  # Ratio alta/baja < 0.5


class TestComputeAntispoofScore:
    def test_returns_float_in_range(self):
        from src.agents.security.antispoof import compute_antispoof_score

        face = np.random.randint(0, 256, (224, 224, 3), dtype=np.uint8)
        score = compute_antispoof_score(face)
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0

    def test_handles_different_sizes(self):
        from src.agents.security.antispoof import compute_antispoof_score

        for size in [(100, 100, 3), (224, 224, 3), (640, 480, 3)]:
            face = np.random.randint(0, 256, size, dtype=np.uint8)
            score = compute_antispoof_score(face)
            assert 0.0 <= score <= 1.0

    def test_empty_image_returns_zero(self):
        from src.agents.security.antispoof import compute_antispoof_score

        score = compute_antispoof_score(np.array([]))
        assert score == 0.0

    def test_none_returns_zero(self):
        from src.agents.security.antispoof import compute_antispoof_score

        score = compute_antispoof_score(None)
        assert score == 0.0

    def test_real_face_proxy_vs_uniform(self):
        """Un face con textura variada debe puntuar más alto que uno uniforme."""
        from src.agents.security.antispoof import compute_antispoof_score

        # "Face" con textura natural (ruido gaussiano = proxy de skin texture)
        np.random.seed(42)
        real_proxy = np.random.randint(80, 200, (224, 224, 3), dtype=np.uint8)
        # Aplicar blur suave para simular piel
        real_proxy = cv2.GaussianBlur(real_proxy, (5, 5), 0)

        # "Face" uniforme (proxy de pantalla uniforme)
        uniform = np.full((224, 224, 3), 140, dtype=np.uint8)

        score_real = compute_antispoof_score(real_proxy)
        score_uniform = compute_antispoof_score(uniform)

        # El face con textura debe tener score mayor
        assert score_real > score_uniform


class TestComputeAntispoofBatch:
    def test_batch_median(self):
        from src.agents.security.antispoof import compute_antispoof_scores_batch

        faces = [
            np.random.randint(0, 256, (224, 224, 3), dtype=np.uint8)
            for _ in range(5)
        ]
        median, per_frame = compute_antispoof_scores_batch(faces)
        assert isinstance(median, float)
        assert len(per_frame) == 5
        assert median == pytest.approx(np.median(per_frame), abs=0.001)

    def test_empty_batch(self):
        from src.agents.security.antispoof import compute_antispoof_scores_batch

        median, per_frame = compute_antispoof_scores_batch([])
        assert median == 0.0
        assert per_frame == []

    def test_single_face(self):
        from src.agents.security.antispoof import compute_antispoof_scores_batch

        face = np.random.randint(0, 256, (224, 224, 3), dtype=np.uint8)
        median, per_frame = compute_antispoof_scores_batch([face])
        assert len(per_frame) == 1
        assert median == per_frame[0]


class TestAntispoofIntegration:
    def test_face_features_has_antispoof_field(self):
        from src.agents.security.biometrics import FaceFeatures
        ff = FaceFeatures(
            embedding=np.zeros(512, dtype=np.float32),
            liveness_score=0.8,
            is_live=True,
            face_confidence=0.99,
        )
        # Default antispoof_score = 1.0 (no bloquear)
        assert ff.antispoof_score == 1.0

    def test_face_features_custom_antispoof(self):
        from src.agents.security.biometrics import FaceFeatures
        ff = FaceFeatures(
            embedding=np.zeros(512, dtype=np.float32),
            liveness_score=0.8,
            is_live=True,
            face_confidence=0.99,
            antispoof_score=0.3,
        )
        assert ff.antispoof_score == 0.3

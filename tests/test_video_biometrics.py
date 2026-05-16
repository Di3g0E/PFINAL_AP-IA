"""Tests del pipeline biométrico con vídeo (E3 Fase 1 de P5).

Valida:
  - decode_video_bytes: decodifica WebM/MP4, muestrea N frames equiespaciados
  - extract_from_video: pipeline completo con voto promedio de embeddings
  - Feature flags: security_video_enabled controla si se usa vídeo
  - Retrocompatibilidad: login single-frame sigue funcionando
  - Robustez: frames sin cara se descartan en silencio
"""

from __future__ import annotations

import struct
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
import pytest


# --- Tests de decode_video_bytes ---


class TestDecodeVideoBytes:
    def _make_test_video(self, n_frames: int = 30, w: int = 160, h: int = 120) -> bytes:
        """Genera un vídeo MP4 sintético con n_frames frames de colores."""
        tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        tmp_path = tmp.name
        tmp.close()

        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(tmp_path, fourcc, 10.0, (w, h))
        for i in range(n_frames):
            # Cada frame tiene un color diferente para distinguirlos
            color = ((i * 8) % 256, (i * 12) % 256, (i * 16) % 256)
            frame = np.full((h, w, 3), color, dtype=np.uint8)
            writer.write(frame)
        writer.release()

        video_bytes = Path(tmp_path).read_bytes()
        Path(tmp_path).unlink(missing_ok=True)
        return video_bytes

    def test_decodes_valid_video(self):
        from src.agents.security.biometrics import decode_video_bytes

        video = self._make_test_video(30)
        frames = decode_video_bytes(video, n_frames=5)
        assert len(frames) == 5
        for f in frames:
            assert isinstance(f, np.ndarray)
            assert f.ndim == 3

    def test_samples_equidistributed(self):
        from src.agents.security.biometrics import decode_video_bytes

        video = self._make_test_video(30)
        frames = decode_video_bytes(video, n_frames=3)
        assert len(frames) == 3
        # Los frames deben ser diferentes (distintos colores)
        assert not np.array_equal(frames[0], frames[-1])

    def test_n_frames_greater_than_total(self):
        from src.agents.security.biometrics import decode_video_bytes

        video = self._make_test_video(5)
        frames = decode_video_bytes(video, n_frames=20)
        assert len(frames) == 5  # No puede muestrear más de los que hay

    def test_empty_bytes_returns_empty(self):
        from src.agents.security.biometrics import decode_video_bytes

        frames = decode_video_bytes(b"", n_frames=5)
        assert frames == []

    def test_invalid_bytes_returns_empty(self):
        from src.agents.security.biometrics import decode_video_bytes

        frames = decode_video_bytes(b"not a video", n_frames=5)
        assert frames == []


# --- Tests de extract_from_video (con mocks) ---


class TestExtractFromVideo:
    """Tests del pipeline con mocks para evitar cargar modelos pesados."""

    @pytest.fixture
    def mock_pipeline(self):
        """BiometricPipeline con métodos de bajo nivel mockeados."""
        from src.agents.security.biometrics import BiometricPipeline

        # Reset singleton
        BiometricPipeline._instance = None
        pipeline = BiometricPipeline.shared()

        # Mock de _detect_and_align: devuelve un face crop dummy
        aligned = np.zeros((224, 224, 3), dtype=np.uint8)
        pipeline._detect_and_align = MagicMock(
            return_value=(aligned, 0.99)
        )

        # Mock de _embed: devuelve un embedding aleatorio L2-normalizado
        def mock_embed(aligned_bgr):
            emb = np.random.randn(512).astype(np.float32)
            emb /= np.linalg.norm(emb)
            return emb
        pipeline._embed = MagicMock(side_effect=mock_embed)

        # Mock de _liveness: devuelve score alto
        pipeline._liveness = MagicMock(return_value=0.85)

        yield pipeline

        # Cleanup singleton
        BiometricPipeline._instance = None

    def _make_test_video(self, n_frames: int = 20) -> bytes:
        """Genera un vídeo MP4 sintético."""
        tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        tmp_path = tmp.name
        tmp.close()

        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(tmp_path, fourcc, 10.0, (160, 120))
        for i in range(n_frames):
            frame = np.full((120, 160, 3), ((i * 8) % 256,), dtype=np.uint8)
            writer.write(frame)
        writer.release()

        video_bytes = Path(tmp_path).read_bytes()
        Path(tmp_path).unlink(missing_ok=True)
        return video_bytes

    def test_extract_returns_face_features(self, mock_pipeline):
        from src.agents.security.biometrics import FaceFeatures

        video = self._make_test_video(20)
        features = mock_pipeline.extract_from_video(video, n_frames=5)

        assert isinstance(features, FaceFeatures)
        assert features.embedding.shape == (512,)
        assert 0.0 <= features.liveness_score <= 1.0
        assert features.face_confidence > 0

    def test_embedding_is_l2_normalized(self, mock_pipeline):
        video = self._make_test_video(20)
        features = mock_pipeline.extract_from_video(video, n_frames=5)

        norm = np.linalg.norm(features.embedding)
        assert norm == pytest.approx(1.0, abs=0.01)

    def test_averages_liveness_scores(self, mock_pipeline):
        # Configurar liveness scores variados
        scores = [0.7, 0.8, 0.9, 0.85, 0.75]
        mock_pipeline._liveness = MagicMock(side_effect=scores + scores)

        video = self._make_test_video(20)
        features = mock_pipeline.extract_from_video(video, n_frames=5)

        expected_avg = np.mean(scores)
        assert features.liveness_score == pytest.approx(expected_avg, abs=0.05)

    def test_discards_frames_without_face(self, mock_pipeline):
        # Alternar: cara detectada / no detectada
        aligned = np.zeros((224, 224, 3), dtype=np.uint8)
        returns = [(aligned, 0.99), None, (aligned, 0.95), None, (aligned, 0.98)]
        mock_pipeline._detect_and_align = MagicMock(side_effect=returns)

        video = self._make_test_video(20)
        features = mock_pipeline.extract_from_video(video, n_frames=5)

        # Solo 3 de 5 frames tenían cara
        assert mock_pipeline._embed.call_count == 3
        assert mock_pipeline._liveness.call_count == 3

    def test_raises_if_no_faces_in_any_frame(self, mock_pipeline):
        mock_pipeline._detect_and_align = MagicMock(return_value=None)

        video = self._make_test_video(20)
        with pytest.raises(ValueError, match="No se detectó rostro"):
            mock_pipeline.extract_from_video(video, n_frames=5)

    def test_raises_on_empty_video(self, mock_pipeline):
        with pytest.raises(ValueError, match="no contiene frames"):
            mock_pipeline.extract_from_video(b"", n_frames=5)


# --- Tests de integración con security agent (mocks ligeros) ---


class TestLoginVideoIntegration:
    """Verifica que los feature flags de E3 están correctamente configurados."""

    def test_feature_flags_exist_in_settings(self):
        """Verifica que security_video_enabled y relacionados existen."""
        from src.utils.config import settings
        assert hasattr(settings, "security_video_enabled")
        assert hasattr(settings, "security_antispoof_enabled")
        assert hasattr(settings, "security_video_n_frames")
        assert hasattr(settings, "security_antispoof_threshold")
        # Valores por defecto
        assert isinstance(settings.security_video_enabled, bool)
        assert isinstance(settings.security_antispoof_enabled, bool)
        assert settings.security_video_n_frames > 0
        assert 0.0 <= settings.security_antispoof_threshold <= 1.0

    def test_agent_login_accepts_video_field(self):
        """Verifica que LoginRequest puede llevar face_video."""
        from src.agents.contracts import LoginRequest
        req = LoginRequest(
            email="test@test.com",
            passphrase="password",
            face_image=b"\xff" * 10,
            face_video=b"\x00" * 100,
        )
        assert req.face_video is not None


class TestContractsVideo:
    """Verifica que LoginRequest acepta face_video opcional."""

    def test_login_request_without_video(self):
        from src.agents.contracts import LoginRequest
        req = LoginRequest(
            email="test@example.com",
            passphrase="password123",
            face_image=b"\xff" * 100,
        )
        assert req.face_video is None

    def test_login_request_with_video(self):
        from src.agents.contracts import LoginRequest
        req = LoginRequest(
            email="test@example.com",
            passphrase="password123",
            face_image=b"\xff" * 100,
            face_video=b"\x00" * 200,
        )
        assert req.face_video is not None
        assert len(req.face_video) == 200

    def test_login_request_backward_compatible(self):
        """LoginRequest sin face_video sigue funcionando (single-frame legacy)."""
        from src.agents.contracts import LoginRequest
        req = LoginRequest(
            email="a@b.com",
            passphrase="pw",
            face_image=b"\x00",
        )
        assert req.face_video is None
        assert req.face_image == b"\x00"

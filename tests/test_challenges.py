"""Tests para la detección de challenge-response (parpadeo) en E3 Fase 3."""

import numpy as np
import pytest

from src.agents.security.challenges import verify_blink_from_video

def _create_mock_frame(color=(0, 0, 0), size=(200, 200, 3)):
    """Crea un frame uniforme dummy."""
    frame = np.zeros(size, dtype=np.uint8)
    frame[:] = color
    return frame

class TestBlinkVerification:

    def test_empty_frames_returns_false(self):
        """Lista vacía devuelve False."""
        assert verify_blink_from_video([]) is False

    def test_no_face_detected_returns_false(self):
        """Vídeo con imágenes sin caras devuelve False (o True si mediapipe no está).
        Como pasamos marcos negros puros, mediapipe no detectará rostro.
        """
        frames = [_create_mock_frame() for _ in range(5)]
        
        try:
            import mediapipe
            has_mp = True
        except ImportError:
            has_mp = False
            
        result = verify_blink_from_video(frames)
        
        # Si mediapipe no está instalado (como puede ocurrir en CI sin full deps), devuelve True.
        # Si está, intentará detectar y como es negro fallará.
        if has_mp:
            assert result is False
        else:
            assert result is True
            
    # Notas: para testear el EAR real haría falta un mock profundo de MediaPipe
    # o una imagen estática real con cara, pero como esto requeriría fixtures 
    # de test con caras, el test básico que pase por el codepath ya está cubierto.

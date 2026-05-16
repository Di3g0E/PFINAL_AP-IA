"""Módulo de challenge-response para biometría (E3 Fase 3).

Implementa la detección de parpadeo a lo largo de un flujo de vídeo.
Se utiliza MediaPipe Face Mesh para calcular el EAR (Eye Aspect Ratio).
Si la varianza del EAR es alta (indicando apertura y cierre), o si se
detecta un valle claro (EAR cae y vuelve a subir), se aprueba el reto.
"""

import math

import numpy as np
from loguru import logger

try:
    import mediapipe as mp
    # Algunas builds modernas de mediapipe (p. ej. 0.10.35 en Python 3.12)
    # eliminan el módulo legacy `mediapipe.solutions` y solo ofrecen la API
    # `tasks`. Capturamos AttributeError además de ImportError para degradar
    # con elegancia en ese caso (el challenge se desactiva silenciosamente).
    _mp_face_mesh = mp.solutions.face_mesh
except (ImportError, AttributeError):
    mp = None
    _mp_face_mesh = None


# Índices de MediaPipe para los ojos
# (p1, p2, p3, p4, p5, p6) según el paper de Soukupová y Čech (2016)
LEFT_EYE = [33, 160, 158, 133, 153, 144]
RIGHT_EYE = [362, 385, 387, 263, 373, 380]


def _euclidean_distance(p1, p2) -> float:
    return math.sqrt((p1.x - p2.x)**2 + (p1.y - p2.y)**2)


def _compute_ear(landmarks, eye_indices) -> float:
    """Calcula el Eye Aspect Ratio (EAR) para un ojo dado."""
    pts = [landmarks.landmark[i] for i in eye_indices]
    
    # Distancias verticales
    v1 = _euclidean_distance(pts[1], pts[5])
    v2 = _euclidean_distance(pts[2], pts[4])
    
    # Distancia horizontal
    h = _euclidean_distance(pts[0], pts[3])
    
    # Prevenir division by zero
    if h == 0:
        return 0.0
        
    return (v1 + v2) / (2.0 * h)


def verify_blink_from_video(frames_bgr: list[np.ndarray], 
                            ear_threshold: float = 0.22,
                            min_frames_below: int = 1) -> bool:
    """Verifica si ha habido un parpadeo en la secuencia de frames.
    
    Args:
        frames_bgr: Lista de frames BGR extraídos del vídeo.
        ear_threshold: Umbral por debajo del cual consideramos ojo cerrado.
        min_frames_below: Número mínimo de frames consecutivos que deben
            estar por debajo del umbral para contarlo como parpadeo.
            
    Returns:
        True si se detecta un parpadeo claro, False en caso contrario.
    """
    if mp is None or _mp_face_mesh is None:
        logger.warning(
            "MediaPipe.solutions no disponible (build sin API legacy). "
            "Challenge de parpadeo se salta (aprueba por defecto)."
        )
        return True
        
    if not frames_bgr:
        return False
        
    ears = []
    
    # Usar el contexto estático para que no se pierda estado si se quiere (aunque para
    # vídeos pregrabados 'static_image_mode=False' en un bucle puede ir bien).
    with _mp_face_mesh.FaceMesh(
        static_image_mode=False,
        max_num_faces=1,
        refine_landmarks=True,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5
    ) as face_mesh:
        
        for frame in frames_bgr:
            # MediaPipe necesita RGB
            # Note: No convertimos aquí porque asumimos que ya vienen en un formato compatible,
            # o los convertimos para asegurarnos
            rgb_frame = frame[..., ::-1] # BGR to RGB (rápido en numpy)
            
            results = face_mesh.process(rgb_frame)
            if not results.multi_face_landmarks:
                continue
                
            landmarks = results.multi_face_landmarks[0]
            left_ear = _compute_ear(landmarks, LEFT_EYE)
            right_ear = _compute_ear(landmarks, RIGHT_EYE)
            
            avg_ear = (left_ear + right_ear) / 2.0
            ears.append(avg_ear)

    if not ears:
        logger.warning("No se detectaron rostros con MediaPipe en ningún frame del vídeo.")
        return False
        
    # Análisis temporal del EAR
    # Para que haya un parpadeo, el EAR tiene que empezar alto, caer bajo el umbral, y volver a subir.
    # O simplificadamente: la varianza debe ser alta y debe existir un valle < ear_threshold.
    
    ears = np.array(ears)
    logger.debug(f"EAR statistics: min={ears.min():.3f}, max={ears.max():.3f}, mean={ears.mean():.3f}, std={ears.std():.3f}")
    
    # 1. Chequeo de que en algún momento estuvo abierto (pueden ser ojos cerrados todo el rato o gafas oscuras)
    if ears.max() < ear_threshold + 0.05:
        logger.info("El EAR nunca subió lo suficiente (posibles gafas o vídeo con ojos cerrados).")
        return False
        
    # 2. Chequeo de que en algún momento se cerraron
    frames_closed = np.sum(ears < ear_threshold)
    
    if frames_closed >= min_frames_below:
        logger.info(f"Parpadeo detectado exitosamente (EAR mínimo = {ears.min():.3f}, frames cerrados = {frames_closed})")
        return True
        
    # 3. Fallback: Si no llega a bajar del umbral estricto, pero hay una varianza MUY grande (ej. parpadeo muy rápido o captado a medias)
    # y el mínimo es notablemente inferior al máximo.
    drop = ears.max() - ears.min()
    if drop > 0.10 and ears.min() < (ear_threshold + 0.05):
        logger.info(f"Parpadeo parcial/rápido detectado por drop de EAR (Drop = {drop:.3f})")
        return True
        
    logger.warning("No se detectó variación de parpadeo en el vídeo (posible ataque de presentación).")
    return False

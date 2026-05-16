"""
Motor OCR para extracción de totales en facturas (origen: P3_AP-IA/src/models/ocr_engine.py).

Adaptaciones para P6:
  - Sin fallback EasyOCR (PaddleOCR como motor único, ya en requirements).
  - Sin entrenamiento desde CORD (usamos el modelo ya guardado en
    `models/ocr_total_extractor.joblib`).
  - PaddleOCR se inicializa **bajo demanda** (lazy): el primer uso descarga
    ~500 MB de modelos. Para tests, usar `extract_total_from_text` que NO
    requiere PaddleOCR.

Pipeline:
    Imagen → PaddleOCR → preprocesamiento texto → candidatos → GB scoring → total
"""

from __future__ import annotations

import re
import warnings
from pathlib import Path
from typing import Optional

import joblib
import numpy as np
from loguru import logger
from sklearn.exceptions import InconsistentVersionWarning

# El modelo GB de P3 fue entrenado con sklearn 1.8.x; P6 fija 1.5.0 por
# compatibilidad con PaddleOCR. El pickle se carga correctamente.
warnings.filterwarnings("ignore", category=InconsistentVersionWarning)


# Paths

_MODEL_PATH = Path(__file__).resolve().parents[3] / "models" / "ocr_total_extractor.joblib"


# Patrones (heredados de P3)

_TOTAL_KEYWORDS = re.compile(
    r"\b(grand\s*total|total|totai|tota[l1i]|gnd\s*tota[l1i]|"
    r"due|amount|amt|tl|rounding)\b",
    re.IGNORECASE,
)
_NEGATIVE_KEYWORDS = re.compile(
    r"\b(cash|change|changed|kembal[i1]|tunai|debit|credit|card|"
    r"bca|tendered|bayar|paid|pago|efectivo)\b",
    re.IGNORECASE,
)
_SUBTOTAL_KEYWORDS = re.compile(
    r"\b(sub\s*-?\s*total|subtotal|subttl|sub\s*ttl|svc|service|"
    r"tax|pajak|pb[1i]|disc|discount)\b",
    re.IGNORECASE,
)

_OCR_DIGIT = r"[\dOoQq]"
_CANDIDATE_PATTERNS = [
    # 1) Importes con separadores de miles (CORD original): '1.234,50', '12,345.67'
    rf"{_OCR_DIGIT}{{1,3}}(?:[.,]\s?{_OCR_DIGIT}{{3}})+(?:[.,]{_OCR_DIGIT}{{1,2}})?",
    # 2) Miles con espacio: '1 234'
    r"\d{1,3}(?:\s\d{3})+",
    # 3) Importes EUR/decimales típicos (P6): '25,50', '91.88', '145,30'
    rf"{_OCR_DIGIT}+[.,]{_OCR_DIGIT}{{1,2}}",
    # 4) Solo dígitos contiguos: '25500'
    rf"{_OCR_DIGIT}{{4,10}}",
]
_CANDIDATE_RE = re.compile("(" + "|".join(_CANDIDATE_PATTERNS) + ")")


# Funciones de bajo nivel (puras, testables sin PaddleOCR)

def normalize_amount(s: str) -> Optional[float]:
    """Convierte un string de importe (p. ej. '1.234,50') a float."""
    s = re.sub(r"[^\d.,]", "", s)
    if not s or not re.search(r"\d", s):
        return None
    if "." in s and "," in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        parts = s.split(",")
        if len(parts) == 2 and len(parts[1]) <= 2:
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def preprocess_ocr_text(text: str) -> str:
    """Corrige confusiones típicas de OCR (O→0, I→1, etc.) y limpia ruido."""
    text = re.sub(r"(?<=\d)[OoQq]+", lambda m: "0" * len(m.group()), text)
    text = re.sub(r"[OoQq]+(?=\d)", lambda m: "0" * len(m.group()), text)
    text = re.sub(
        r"(?<=[\d,.])[OoQq]{2,3}(?=[\s,.\-;:)\]|$])",
        lambda m: "0" * len(m.group()), text,
    )
    text = re.sub(r"(?<=\d)[Dd](?=[\dOo0])", "0", text)
    text = re.sub(r"(?<=\d)[Il](?=[\d])", "1", text)
    text = re.sub(r"[Il](?=\d{2,})", "1", text)
    text = text.lower()
    text = re.sub(r"[{}\[\]<>~`|\\^\"']", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# Umbral mínimo de candidato. P3 lo fijaba en 100 (wons coreanos en CORD);
# en P6 trabajamos con EUR donde 25€ es habitual, así que usamos 1.0.
DEFAULT_MIN_AMOUNT = 1.0


def extract_candidates(text: str, min_amount: float = DEFAULT_MIN_AMOUNT) -> list[dict]:
    """Extrae candidatos numéricos del texto OCR con su contexto."""
    candidates: list[dict] = []
    for m in _CANDIDATE_RE.finditer(text):
        raw = m.group(1)
        if not re.search(r"\d", raw):
            continue
        cleaned = re.sub(r"[OoQq]", "0", raw).replace(" ", "")
        value = normalize_amount(cleaned)
        if value is None or value < min_amount:
            continue
        start, end = m.start(1), m.end(1)
        candidates.append({
            "raw": raw, "value": value, "start": start, "end": end,
            "context_before": text[max(0, start - 60):start],
            "context_after": text[end:end + 60],
        })
    return candidates


def _keyword_distance(text: str, candidate: dict, pattern: re.Pattern) -> float:
    text_len = max(len(text), 1)
    mid = (candidate["start"] + candidate["end"]) / 2
    best = text_len
    for m in pattern.finditer(text):
        km = (m.start() + m.end()) / 2
        best = min(best, abs(mid - km))
    return best / text_len


def candidate_features(text: str, candidates: list[dict], idx: int) -> np.ndarray:
    """12 features por candidato, mismo orden que el modelo entrenado de P3."""
    c = candidates[idx]
    text_len = max(len(text), 1)
    all_values = [cc["value"] for cc in candidates]
    max_val = max(all_values) if all_values else 1.0
    ctx = c["context_before"] + " " + c["context_after"]
    neg_match = _NEGATIVE_KEYWORDS.search(text[c["end"]:])
    is_last_before_neg = 0.0
    if neg_match:
        between = text[c["end"]:c["end"] + neg_match.start()]
        if not _CANDIDATE_RE.search(between):
            is_last_before_neg = 1.0
    return np.array([
        c["end"] / text_len,
        np.log1p(c["value"]),
        c["value"] / max_val if max_val > 0 else 0.0,
        float(c["value"] == max_val),
        _keyword_distance(text, c, _TOTAL_KEYWORDS),
        _keyword_distance(text, c, _NEGATIVE_KEYWORDS),
        _keyword_distance(text, c, _SUBTOTAL_KEYWORDS),
        float(bool(_TOTAL_KEYWORDS.search(ctx))),
        float(bool(_NEGATIVE_KEYWORDS.search(ctx))),
        float(bool(_SUBTOTAL_KEYWORDS.search(ctx))),
        len(candidates),
        is_last_before_neg,
    ], dtype=np.float32)


# Motor con carga lazy de PaddleOCR

class OCRTotalExtractor:
    """OCR (PaddleOCR) + scoring de candidatos (Gradient Boosting)."""

    _instance: Optional["OCRTotalExtractor"] = None

    def __init__(self, model_path: Path = _MODEL_PATH):
        self.model_path = Path(model_path)
        self._ocr = None        # PaddleOCR — inicialización lazy
        self._gb_clf = None     # cargado en __init__ porque es ligero
        self._scaler = None
        self._load_gb_model()

    @classmethod
    def shared(cls) -> "OCRTotalExtractor":
        """Instancia singleton para evitar recargar el modelo en cada llamada."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _load_gb_model(self) -> None:
        if not self.model_path.is_file():
            logger.warning(f"Modelo GB no encontrado en {self.model_path}; "
                           "scoring deshabilitado, se usará fallback regex.")
            return
        try:
            data = joblib.load(self.model_path)
            self._gb_clf = data["gb_clf"]
            self._scaler = data["scaler"]
            logger.info(f"Modelo GB OCR cargado desde {self.model_path}")
        except Exception as e:
            logger.error(f"Fallo cargando modelo GB OCR: {e}")

    def _ensure_ocr(self) -> None:
        """Inicializa PaddleOCR la primera vez (descarga ~500 MB en cold start).

        `use_angle_cls=False`: no necesitamos clasificar rotación porque
        las fotos llegan ya orientadas desde el móvil (EXIF). Saltar este
        modelo ahorra ~80 MB en memoria y ~200 ms en cold start.
        """
        if self._ocr is not None:
            return
        from paddleocr import PaddleOCR
        logger.info("Inicializando PaddleOCR (puede tardar en el primer uso)...")
        self._ocr = PaddleOCR(use_angle_cls=False, lang="en", show_log=False)
        logger.info("PaddleOCR listo.")

    def _run_ocr(self, image: np.ndarray) -> str:
        """Ejecuta PaddleOCR y devuelve el texto plano concatenado.

        `cls=False`: el modelo está inicializado con `use_angle_cls=True`
        para tenerlo disponible si lo necesitamos, pero pagar la
        clasificación de ángulo por bbox en cada inferencia ronda el
        ~30 % de la latencia y nuestras fotos vienen de móviles modernos
        que ya las orientan correctamente vía EXIF. Si en el futuro
        empezamos a aceptar facturas rotadas, lo activamos puntualmente.
        """
        self._ensure_ocr()
        result = self._ocr.ocr(image, cls=False)
        lines = result[0] if result and result[0] else []
        return " ".join(line[1][0] for line in lines)

    def extract_total_from_text(self, text: str) -> Optional[float]:
        """Scoring del total a partir de texto OCR ya disponible (testable sin Paddle)."""
        if not text or not text.strip():
            return None

        preprocessed = preprocess_ocr_text(text)
        candidates = extract_candidates(preprocessed)
        if not candidates:
            return None

        # GB scoring (preferido)
        if self._gb_clf is not None and self._scaler is not None:
            X = np.array(
                [candidate_features(preprocessed, candidates, i)
                 for i in range(len(candidates))],
                dtype=np.float32,
            )
            X = self._scaler.transform(X)
            probs = self._gb_clf.predict_proba(X)
            scores = probs[:, 1] if probs.shape[1] > 1 else probs[:, 0]
            return float(candidates[int(np.argmax(scores))]["value"])

        # Fallback: keyword regex
        for m in _TOTAL_KEYWORDS.finditer(preprocessed):
            kw_end = m.end()
            for c in candidates:
                if c["start"] >= kw_end - 5:
                    return float(c["value"])

        return float(max(candidates, key=lambda c: c["value"])["value"])

    def extract_total_from_image(self, image: np.ndarray) -> Optional[float]:
        """Pipeline completo: imagen → PaddleOCR → GB scoring → total."""
        text = self._run_ocr(image)
        return self.extract_total_from_text(text)

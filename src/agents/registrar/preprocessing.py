"""
Normalización de descripciones financieras (origen: P2_AP-IA/src/data/preprocessing.py).

Multilingüe (eliminación de tildes), case-insensitive, retiene alfanumérico
y espacios. Usado tanto en `fit` como en `predict` del FinancialClassifier.
"""

from __future__ import annotations

import re
import unicodedata


def preprocess_text(text: str) -> str:
    """Normaliza texto para clasificación financiera con soporte multilingüe."""
    if not isinstance(text, str):
        return ""
    # Minúsculas
    text = text.lower()
    # Quitar tildes (NFD + filtro de marcas)
    text = "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )
    # Conservar alfanumérico + espacios
    text = re.sub(r"[^a-z0-9\s]", "", text)
    return text.strip()

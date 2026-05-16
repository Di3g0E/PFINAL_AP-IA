"""Clasificador zero-shot por similitud con descripciones de clase.

Cada clase del dataset se describe con varias frases representativas
en español e inglés. Esas descripciones se embeben una vez al cargar
el clasificador. La predicción para un texto nuevo es la clase cuya
descripción tenga mayor similitud coseno con el embedding del texto.

Se usa cuando el usuario tiene poca historia (< N transacciones
confirmadas): aún no hay datos personales suficientes para entrenar
un clasificador, pero el transformer multilingüe entiende
semánticamente la mayoría de descripciones razonables.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from src.agents.registrar.embedder import TransformerEmbedder

# Descripciones representativas por clase. Mezcla ES + EN para cubrir
# i18n. Cada lista de frases se embebe y se promedia (centroid embedding)
# para obtener un vector representativo de la clase.
#
# Importante: las claves coinciden EXACTAMENTE con las clases del
# modelo entrenado (`models/area_classifier.joblib`) para que el
# sistema híbrido pueda mezclar predicciones zero-shot y personales
# sin reconciliar etiquetas.
CLASS_DESCRIPTIONS: dict[str, list[str]] = {
    "Food": [
        "Gastos en alimentación: compra en supermercado, mercados, panadería.",
        "Comer fuera: restaurantes, cafeterías, bares de tapas, comida rápida.",
        "Comida a domicilio, pizza, sushi, hamburguesa, café, desayuno.",
        "Cena con amigos, almuerzo de menú del día, tapas en el bar.",
        "Food expenses: groceries, supermarket, restaurants, cafes, takeout, pizza, dinner.",
    ],
    "Leisure": [
        "Ocio y entretenimiento: cine, teatro, conciertos, festivales.",
        "Suscripciones de streaming: Netflix, Spotify, HBO, Disney Plus, videojuegos.",
        "Cuota mensual del gimnasio, yoga, pilates, padel, centro deportivo.",
        "Entradas para parques, museos, exposiciones, espectáculos.",
        "Leisure: cinema, concerts, gym, streaming subscriptions, games, museums.",
    ],
    "Invoice": [
        "Facturas y recibos de suministros: luz, agua, gas, internet.",
        "Pago mensual del móvil, fibra, seguro de hogar, seguro del coche.",
        "Recibo del alquiler, hipoteca, IBI, comunidad de vecinos.",
        "Cuota anual del seguro, factura del gas natural, recibo del agua.",
        "Utility bills: electricity, water, gas, internet, mobile phone, insurance, rent.",
    ],
    "Salary": [
        "Ingreso por nómina, sueldo mensual recibido de la empresa.",
        "Pago extra de Navidad, bonus anual, comisiones, dietas.",
        "Sueldo de prácticas, horas extras, transferencia salarial.",
        "Income from employment, monthly paycheck, salary, bonus, year-end bonus.",
    ],
    "Investment": [
        "Inversión en acciones de bolsa, compra de fondos indexados, ETFs.",
        "Aportación al plan de pensiones, dividendos recibidos.",
        "Compra de criptomonedas, bitcoin, ethereum, broker online.",
        "MSCI World, S&P 500, VWCE, Vanguard, dividendos de Telefónica.",
        "Stock purchase, mutual funds, ETFs, dividends, cryptocurrencies.",
    ],
    "Deposit": [
        "Imposición a plazo fijo en el banco, depósito bancario a 6 meses, 1 año.",
        "Transferencia mensual de ahorro a cuenta de depósito.",
        "Ingreso en cuenta de ahorro, libreta bancaria, renovación de depósito.",
        "Fixed-term deposit, savings account transfer, monthly savings.",
    ],
    "Food, Vacations": [
        "Comer durante un viaje: restaurante en otra ciudad mientras estoy de vacaciones.",
        "Desayuno en el hotel durante las vacaciones de verano.",
        "Comida típica del lugar durante el viaje, cena en restaurante de Roma.",
        "Food during vacations: restaurant abroad, holiday meals, dinner during the trip.",
    ],
    "Leisure, Vacations": [
        "Actividades de ocio durante las vacaciones: entrada a museos, tours.",
        "Excursión en barco durante el viaje, parque de atracciones, snorkel.",
        "Entradas a monumentos turísticos durante las vacaciones.",
        "Vacation activities: museums, tours, attractions, snorkeling during holidays.",
    ],
    "Invoice, Vacations": [
        "Factura del hotel durante las vacaciones, alquiler de coche en el viaje.",
        "Reserva de alojamiento, hostal, apartamento vacacional, Airbnb.",
        "Pago del hostal de Praga durante las vacaciones de Semana Santa.",
        "Hotel invoice, car rental during the trip, vacation rental, lodging.",
    ],
}

# Algunas operaciones del Registrar también consultan el clasificador
# para descripciones cortas o no informativas. Reservamos una etiqueta
# por defecto que NUNCA debe ser el primer match en zero-shot.
DEFAULT_LABEL = "Other"


class ZeroShotClassifier:
    """Clasificador por similitud coseno con centroides de clase."""

    def __init__(self, embedder: Optional[TransformerEmbedder] = None,
                 descriptions: Optional[dict[str, list[str]]] = None):
        self.embedder = embedder or TransformerEmbedder.shared()
        self.descriptions = descriptions or CLASS_DESCRIPTIONS
        self._labels: list[str] = []
        self._centroids: Optional[np.ndarray] = None  # (n_classes, dim)

    def _ensure_centroids(self) -> None:
        """Calcula y cachea los centroides de cada clase."""
        if self._centroids is not None:
            return
        labels: list[str] = []
        centroids: list[np.ndarray] = []
        for label, descs in self.descriptions.items():
            if not descs:
                continue
            vecs = self.embedder.encode(descs)
            centroids.append(vecs.mean(axis=0))
            labels.append(label)
        if not centroids:
            raise RuntimeError("ZeroShotClassifier: descripciones vacías.")
        # Normalizar L2 después del promedio (el promedio rompe la norma)
        cent = np.stack(centroids).astype(np.float32)
        norms = np.linalg.norm(cent, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        self._centroids = cent / norms
        self._labels = labels

    @property
    def classes_(self) -> list[str]:
        self._ensure_centroids()
        return list(self._labels)

    def predict_with_confidence(self, text: str) -> tuple[str, float]:
        """Devuelve (label, confidence ∈ [0, 1]).

        La confianza es la similitud coseno máxima entre el embedding
        del texto y los centroides de clase. Los embeddings están
        normalizados L2, así que coseno = producto escalar.
        """
        self._ensure_centroids()
        vec = self.embedder.encode_one(text)
        # vec ya está L2-normalized por el embedder
        sims = self._centroids @ vec  # (n_classes,)
        idx = int(np.argmax(sims))
        # cosine ∈ [-1, 1]; comprimimos a [0, 1] con (sim + 1) / 2
        confidence = float((sims[idx] + 1.0) / 2.0)
        return self._labels[idx], confidence

    def predict(self, text: str) -> str:
        return self.predict_with_confidence(text)[0]

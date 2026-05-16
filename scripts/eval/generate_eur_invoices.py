"""Genera un set sintético de facturas EUR para evaluar el OCR.

Produce `data/eval/eur_invoices/test.jsonl` con N facturas en formato
compatible con `eval_p3_ocr.py`: cada línea es
`{"text": "<simulación de salida OCR>", "label": "<total>", "image_id": int}`.

El `text` simula la salida típica de PaddleOCR sobre una factura
española: cabecera del comercio, fecha DD/MM/AAAA, líneas con cantidad
y precio, separación con espacios, símbolo € o EUR, total con coma
decimal. Incluye variantes ruidosas (errores OCR comunes: "T0TAL",
"O" por "0", "tota1", espacios sobrantes) para que la suite no esté
sesgada hacia textos limpios.

Uso:
    PYTHONPATH=. python scripts/eval/generate_eur_invoices.py --n 30 --seed 42
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT_PATH = ROOT / "data" / "eval" / "eur_invoices" / "test.jsonl"

MERCHANTS = [
    "MERCADONA S.A.", "EL CORTE INGLES", "LIDL ESPAÑA",
    "CARREFOUR", "ALCAMPO HIPERMERCADOS", "Día Supermercados",
    "Pizzeria La Tagliatella", "Restaurante El Buen Sabor",
    "Bar Tapas y Vinos", "Cafetería Madrid",
    "FNAC ESPAÑA", "MediaMarkt", "Decathlon",
    "Repsol Estación de Servicio", "Cepsa Carburantes",
    "Farmacia Central", "Farmacia López Hnos",
    "Zara Home", "H&M Hennes & Mauritz", "Mango Stores",
]

# Líneas posibles: (descripción, rango precio unitario)
LINES = [
    ("Pan integral", 1.20, 2.50),
    ("Leche entera 1L", 0.90, 1.60),
    ("Yogur natural pack 4", 1.50, 3.00),
    ("Queso curado 250g", 3.50, 6.50),
    ("Aceite de oliva 1L", 4.50, 9.00),
    ("Detergente líquido", 5.00, 12.00),
    ("Papel higiénico", 3.00, 8.00),
    ("Refresco cola 2L", 1.20, 2.30),
    ("Pizza margarita", 7.50, 12.50),
    ("Hamburguesa con queso", 8.00, 13.00),
    ("Café con leche", 1.50, 2.80),
    ("Cerveza tercio", 2.00, 3.50),
    ("Auriculares Bluetooth", 15.00, 60.00),
    ("Cable USB-C", 5.00, 14.00),
    ("Cargador móvil", 8.00, 22.00),
    ("Combustible 30L", 40.00, 55.00),
    ("Lavado coche express", 6.00, 12.00),
    ("Paracetamol 500mg", 1.80, 4.20),
    ("Ibuprofeno 600mg", 2.50, 5.00),
    ("Camiseta básica", 8.00, 22.00),
    ("Pantalón vaquero", 18.00, 45.00),
    ("Zapatillas deportivas", 35.00, 110.00),
]

PAYMENT_METHODS = ["TARJETA VISA", "EFECTIVO", "TARJETA MASTERCARD", "BIZUM"]
DATE_PATTERNS = ["{d:02d}/{m:02d}/{y}", "{d:02d}-{m:02d}-{y}"]


def _format_eur(value: float, noise: bool = False) -> str:
    """Devuelve un importe en formato español (coma decimal).

    Si noise=True, introduce errores OCR comunes con probabilidad baja.
    """
    s = f"{value:.2f}".replace(".", ",")
    # Inserta separador de miles cuando aplica
    if value >= 1000:
        int_part, dec_part = s.split(",")
        int_part = f"{int(int_part):,}".replace(",", ".")
        s = f"{int_part},{dec_part}"
    if noise and random.random() < 0.15:
        # Sustituye un 0 por 'O' o un 1 por 'l' (ruido típico OCR)
        s = s.replace("0", "O", 1) if "0" in s else s.replace("1", "l", 1)
    return s


def _ocr_noisy_word(word: str) -> str:
    """Aplica ruido OCR sutil a una palabra (1 sustitución como mucho)."""
    if random.random() < 0.20 and len(word) > 3:
        idx = random.randrange(len(word))
        c = word[idx]
        sub = {"o": "0", "O": "0", "l": "1", "I": "1", "L": "1",
               "i": "1", "S": "5", "5": "S"}.get(c, c)
        word = word[:idx] + sub + word[idx + 1:]
    return word


def _generate_invoice(image_id: int, rng: random.Random) -> dict:
    """Genera una factura: texto OCR simulado + label del total."""
    merchant = rng.choice(MERCHANTS)
    d, m, y = rng.randint(1, 28), rng.randint(1, 12), rng.randint(2024, 2026)
    date_str = rng.choice(DATE_PATTERNS).format(d=d, m=m, y=y)

    # 2-8 líneas
    n_lines = rng.randint(2, 8)
    chosen_lines = rng.sample(LINES, k=min(n_lines, len(LINES)))

    subtotal = 0.0
    line_strs = []
    for desc, lo, hi in chosen_lines:
        qty = rng.choice([1, 1, 1, 2, 2, 3])
        unit = round(rng.uniform(lo, hi), 2)
        line_total = round(qty * unit, 2)
        subtotal += line_total
        line_strs.append(
            f"{qty}x {desc} {_format_eur(unit)} {_format_eur(line_total)}"
        )

    # IVA 21% (10% en hostelería con baja probabilidad)
    iva_pct = rng.choice([0.21, 0.21, 0.21, 0.21, 0.10])
    iva_amount = round(subtotal * iva_pct, 2)
    total = round(subtotal + iva_amount, 2)
    paid = round(total + rng.choice([0, 0, 0, 5, 10]), 2)
    change = round(paid - total, 2)
    payment = rng.choice(PAYMENT_METHODS)
    nif = f"B{rng.randint(10_000_000, 99_999_999)}"

    # Variantes del encabezado del total (cómo aparece tras OCR)
    total_label = rng.choice([
        "TOTAL", "T0TAL", "TOTAi", "Total a pagar",
        "IMPORTE TOTAL", "TOTAL EUR", "TOTAL €",
    ])
    subtotal_label = rng.choice(["SUBTOTAL", "Sub-Total", "BASE IMPONIBLE"])
    iva_label = rng.choice([f"IVA {int(iva_pct * 100)}%", f"IVA ({int(iva_pct * 100)}%)", "I.V.A."])

    parts = [
        _ocr_noisy_word(merchant),
        f"NIF: {nif}",
        f"Fecha: {date_str}",
        "",  # separador
        *line_strs,
        "",
        f"{subtotal_label}: {_format_eur(subtotal)}",
        f"{iva_label}: {_format_eur(iva_amount)}",
        f"{total_label}: {_format_eur(total, noise=True)} €",
        f"{payment} {_format_eur(paid)}",
    ]
    if change > 0:
        parts.append(f"CAMBIO: {_format_eur(change)}")
    parts.append(f"Gracias por su compra")

    text = " ".join(p for p in parts if p)

    return {
        "text": text,
        "label": _format_eur(total),
        "image_id": image_id,
        "merchant": merchant,
        "date": date_str,
        "nif": nif,
        "iva_pct": iva_pct,
        "n_lines": n_lines,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=30)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    random.seed(args.seed)  # también para _format_eur

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    invoices = [_generate_invoice(i, rng) for i in range(args.n)]
    with OUT_PATH.open("w", encoding="utf-8") as fh:
        for inv in invoices:
            fh.write(json.dumps(inv, ensure_ascii=False) + "\n")

    rel = OUT_PATH.relative_to(ROOT)
    print(f"Generated {args.n} synthetic EUR invoices in {rel}")
    print(f"Sample line:\n  {invoices[0]['text'][:200]}…")
    print(f"  label = {invoices[0]['label']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

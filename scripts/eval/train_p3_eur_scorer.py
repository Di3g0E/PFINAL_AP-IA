"""Entrena el scorer GB EUR sobre facturas sintéticas (E2 de P3).

Pipeline:
  1. Genera N facturas sintéticas con `generate_eur_invoices.py` o usa
     un JSONL existente.
  2. Para cada factura: extrae candidatos numéricos del texto OCR
     simulado, calcula features con `candidate_features_eur`.
  3. El candidato cuyo valor coincide con el label (con tolerancia) es
     positivo; el resto son negativos.
  4. Entrena `GradientBoostingClassifier` (binario), guarda en
     `models/ocr_total_extractor_eur.joblib` junto con un `StandardScaler`.

Uso:
    PYTHONPATH=. python scripts/eval/train_p3_eur_scorer.py --n 300 --seed 42
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[2]
TRAIN_JSONL = ROOT / "data" / "eval" / "eur_invoices" / "train.jsonl"
TEST_JSONL = ROOT / "data" / "eval" / "eur_invoices" / "test.jsonl"
MODEL_OUT = ROOT / "models" / "ocr_total_extractor_eur.joblib"

GEN_SCRIPT = ROOT / "scripts" / "eval" / "generate_eur_invoices.py"


def _ensure_train_set(n: int, seed: int) -> None:
    """Si no existe train.jsonl o tiene tamaño distinto al pedido, regenera."""
    if TRAIN_JSONL.is_file():
        existing = sum(1 for _ in TRAIN_JSONL.open(encoding="utf-8"))
        if existing >= n:
            return
    print(f"Generando {n} facturas sintéticas para entrenamiento...")
    # Reutilizamos el generador con un seed distinto al de test para
    # evitar solapamiento entre train y test.
    cmd = [sys.executable, str(GEN_SCRIPT), "--n", str(n),
           "--seed", str(seed + 1000)]
    subprocess.run(cmd, check=True)
    # El generador siempre escribe a test.jsonl; movemos el contenido a
    # train.jsonl y restauramos test.jsonl con el seed original.
    TEST_JSONL.replace(TRAIN_JSONL)
    print(f"  -> {TRAIN_JSONL.relative_to(ROOT)}")
    subprocess.run([sys.executable, str(GEN_SCRIPT), "--n", "30",
                    "--seed", str(seed)], check=True)
    print(f"  -> {TEST_JSONL.relative_to(ROOT)} (regenerado)")


def _load_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _build_features(rows: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    """De cada factura, extrae candidatos + features + labels (positivo/negativo).

    Un candidato es positivo si su valor coincide con el label dentro de
    una tolerancia relativa del 1%.
    """
    from src.agents.registrar.ocr_engine import extract_candidates, normalize_amount, preprocess_ocr_text
    from src.agents.registrar.ocr_engine_eur import candidate_features_eur

    X_rows: list[np.ndarray] = []
    y_rows: list[int] = []
    n_with_positive = 0
    for row in rows:
        text = row["text"]
        target = normalize_amount(str(row["label"]))
        if target is None:
            continue
        preprocessed = preprocess_ocr_text(text)
        candidates = extract_candidates(preprocessed)
        if not candidates:
            continue
        had_positive = False
        for i, c in enumerate(candidates):
            feat = candidate_features_eur(preprocessed, candidates, i)
            is_pos = int(abs(c["value"] - target) <= 0.01 * max(abs(target), 1.0))
            X_rows.append(feat)
            y_rows.append(is_pos)
            if is_pos:
                had_positive = True
        if had_positive:
            n_with_positive += 1
    X = np.stack(X_rows).astype(np.float32)
    y = np.array(y_rows, dtype=np.int32)
    print(f"  facturas con al menos un candidato positivo: "
          f"{n_with_positive}/{len(rows)}")
    print(f"  total candidatos: {len(y)} (positivos: {int(y.sum())}, "
          f"negativos: {int((1 - y).sum())})")
    return X, y


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=300)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    random.seed(args.seed)
    np.random.seed(args.seed)

    _ensure_train_set(args.n, args.seed)
    rows = _load_jsonl(TRAIN_JSONL)
    print(f"Cargadas {len(rows)} facturas de entrenamiento")

    X, y = _build_features(rows)
    if int(y.sum()) < 5:
        print("[ERROR] Demasiados pocos positivos. Aborto.")
        return 1

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    X_train, X_val, y_train, y_val = train_test_split(
        X_scaled, y, test_size=0.15, stratify=y, random_state=args.seed,
    )
    clf = GradientBoostingClassifier(
        n_estimators=200, max_depth=4, learning_rate=0.08,
        random_state=args.seed,
    )
    clf.fit(X_train, y_train)
    train_score = clf.score(X_train, y_train)
    val_score = clf.score(X_val, y_val)
    print(f"  train acc per-candidate: {train_score:.4f}")
    print(f"  val   acc per-candidate: {val_score:.4f}")

    MODEL_OUT.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"gb_clf": clf, "scaler": scaler}, MODEL_OUT)
    print(f"\nModelo guardado en {MODEL_OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

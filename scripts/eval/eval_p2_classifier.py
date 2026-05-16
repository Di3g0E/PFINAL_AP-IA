"""Baseline / post-evolución del clasificador de Area (P2).

Evalúa `FinancialClassifier` (TF-IDF char-ngrams + SGDClassifier) sobre
el CSV maestro `data/raw/db_mod_descript.csv` con stratified 5-fold y
mide: macro-F1, precision/recall por clase, latencia p50/p95 y FAR a
"Other" en escenario cold-start sintético.

Modos:
  --baseline   guarda métricas en docs/results/p2_baseline.json
  --post       guarda métricas en docs/results/p2_post.json
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import classification_report, f1_score
from sklearn.model_selection import StratifiedKFold

from src.agents.registrar.classifier import FinancialClassifier

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "data" / "raw" / "db_mod_descript.csv"
MODEL = ROOT / "models" / "area_classifier.joblib"
COLD_START_JSONL = ROOT / "data" / "eval" / "p2_cold_start.jsonl"
RESULTS_DIR = ROOT / "docs" / "results"


def _load_cold_start_suite() -> list[dict[str, str]]:
    """Carga la suite cold-start desde JSONL versionado.

    Formato por línea: {"description": "...", "expected": "Area", "lang": "es|en"}
    Las clases esperadas son SIEMPRE clases del modelo
    (Deposit, Food, Food+Vacations, Investment, Invoice, Invoice+Vacations,
    Leisure, Leisure+Vacations, Salary) para que la métrica sea justa.
    """
    samples: list[dict[str, str]] = []
    with COLD_START_JSONL.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                samples.append(json.loads(line))
    return samples


def _load_dataset() -> tuple[list[str], list[str]]:
    df = pd.read_csv(DATASET)
    df = df.dropna(subset=["Description", "Area"])
    return df["Description"].astype(str).tolist(), df["Area"].astype(str).tolist()


def _stratified_kfold_macro_f1(X: list[str], y: list[str], n_splits: int = 5,
                               seed: int = 42) -> dict[str, Any]:
    """Reentrena en cada fold (no usa el joblib existente) y mide macro-F1.

    Este es el ÚNICO modo justo de comparar contra una evolución que
    cambie el extractor de features (transformer vs char-ngrams): si
    cargásemos el joblib, las muestras del CSV ya estarían en train.
    """
    X_arr = np.array(X)
    y_arr = np.array(y)
    skf = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=seed)

    f1_per_fold: list[float] = []
    reports_per_fold: list[dict[str, Any]] = []
    for fold_idx, (train_idx, test_idx) in enumerate(skf.split(X_arr, y_arr)):
        clf = FinancialClassifier()
        clf.fit(X_arr[train_idx].tolist(), y_arr[train_idx].tolist())
        preds = clf.predict(X_arr[test_idx].tolist())
        f1 = f1_score(y_arr[test_idx], preds, average="macro", zero_division=0)
        f1_per_fold.append(float(f1))
        reports_per_fold.append(classification_report(
            y_arr[test_idx], preds, output_dict=True, zero_division=0))

    # Macro-F1 por clase: promedio de los recall/precision/f1 entre folds
    per_class: dict[str, dict[str, float]] = {}
    classes = sorted({c for rep in reports_per_fold for c in rep
                      if c not in {"accuracy", "macro avg", "weighted avg"}})
    for c in classes:
        f1s = [rep.get(c, {}).get("f1-score", 0.0) for rep in reports_per_fold]
        precs = [rep.get(c, {}).get("precision", 0.0) for rep in reports_per_fold]
        recs = [rep.get(c, {}).get("recall", 0.0) for rep in reports_per_fold]
        supports = [rep.get(c, {}).get("support", 0) for rep in reports_per_fold]
        per_class[c] = {
            "f1_mean": float(np.mean(f1s)),
            "precision_mean": float(np.mean(precs)),
            "recall_mean": float(np.mean(recs)),
            "support_mean": float(np.mean(supports)),
        }

    return {
        "macro_f1_mean": float(np.mean(f1_per_fold)),
        "macro_f1_std": float(np.std(f1_per_fold)),
        "per_fold_macro_f1": f1_per_fold,
        "per_class": per_class,
    }


def _measure_latency_legacy(n_iters: int = 500) -> dict[str, float]:
    """Carga el modelo legacy en producción y mide latencias por predicción."""
    clf = FinancialClassifier.load(MODEL)
    sample = "Compra en supermercado Mercadona"
    for _ in range(20):
        clf.predict([sample])  # warmup
    times_ms: list[float] = []
    for _ in range(n_iters):
        t0 = time.perf_counter()
        clf.predict([sample])
        times_ms.append((time.perf_counter() - t0) * 1000)
    return _latency_stats(times_ms, n_iters)


def _measure_latency_hybrid(n_iters: int = 200) -> dict[str, float]:
    """Mide latencia del HybridClassifier en modo zero-shot (cold-start).

    Menos iteraciones porque cada predicción incluye un forward del
    transformer (~30 ms en CPU) y queremos mantener el script rápido.
    """
    import uuid as _uuid

    from src.agents.registrar.classifier_hybrid import HybridClassifier

    hybrid = HybridClassifier.shared()
    sample = "Compra en supermercado Mercadona"
    fresh = str(_uuid.uuid4())
    for _ in range(5):
        hybrid.predict(fresh, sample)  # warmup + carga modelo

    times_ms: list[float] = []
    for _ in range(n_iters):
        t0 = time.perf_counter()
        hybrid.predict(fresh, sample)
        times_ms.append((time.perf_counter() - t0) * 1000)
    return _latency_stats(times_ms, n_iters)


def _latency_stats(times_ms: list[float], n_iters: int) -> dict[str, float]:
    times_arr = np.array(times_ms)
    return {
        "p50_ms": float(np.percentile(times_arr, 50)),
        "p95_ms": float(np.percentile(times_arr, 95)),
        "p99_ms": float(np.percentile(times_arr, 99)),
        "mean_ms": float(np.mean(times_arr)),
        "n_iters": n_iters,
    }


def _legacy_predict_batch(descriptions: list[str]) -> list[str]:
    clf = FinancialClassifier.load(MODEL)
    return clf.predict(descriptions).tolist()


def _zero_shot_predict_batch(descriptions: list[str]) -> list[str]:
    """Predicciones zero-shot con el HybridClassifier en modo cold-start.

    Cualquier user_id nuevo (history_size=0) activa la rama zero-shot del
    híbrido. Esto aísla la calidad semántica del transformer del SGD
    personal.
    """
    import uuid as _uuid

    from src.agents.registrar.classifier_hybrid import HybridClassifier

    hybrid = HybridClassifier.shared()
    fresh_user = str(_uuid.uuid4())
    return [hybrid.predict(fresh_user, desc).area[0] for desc in descriptions]


def _personal_predict_batch(descriptions: list[str], bootstrap_size: int = 30,
                            seed: int = 42) -> list[str]:
    """Simula un usuario con historial: bootstrappea el personal sobre
    `bootstrap_size` muestras aleatorias del CSV (no en la suite cold-start)
    y luego predice cada descripción.

    Permite medir si el modelo personal NO degrada respecto a zero-shot
    cuando hay historial.
    """
    import uuid as _uuid
    import numpy as _np

    from src.agents.registrar.classifier_hybrid import HybridClassifier

    hybrid = HybridClassifier.shared()
    fresh_user = str(_uuid.uuid4())

    X, y = _load_dataset()
    rng = _np.random.default_rng(seed)
    idx = rng.choice(len(X), size=min(bootstrap_size, len(X)), replace=False)
    for i in idx:
        hybrid.record_confirmed(fresh_user, X[i], y[i])

    return [hybrid.predict(fresh_user, desc).area[0] for desc in descriptions]


def _cold_start_evaluation(mode: str) -> dict[str, Any]:
    """Mide el clasificador sobre la suite cold-start versionada.

    Modos disponibles:
      - 'legacy':    `FinancialClassifier` original (TF-IDF + SGDClassifier)
      - 'zero_shot': HybridClassifier en cold-start (transformer + centroides)
      - 'personal':  HybridClassifier con 30 muestras de bootstrap del CSV
    """
    samples = _load_cold_start_suite()
    descriptions = [s["description"] for s in samples]
    expected = [s["expected"] for s in samples]
    langs = [s.get("lang", "es") for s in samples]

    if mode == "legacy":
        preds = _legacy_predict_batch(descriptions)
    elif mode == "zero_shot":
        preds = _zero_shot_predict_batch(descriptions)
    elif mode == "personal":
        preds = _personal_predict_batch(descriptions)
    else:
        raise ValueError(f"Modo desconocido: {mode}")

    correct = sum(1 for p, e in zip(preds, expected) if p == e)
    n = len(samples)
    pred_other = sum(1 for p in preds if p == "Other")

    # Slice por clase
    classes = sorted(set(expected))
    per_class: dict[str, dict[str, float]] = {}
    for c in classes:
        idx = [i for i, e in enumerate(expected) if e == c]
        c_correct = sum(1 for i in idx if preds[i] == c)
        per_class[c] = {
            "n": len(idx),
            "accuracy": c_correct / len(idx) if idx else 0.0,
        }

    # Slice por idioma
    per_lang: dict[str, dict[str, float]] = {}
    for lang in sorted(set(langs)):
        idx = [i for i, l in enumerate(langs) if l == lang]
        l_correct = sum(1 for i in idx if preds[i] == expected[i])
        per_lang[lang] = {
            "n": len(idx),
            "accuracy": l_correct / len(idx) if idx else 0.0,
        }

    return {
        "samples": n,
        "accuracy": correct / n if n else 0.0,
        "rate_pred_other": pred_other / n if n else 0.0,
        "per_class": per_class,
        "per_lang": per_lang,
        "examples": [
            {"description": d, "expected": e, "predicted": p, "lang": l}
            for d, e, p, l in zip(descriptions, expected, preds, langs)
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["baseline", "post"], default="baseline")
    parser.add_argument("--folds", type=int, default=5)
    args = parser.parse_args()

    is_post = args.mode == "post"
    print(f"Evaluating P2 classifier ({args.mode})…")
    X, y = _load_dataset()
    print(f"  dataset: {len(X)} samples, {len(set(y))} classes")

    # K-fold sobre el CSV — siempre con LEGACY (es el guardarraíl: el
    # post no debe degradarlo). Mostramos los dos números si --mode post.
    print("  [1/4] stratified k-fold con LEGACY (TF-IDF + SGD)…")
    kfold_stats = _stratified_kfold_macro_f1(X, y, n_splits=args.folds)
    print(f"        macro-F1 = {kfold_stats['macro_f1_mean']:.4f} "
          f"± {kfold_stats['macro_f1_std']:.4f}")

    print("  [2/4] latencia LEGACY (500 iters)…")
    latency_legacy = _measure_latency_legacy()
    print(f"        p50={latency_legacy['p50_ms']:.2f} ms · "
          f"p95={latency_legacy['p95_ms']:.2f} ms")

    latency_hybrid: dict | None = None
    if is_post:
        print("  [3/4] latencia HÍBRIDO (200 iters; primera carga descarga el transformer)…")
        latency_hybrid = _measure_latency_hybrid()
        print(f"        p50={latency_hybrid['p50_ms']:.2f} ms · "
              f"p95={latency_hybrid['p95_ms']:.2f} ms")
    else:
        print("  [3/4] latencia HÍBRIDO: omitida (sólo en --mode post)")

    print("  [4/4] cold-start con descripciones sintéticas…")
    cold_mode_default = "zero_shot" if is_post else "legacy"
    cold_stats = _cold_start_evaluation(cold_mode_default)
    print(f"        cold-start ({cold_mode_default}) "
          f"accuracy = {cold_stats['accuracy']:.2%} · "
          f"rate_pred_other = {cold_stats['rate_pred_other']:.2%}")

    # En modo post además medimos el rendimiento PERSONAL (con bootstrap
    # simulado desde el CSV) sobre la misma suite — sirve para verificar
    # que el modelo personal no degrada respecto al zero-shot.
    personal_stats = None
    if is_post:
        print("        midiendo también modo PERSONAL (bootstrap=30 desde CSV)…")
        personal_stats = _cold_start_evaluation("personal")
        print(f"        cold-start (personal) "
              f"accuracy = {personal_stats['accuracy']:.2%}")

    out = {
        "mode": args.mode,
        "model_used": str(MODEL.relative_to(ROOT)),
        "dataset": str(DATASET.relative_to(ROOT)),
        "n_samples": len(X),
        "n_classes": len(set(y)),
        "classes": sorted(set(y)),
        "kfold_legacy": kfold_stats,
        "latency_legacy": latency_legacy,
        "latency_hybrid": latency_hybrid,
        "cold_start_primary": cold_stats,
        "cold_start_primary_mode": cold_mode_default,
        "cold_start_personal": personal_stats,
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"p2_{args.mode}.json"
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nResults written to {out_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

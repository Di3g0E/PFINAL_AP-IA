"""Baseline / post-evolución de la pipeline biométrica (P5).

Mide FAR / FRR / EER aproximado sobre una suite anotada de imágenes
reales y spoofs (foto impresa, pantalla, etc.). Para que la comparación
pre/post sea legítima, el MISMO conjunto se usa antes y después de la
evolución (vídeo + anti-spoofing escalonado).

Formato de entrada — CSV en `data/eval/p5_biometrics/labels.csv`:

    path,kind,user_label
    real/diego_01.jpg,real,diego
    real/diego_02.jpg,real,diego
    real/sofia_01.jpg,real,sofia
    spoof_print/diego_print_01.jpg,spoof_print,diego
    spoof_screen/diego_screen_01.jpg,spoof_screen,diego
    ...

Las `path` son relativas a `data/eval/p5_biometrics/images/`.
`kind ∈ {real, spoof_print, spoof_screen, spoof_video}`.
`user_label` es la identidad esperada (debe coincidir con un fichero
de referencia en `models/embeddings/<user>.npy` o se ignora si no
existe).

La métrica:
  - FAR (False Accept Rate) = proporción de spoofs que el sistema acepta
  - FRR (False Reject Rate) = proporción de reales rechazados
  - EER = punto en el barrido de umbrales donde FAR ≈ FRR

Modos:
  --mode baseline   guarda métricas en docs/results/p5_baseline.json
  --mode post       guarda métricas en docs/results/p5_post.json
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
LABELS_CSV = ROOT / "data" / "eval" / "p5_biometrics" / "labels.csv"
IMAGES_DIR = ROOT / "data" / "eval" / "p5_biometrics" / "images"
RESULTS_DIR = ROOT / "docs" / "results"


def _load_labels() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with LABELS_CSV.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows.extend(reader)
    return rows


def _evaluate(rows: list[dict[str, str]],
              sim_threshold: float = 0.60,
              liveness_threshold: float = 0.50,
              antispoof_threshold: float = 0.55) -> dict[str, Any]:
    """Ejecuta la pipeline biométrica sobre cada imagen."""
    from src.agents.security.biometrics import BiometricPipeline, decode_image_bytes
    
    # Aseguramos que los flags están activos en config para probar antispoof
    try:
        from src.utils.config import settings
        settings.security_antispoof_enabled = True
        settings.liveness_threshold = liveness_threshold
        settings.security_antispoof_threshold = antispoof_threshold
    except Exception:
        pass
        
    pipeline = BiometricPipeline.shared()

    # 1. Obtener la imagen de referencia (primer 'real' con user_label=1)
    ref_row = next((r for r in rows if r["kind"] == "real" and r["user_label"] == "1"), None)
    if not ref_row:
        raise ValueError("No se encontró ninguna imagen real con user_label=1 para usar de referencia.")
    
    print(f"  Usando {ref_row['path']} como embedding de referencia (Enrollment).")
    ref_path = IMAGES_DIR / ref_row["path"]
    with ref_path.open("rb") as fh:
        ref_data = fh.read()
    ref_img = decode_image_bytes(ref_data)
    ref_features = pipeline.extract(ref_img)
    ref_embedding = ref_features.embedding

    # 2. Evaluar todas las imágenes
    results: list[dict[str, Any]] = []
    for row in rows:
        path = IMAGES_DIR / row["path"]
        if not path.is_file():
            results.append({**row, "error": "missing_file"})
            continue
        t0 = time.perf_counter()
        try:
            with path.open("rb") as fh:
                data = fh.read()
            img = decode_image_bytes(data)
            features = pipeline.extract(img)
            
            sim = pipeline.cosine_similarity(ref_embedding, features.embedding)
            liveness = features.liveness_score
            antispoof = features.antispoof_score
            challenge = features.challenge_passed
            
            accept = (sim >= sim_threshold) and (liveness >= liveness_threshold) and (antispoof >= antispoof_threshold) and challenge
            
            results.append({
                **row,
                "similarity": float(sim),
                "liveness": float(liveness),
                "antispoof": float(antispoof),
                "challenge": challenge,
                "accept": accept,
                "latency_ms": (time.perf_counter() - t0) * 1000,
            })
        except Exception as e:
            results.append({**row, "error": f"{type(e).__name__}: {e}"})

    # Métricas agregadas
    # FRR: Reales genuinos (user_label=1) rechazados
    real_gen = [r for r in results if r.get("kind") == "real" and r.get("user_label") == "1" and "accept" in r]
    frr = sum(1 for r in real_gen if not r["accept"]) / len(real_gen) if real_gen else 0.0
    
    # FAR: Impostores (reales user_label=0) o ataques (spoof_screen) aceptados
    impostors = [r for r in results if (r.get("kind") == "real" and r.get("user_label") == "0") or r.get("kind", "").startswith("spoof")]
    impostors = [r for r in impostors if "accept" in r]
    far = sum(1 for r in impostors if r["accept"]) / len(impostors) if impostors else 0.0

    return {
        "n_real_gen": len(real_gen),
        "n_impostors": len(impostors),
        "sim_threshold": sim_threshold,
        "liveness_threshold": liveness_threshold,
        "antispoof_threshold": antispoof_threshold,
        "far": far,
        "frr": frr,
        "per_sample": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["baseline", "post"], default="baseline")
    args = parser.parse_args()

    if not LABELS_CSV.is_file():
        print(f"[ERROR] no encuentro {LABELS_CSV.relative_to(ROOT)}")
        print("\nProtocolo de recopilación:")
        print("  1. Crear data/eval/p5_biometrics/images/{real,spoof_print,spoof_screen}/")
        print("  2. Recopilar mínimo: 15 fotos reales + 10 impresas + 10 de pantalla")
        print("  3. Crear labels.csv con columnas: path,kind,user_label")
        return 1

    print(f"Evaluating P5 biometrics ({args.mode})…")
    rows = _load_labels()
    print(f"  labels: {len(rows)} entries")

    stats = _evaluate(rows)
    print(f"  FAR = {stats['far']:.2%} · FRR = {stats['frr']:.2%}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"p5_{args.mode}.json"
    out_path.write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nResults written to {out_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

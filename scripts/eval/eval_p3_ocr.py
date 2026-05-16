"""Baseline / post-evolución del extractor de total (P3).

Mide el componente de scoring (Gradient Boosting) del OCR sobre el set
de test de CORD ya OCR'd (JSONL con texto + label). NO requiere
PaddleOCR para correr el baseline — opera directamente sobre el texto
OCR, lo cual aísla la calidad del scoring del extractor.

Esto da una **baseline de la distribución de entrenamiento (KRW)**.
Para el target EUR habrá que preparar una suite manualmente
anotada en `data/eval/eur_invoices/` con un JSONL paralelo.

Modos:
  --mode baseline   guarda métricas en docs/results/p3_baseline.json
  --mode post       guarda métricas en docs/results/p3_post.json

Set por defecto:
  --set cord        usa ../P3_AP-IA/data/processed/cord_test_data.jsonl
  --set eur         usa data/eval/eur_invoices/test.jsonl (pendiente crear)
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import numpy as np

from src.agents.registrar.ocr_engine import OCRTotalExtractor, normalize_amount
from src.agents.registrar.ocr_engine_eur import EnrichedOCRExtractor

ROOT = Path(__file__).resolve().parents[2]
CORD_JSONL = ROOT.parent / "P3_AP-IA" / "data" / "processed" / "cord_test_data.jsonl"
EUR_JSONL = ROOT / "data" / "eval" / "eur_invoices" / "test.jsonl"
RESULTS_DIR = ROOT / "docs" / "results"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _normalize_label(label: str) -> float | None:
    """El label del CORD es un string como '60.000' (KRW: punto = miles).

    Lo normalizamos con la misma función que aplica el OCR a sus
    candidatos para comparar manzanas con manzanas.
    """
    return normalize_amount(str(label))


def _evaluate_set(rows: list[dict[str, Any]], extractor,
                  enriched: bool, tolerance: float = 0.01) -> dict[str, Any]:
    """Mide accuracy (exact match con tolerancia) + tipos de error.

    Categorías de resultado:
      - hit:      predicción dentro de la tolerancia del label
      - miss:     predicción fuera de la tolerancia
      - no_pred:  el extractor no encontró candidato (devolvió None)
      - bad_label: el JSONL trae un label que no se puede parsear

    Si `enriched=True`, además cuenta la cobertura de cada campo
    (fecha, nif, comercio, iva) sobre los registros del JSONL que
    traen esos atributos en el ground-truth (los sintéticos sí).
    """
    counts = {"hit": 0, "miss": 0, "no_pred": 0, "bad_label": 0}
    errors: list[dict[str, Any]] = []
    latencies_ms: list[float] = []
    field_stats = {
        "fecha": {"present_in_gt": 0, "extracted": 0, "correct": 0},
        "nif": {"present_in_gt": 0, "extracted": 0, "correct": 0},
        "comercio": {"present_in_gt": 0, "extracted": 0, "correct": 0},
        "iva": {"present_in_gt": 0, "extracted": 0},
    }

    for row in rows:
        text = row.get("text", "")
        raw_label = row.get("label", "")
        target = _normalize_label(raw_label)
        if target is None:
            counts["bad_label"] += 1
            continue

        t0 = time.perf_counter()
        if enriched:
            result = extractor.extract_all_from_text(text)
            pred = result["total"]
            extracted_date = result.get("date")
            md = result.get("metadata")
        else:
            pred = extractor.extract_total_from_text(text)
            extracted_date = None
            md = None
        latencies_ms.append((time.perf_counter() - t0) * 1000)

        if pred is None:
            counts["no_pred"] += 1
            errors.append({"text_snippet": text[:120], "target": target, "pred": None})
        elif abs(pred - target) <= tolerance * max(abs(target), 1.0):
            counts["hit"] += 1
        else:
            counts["miss"] += 1
            errors.append({"text_snippet": text[:120], "target": target, "pred": pred})

        # Cobertura por campo (sólo si modo enriched + GT disponible)
        if enriched and md is not None:
            gt_date = row.get("date")
            if gt_date:
                field_stats["fecha"]["present_in_gt"] += 1
                if extracted_date is not None:
                    field_stats["fecha"]["extracted"] += 1
                    # GT format from generator: "01/12/2025" or "01-12-2025"
                    import re as _re
                    parts = _re.split(r"[/.\-]", gt_date)
                    if len(parts) == 3:
                        try:
                            d, m, y = int(parts[0]), int(parts[1]), int(parts[2])
                            if (extracted_date.day == d
                                    and extracted_date.month == m
                                    and extracted_date.year == y):
                                field_stats["fecha"]["correct"] += 1
                        except ValueError:
                            pass
            gt_nif = row.get("nif")
            if gt_nif:
                field_stats["nif"]["present_in_gt"] += 1
                if md.nif:
                    field_stats["nif"]["extracted"] += 1
                    if md.nif.upper() == gt_nif.upper():
                        field_stats["nif"]["correct"] += 1
            gt_merchant = row.get("merchant")
            if gt_merchant:
                field_stats["comercio"]["present_in_gt"] += 1
                if md.merchant:
                    field_stats["comercio"]["extracted"] += 1
                    # Match laxo: subcadena suficiente (OCR a veces añade ruido)
                    if (md.merchant.lower()[:10] in gt_merchant.lower()
                            or gt_merchant.lower()[:10] in md.merchant.lower()):
                        field_stats["comercio"]["correct"] += 1
            if row.get("iva_pct") is not None:
                field_stats["iva"]["present_in_gt"] += 1
                if md.iva_pct is not None:
                    field_stats["iva"]["extracted"] += 1

    n_valid = counts["hit"] + counts["miss"] + counts["no_pred"]
    accuracy = counts["hit"] / n_valid if n_valid else 0.0

    lat_arr = np.array(latencies_ms) if latencies_ms else np.array([0.0])
    out = {
        "n_total": len(rows),
        "n_valid": n_valid,
        "counts": counts,
        "accuracy": accuracy,
        "latency": {
            "p50_ms": float(np.percentile(lat_arr, 50)),
            "p95_ms": float(np.percentile(lat_arr, 95)),
            "mean_ms": float(np.mean(lat_arr)),
        },
        "error_examples": errors[:10],
    }
    if enriched:
        out["field_coverage"] = field_stats
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["baseline", "post"], default="baseline")
    parser.add_argument("--set", choices=["cord", "eur"], default="cord")
    parser.add_argument(
        "--engine", choices=["legacy", "eur"], default=None,
        help="Por defecto: 'legacy' en baseline, 'eur' en post.",
    )
    args = parser.parse_args()
    engine = args.engine or ("eur" if args.mode == "post" else "legacy")

    path = CORD_JSONL if args.set == "cord" else EUR_JSONL
    if not path.is_file():
        print(f"[ERROR] dataset no encontrado: {path}")
        print("  -> Para el set 'eur' hay que crear data/eval/eur_invoices/test.jsonl")
        print("     con lineas {'text': '<ocr-text>', 'label': '<total>', 'image_id': int}")
        return 1

    print(f"Evaluating P3 OCR ({args.mode}, set={args.set}, engine={engine})...")
    rows = _load_jsonl(path)
    print(f"  dataset: {len(rows)} samples from {path.name}")

    if engine == "eur":
        extractor = EnrichedOCRExtractor.shared()
        enriched = True
        if extractor._eur_clf is None:
            print("  [WARN] modelo EUR no cargado, cayendo a legacy GB")
        model_used = "models/ocr_total_extractor_eur.joblib"
    else:
        extractor = OCRTotalExtractor.shared()
        enriched = False
        if extractor._gb_clf is None:
            print("  [WARN] modelo legacy no cargado, se usara fallback regex")
        model_used = "models/ocr_total_extractor.joblib"

    stats = _evaluate_set(rows, extractor, enriched=enriched)
    print(f"  accuracy = {stats['accuracy']:.2%} "
          f"(hit={stats['counts']['hit']}, miss={stats['counts']['miss']}, "
          f"no_pred={stats['counts']['no_pred']})")
    print(f"  latency p50={stats['latency']['p50_ms']:.2f} ms . "
          f"p95={stats['latency']['p95_ms']:.2f} ms")

    if enriched and "field_coverage" in stats:
        print("  Cobertura por campo (extraido / GT, correcto / GT):")
        for field, c in stats["field_coverage"].items():
            gt = c["present_in_gt"]
            ext = c["extracted"]
            corr = c.get("correct", ext)
            if gt:
                print(f"    {field:9s}  GT={gt:3d}  extraido={ext:3d} "
                      f"({ext/gt:.0%})  correcto={corr:3d} ({corr/gt:.0%})")

    out = {
        "mode": args.mode,
        "set": args.set,
        "engine": engine,
        "dataset_path": str(path),
        "model_used": model_used,
        **stats,
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"p3_{args.set}_{args.mode}.json"
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str),
                        encoding="utf-8")
    print(f"\nResults written to {out_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

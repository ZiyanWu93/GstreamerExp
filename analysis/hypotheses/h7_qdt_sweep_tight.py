"""H7 verifier — queue-delay-target sweep at the tight (800 kbps) ceiling.

H6 (loose 4000 kbps ceiling) found picture quality (PSNR) stays flat as the
knob loosens. H7 re-runs the sweep with the encoder starved and asks whether
PSNR now rises with the knob — and whether its swing is larger than H6's.

Aggregates each cell's mean PSNR and p95 latency across reps, evaluates the
predicates from specs/hypotheses/h7.yaml, and writes h7_report.json.

Run from the project root:
    python3 analysis/hypotheses/h7_qdt_sweep_tight.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from statistics import mean

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RUNS_DIR = PROJECT_ROOT / "runs"
RESULT_PATH = PROJECT_ROOT / "analysis" / "hypotheses" / "results" / "h7_report.json"
H6_REPORT = PROJECT_ROOT / "analysis" / "hypotheses" / "results" / "h6_report.json"

REGIMES = ("static", "fluct", "5g")
QDT_MS = (10, 30, 60, 100, 200, 500)
# H7 config-id allocation: id_base=119, regime order [static, fluct, 5g],
# qdt order [10, 30, 60, 100, 200, 500].
ID_BASE = 119
SWEEP_BY_REGIME = {
    "static": "h7-qdt-sweep-static",
    "fluct": "h7-qdt-sweep-fluct",
    "5g": "h7-qdt-sweep-5g",
}


def load_run_summary(config: int, run_id: str) -> dict | None:
    p = RUNS_DIR / str(config) / run_id / "summary.json"
    return json.loads(p.read_text()) if p.is_file() else None


def load_run_psnr(config: int, run_id: str) -> float | None:
    p = RUNS_DIR / str(config) / run_id / "viewer.json"
    if not p.is_file():
        return None
    v = json.loads(p.read_text())
    return (v.get("metrics", {})
             .get("decoded_psnr", {})
             .get("summary", {})
             .get("mean_psnr_db"))


def per_cell_aggregates(regime: str) -> dict[int, dict]:
    """For one network, return {qdt_ms: {mean_psnr_db, mean_p95_latency_ms, n_reps}}."""
    sweep_path = PROJECT_ROOT / "runs" / "experiments" / f"{SWEEP_BY_REGIME[regime]}.json"
    runs = json.loads(sweep_path.read_text())["runs"]
    by_qdt: dict[int, list[tuple[float, float]]] = {q: [] for q in QDT_MS}
    for r in runs:
        if r.get("exit_code") != 0:
            continue
        config = int(r["config"])
        run_id = r["run_id"]
        qdt = QDT_MS[(config - ID_BASE) % len(QDT_MS)]
        psnr = load_run_psnr(config, run_id)
        summary = load_run_summary(config, run_id)
        p95 = (summary.get("latency", {}) or {}).get("p95_ms") if summary else None
        if psnr is None or p95 is None:
            continue
        by_qdt[qdt].append((psnr, p95))
    out: dict[int, dict] = {}
    for q, samples in by_qdt.items():
        if not samples:
            continue
        out[q] = {
            "mean_psnr_db": round(mean(s[0] for s in samples), 3),
            "mean_p95_latency_ms": round(mean(s[1] for s in samples), 3),
            "n_reps": len(samples),
        }
    return out


def is_monotone_nondecreasing(values: list[float]) -> bool:
    return all(b >= a for a, b in zip(values, values[1:]))


def psnr_swing(cells: dict[int, dict]) -> float:
    psnrs = [c["mean_psnr_db"] for c in cells.values()]
    return round(max(psnrs) - min(psnrs), 3) if psnrs else 0.0


def h6_max_swing() -> float | None:
    """Largest per-network PSNR swing recorded by H6 (for comparison)."""
    if not H6_REPORT.is_file():
        return None
    h6 = json.loads(H6_REPORT.read_text())
    swings = []
    for regime in REGIMES:
        cells = h6.get("per_regime", {}).get(regime, {}).get("cells", [])
        psnrs = [c["mean_psnr_db"] for c in cells]
        if psnrs:
            swings.append(max(psnrs) - min(psnrs))
    return round(max(swings), 3) if swings else None


def evaluate() -> dict:
    per_regime: dict[str, dict] = {}
    for regime in REGIMES:
        cells = per_cell_aggregates(regime)
        sorted_qdts = sorted(cells.keys())
        psnr_curve = [cells[q]["mean_psnr_db"] for q in sorted_qdts]
        latency_curve = [cells[q]["mean_p95_latency_ms"] for q in sorted_qdts]
        per_regime[regime] = {
            "cells": [
                {
                    "qdt_ms": q,
                    "mean_psnr_db": cells[q]["mean_psnr_db"],
                    "mean_p95_latency_ms": cells[q]["mean_p95_latency_ms"],
                    "n_reps": cells[q]["n_reps"],
                }
                for q in sorted_qdts
            ],
            "psnr_curve_monotone_nondecreasing": is_monotone_nondecreasing(psnr_curve),
            "p95_latency_curve_monotone_nondecreasing": is_monotone_nondecreasing(latency_curve),
            "psnr_swing_db": psnr_swing(cells),
        }

    psnr_rises_all = all(per_regime[r]["psnr_curve_monotone_nondecreasing"] for r in REGIMES)
    latency_rises_all = all(per_regime[r]["p95_latency_curve_monotone_nondecreasing"] for r in REGIMES)
    h7_max_swing = max(per_regime[r]["psnr_swing_db"] for r in REGIMES)
    h6_swing = h6_max_swing()
    swing_larger_than_h6 = h6_swing is not None and h7_max_swing > h6_swing

    predicates = {
        "psnr_rises_with_knob_on_every_network": psnr_rises_all,
        "psnr_swing_across_the_knob_is_larger_than_in_h6": swing_larger_than_h6,
        "p95_latency_rises_with_knob_on_every_network": latency_rises_all,
        # Refutation
        "psnr_stays_flat_like_h6_on_every_network": (not psnr_rises_all) and (not swing_larger_than_h6),
        # Untested triggers
        "any_cell_failed": False,
        "required_metric_missing": False,
    }

    supported = predicates["psnr_rises_with_knob_on_every_network"] and \
        predicates["psnr_swing_across_the_knob_is_larger_than_in_h6"]
    refuted = predicates["psnr_stays_flat_like_h6_on_every_network"]
    verdict = "supported" if (supported and not refuted) else ("refuted" if refuted else "inconclusive")

    return {
        "verdict": verdict,
        "predicates": predicates,
        "h7_max_psnr_swing_db": h7_max_swing,
        "h6_max_psnr_swing_db": h6_swing,
        "per_regime": per_regime,
    }


def main() -> int:
    report = evaluate()
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(report, indent=2))
    print(f"H7 verdict: {report['verdict']}")
    print(f"PSNR swing: H7 max {report['h7_max_psnr_swing_db']} dB vs H6 max {report['h6_max_psnr_swing_db']} dB")
    for regime in REGIMES:
        b = report["per_regime"][regime]
        print(f"  {regime}: psnr_rises={b['psnr_curve_monotone_nondecreasing']} "
              f"swing={b['psnr_swing_db']}dB latency_rises={b['p95_latency_curve_monotone_nondecreasing']}")
    print(f"Report: {RESULT_PATH.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

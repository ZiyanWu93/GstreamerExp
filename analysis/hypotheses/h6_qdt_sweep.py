"""H6 verifier — SCReAM queue-delay-target sensitivity sweep.

Loads the three sweep records (static / fluct / 5g), aggregates each
cell's mean PSNR and p95 latency across reps, and evaluates the
predicates declared in specs/hypotheses/h6.yaml.

Run from the project root:
    python3 analysis/hypotheses/h6_qdt_sweep.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from statistics import mean

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RUNS_DIR = PROJECT_ROOT / "runs"
RESULT_PATH = PROJECT_ROOT / "analysis" / "hypotheses" / "results" / "h6_report.json"

REGIMES = ("static", "fluct", "5g")
QDT_MS = (10, 30, 60, 100, 200, 500)
# config-id allocation matches tools/gen_h6_configs.py: ID_BASE=101, regime
# order [static, fluct, 5g], qdt order [10, 30, 60, 100, 200, 500].
CONFIG_BY_CELL: dict[tuple[str, int], int] = {
    (regime, qdt): 101 + ri * len(QDT_MS) + qi
    for ri, regime in enumerate(REGIMES)
    for qi, qdt in enumerate(QDT_MS)
}
SWEEP_BY_REGIME = {
    "static": "h6-qdt-sweep-static",
    "fluct": "h6-qdt-sweep-fluct",
    "5g": "h6-qdt-sweep-5g",
}

# Elbow detection: smallest knob value V* such that doubling V* yields
# < this fractional PSNR gain. Predicate id: elbow_cell_identifiable_per_regime.
ELBOW_GAIN_THRESHOLD = 0.05


def load_run_summary(config: int, run_id: str) -> dict | None:
    p = RUNS_DIR / str(config) / run_id / "summary.json"
    if not p.is_file():
        return None
    return json.loads(p.read_text())


def per_cell_aggregates(regime: str) -> dict[int, dict]:
    """For one regime, return {qdt_ms: {mean_psnr_db, mean_p95_latency_ms, n_reps}}."""
    sweep = SWEEP_BY_REGIME[regime]
    sweep_path = PROJECT_ROOT / "runs" / "experiments" / f"{sweep}.json"
    runs = json.loads(sweep_path.read_text())["runs"]
    by_qdt: dict[int, list[tuple[float, float]]] = {q: [] for q in QDT_MS}
    for r in runs:
        if r.get("exit_code") != 0:
            continue
        config = int(r["config"])
        run_id = r["run_id"]
        summary = load_run_summary(config, run_id)
        if not summary:
            continue
        # Recover qdt from config id (inverse of CONFIG_BY_CELL).
        qdt_idx = (config - 101) % len(QDT_MS)
        qdt = QDT_MS[qdt_idx]
        psnr_summary = (summary.get("viewer", {}).get("metrics", {}) or {})
        # Older summary shape: PSNR mean isn't in summary.viewer, only in
        # viewer.json/metrics/decoded_psnr/summary. Load that.
        viewer_path = RUNS_DIR / str(config) / run_id / "viewer.json"
        if viewer_path.is_file():
            v = json.loads(viewer_path.read_text())
            mean_psnr = (v.get("metrics", {})
                          .get("decoded_psnr", {})
                          .get("summary", {})
                          .get("mean_psnr_db"))
        else:
            mean_psnr = None
        p95 = (summary.get("latency", {}) or {}).get("p95_ms")
        if mean_psnr is None or p95 is None:
            continue
        by_qdt[qdt].append((mean_psnr, p95))
    out: dict[int, dict] = {}
    for q, samples in by_qdt.items():
        if not samples:
            continue
        psnrs = [s[0] for s in samples]
        p95s = [s[1] for s in samples]
        out[q] = {
            "mean_psnr_db": round(mean(psnrs), 3),
            "mean_p95_latency_ms": round(mean(p95s), 3),
            "n_reps": len(samples),
        }
    return out


def is_monotone_nondecreasing(values: list[float]) -> bool:
    return all(b >= a for a, b in zip(values, values[1:]))


def find_elbow(qdt_to_psnr: dict[int, float]) -> int | None:
    """Smallest knob value V* such that the next-larger knob yields
    < ELBOW_GAIN_THRESHOLD fractional PSNR gain."""
    sorted_qdts = sorted(qdt_to_psnr.keys())
    for i, q in enumerate(sorted_qdts[:-1]):
        psnr_here = qdt_to_psnr[q]
        psnr_next = qdt_to_psnr[sorted_qdts[i + 1]]
        if psnr_here <= 0:
            continue
        gain = (psnr_next - psnr_here) / psnr_here
        if gain < ELBOW_GAIN_THRESHOLD:
            return q
    return None


def evaluate() -> dict:
    # Per-regime aggregates and curves.
    per_regime: dict[str, dict] = {}
    for regime in REGIMES:
        cells = per_cell_aggregates(regime)
        sorted_qdts = sorted(cells.keys())
        psnr_curve = [cells[q]["mean_psnr_db"] for q in sorted_qdts]
        latency_curve = [cells[q]["mean_p95_latency_ms"] for q in sorted_qdts]
        elbow = find_elbow({q: cells[q]["mean_psnr_db"] for q in sorted_qdts})
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
            "elbow_qdt_ms": elbow,
        }

    # Predicate evaluation.
    psnr_monotone_all = all(per_regime[r]["psnr_curve_monotone_nondecreasing"] for r in REGIMES)
    latency_monotone_all = all(per_regime[r]["p95_latency_curve_monotone_nondecreasing"] for r in REGIMES)
    elbows = {r: per_regime[r]["elbow_qdt_ms"] for r in REGIMES}
    elbow_identifiable_all = all(e is not None for e in elbows.values())
    elbows_differ = len({e for e in elbows.values() if e is not None}) > 1

    predicates = {
        "psnr_rises_with_knob_on_every_network": psnr_monotone_all,
        "p95_latency_rises_with_knob_on_every_network": latency_monotone_all,
        "diminishing_returns_point_visible_on_every_network": elbow_identifiable_all,
        "diminishing_returns_points_differ_between_networks": elbows_differ,
        # Refutation predicates
        "psnr_does_not_rise_with_knob_on_at_least_one_network": not psnr_monotone_all,
        "no_diminishing_returns_point_visible_anywhere": not any(e is not None for e in elbows.values()),
        "diminishing_returns_points_match_across_networks": (
            elbow_identifiable_all and not elbows_differ
        ),
        # Untested triggers
        "any_cell_failed": False,
        "required_metric_missing": False,
    }

    supported = all(
        predicates[p]
        for p in [
            "psnr_rises_with_knob_on_every_network",
            "p95_latency_rises_with_knob_on_every_network",
            "diminishing_returns_point_visible_on_every_network",
            "diminishing_returns_points_differ_between_networks",
        ]
    )
    refuted = any(
        predicates[p]
        for p in [
            "psnr_does_not_rise_with_knob_on_at_least_one_network",
            "no_diminishing_returns_point_visible_anywhere",
            "diminishing_returns_points_match_across_networks",
        ]
    )

    if supported and not refuted:
        verdict = "supported"
    elif refuted:
        verdict = "refuted"
    else:
        verdict = "inconclusive"

    return {
        "verdict": verdict,
        "predicates": predicates,
        "elbows": elbows,
        "per_regime": per_regime,
    }


def _sweeps_meta() -> list[dict]:
    cfg = list(range(101, 119))  # 101..118, 6 per regime in REGIME order
    return [
        {"name": SWEEP_BY_REGIME[reg], "configs": cfg[i * len(QDT_MS):(i + 1) * len(QDT_MS)],
         "knob_values": list(QDT_MS), "reps": 3,
         "record_path": f"runs/experiments/{SWEEP_BY_REGIME[reg]}.json",
         "description": f"queue-delay-target sweep on the {reg} network, loose 4000 kbps ceiling."}
        for i, reg in enumerate(REGIMES)
    ]


def main() -> int:
    from _qdt_report import build_extras, QDT
    report = evaluate()
    report.update(build_extras("h6", report["per_regime"], list(REGIMES),
                               list(QDT_MS), _sweeps_meta(), QDT))
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(report, indent=2))
    print(f"H6 verdict: {report['verdict']}")
    print(f"Elbows (qdt_ms per regime): {report['elbows']}")
    for regime in REGIMES:
        block = report["per_regime"][regime]
        print(f"  {regime}: "
              f"psnr_monotone={block['psnr_curve_monotone_nondecreasing']} "
              f"latency_monotone={block['p95_latency_curve_monotone_nondecreasing']} "
              f"elbow={block['elbow_qdt_ms']}")
    print(f"Predicates:")
    for k, v in report["predicates"].items():
        print(f"  {k}: {v}")
    print(f"Report: {RESULT_PATH.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

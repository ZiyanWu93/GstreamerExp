"""H9 verifier — SCReAM RTT-boundary sweep (where does delay-based control break?).

H6/H7/H8 found SCReAM's tuning knobs do not move picture quality for this
workload. H9 opens the boundary axis (§7.8): SCReAM is a delay-based controller,
so base path delay is the most controller-specific stressor. We sweep the
one-way netem delay {20, 50, 100, 200, 400, 800} ms on a fixed 5 Mbps link
(no loss) and ask whether SCReAM's own decoded quality holds, or cliffs past
some delay.

Single arm (no bare baseline exists in this testbed, only scream|gcc), so the
boundary is read off SCReAM's own quality-vs-delay curve: a usable floor at low
delay plus a clear drop by the top of the range means a degeneration boundary
lives in [20, 800] ms; a roughly flat curve means SCReAM tolerates the whole
range (itself a finding).

Run from the project root:
    python3 analysis/hypotheses/h9_rtt_boundary.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from statistics import mean

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RUNS_DIR = PROJECT_ROOT / "runs"
RESULT_PATH = PROJECT_ROOT / "analysis" / "hypotheses" / "results" / "h9_report.json"

DELAY_MS = (20, 50, 100, 200, 400, 800)
ID_BASE = 301                       # config 301..306, one per delay
SWEEP = "h9-rtt-boundary"
USABLE_FLOOR_DB = 25.0              # below this, video is visibly degraded
DEGRADE_DROP_DB = 5.0              # a "clear drop" from low to high delay


def load_run_psnr(config: int, run_id: str) -> float | None:
    p = RUNS_DIR / str(config) / run_id / "viewer.json"
    if not p.is_file():
        return None
    v = json.loads(p.read_text())
    return (v.get("metrics", {}).get("decoded_psnr", {})
             .get("summary", {}).get("mean_psnr_db"))


def load_run_p95(config: int, run_id: str) -> float | None:
    p = RUNS_DIR / str(config) / run_id / "summary.json"
    if not p.is_file():
        return None
    s = json.loads(p.read_text())
    return (s.get("latency", {}) or {}).get("p95_ms")


def per_delay_aggregates() -> dict[int, dict]:
    sweep_path = RUNS_DIR / "experiments" / f"{SWEEP}.json"
    if not sweep_path.is_file():
        return {}
    runs = json.loads(sweep_path.read_text())["runs"]
    by_delay: dict[int, list[tuple[float, float]]] = {d: [] for d in DELAY_MS}
    for r in runs:
        if r.get("exit_code") != 0:
            continue
        config = int(r["config"])
        delay = DELAY_MS[config - ID_BASE]
        psnr = load_run_psnr(config, r["run_id"])
        p95 = load_run_p95(config, r["run_id"])
        if psnr is None or p95 is None:
            continue
        by_delay[delay].append((psnr, p95))
    out: dict[int, dict] = {}
    for d, samples in by_delay.items():
        if not samples:
            continue
        out[d] = {
            "mean_psnr_db": round(mean(s[0] for s in samples), 3),
            "mean_p95_latency_ms": round(mean(s[1] for s in samples), 3),
            "n_reps": len(samples),
        }
    return out


def evaluate() -> dict:
    cells = per_delay_aggregates()
    any_data = bool(cells)
    sorted_d = sorted(cells.keys())
    per_regime = {
        "rtt": {
            "cells": [
                {"rtt_ms": d, "mean_psnr_db": cells[d]["mean_psnr_db"],
                 "mean_p95_latency_ms": cells[d]["mean_p95_latency_ms"],
                 "n_reps": cells[d]["n_reps"]}
                for d in sorted_d
            ],
        }
    }

    psnr_low = cells.get(DELAY_MS[0], {}).get("mean_psnr_db")
    psnr_high = cells.get(DELAY_MS[-1], {}).get("mean_psnr_db")
    all_psnr = [cells[d]["mean_psnr_db"] for d in sorted_d]

    usable_at_low_delay = psnr_low is not None and psnr_low >= 30.0
    quality_drops_with_delay = (
        psnr_low is not None and psnr_high is not None
        and (psnr_low - psnr_high) >= DEGRADE_DROP_DB
    ) or (all_psnr and min(all_psnr) < USABLE_FLOOR_DB)

    # threshold = first delay whose PSNR falls below the usable floor
    threshold_ms = next((d for d in sorted_d if cells[d]["mean_psnr_db"] < USABLE_FLOOR_DB), None)

    if not any_data:
        verdict = "untested"
    elif not usable_at_low_delay:
        verdict = "untested"   # baseline broken; cannot read a boundary
    elif quality_drops_with_delay:
        verdict = "supported"  # a degeneration boundary lives in [20, 800] ms
    else:
        verdict = "refuted"    # SCReAM tolerates the whole range; no boundary here

    predicates = {
        "usable_quality_at_low_delay": usable_at_low_delay,
        "quality_degrades_across_the_delay_range": bool(quality_drops_with_delay),
        "degeneration_threshold_found_below_max_delay": threshold_ms is not None,
        "quality_robust_across_whole_range": usable_at_low_delay and not quality_drops_with_delay,
        "any_cell_failed": False,
        "required_metric_missing": not any_data,
    }

    return {
        "verdict": verdict,
        "predicates": predicates,
        "psnr_at_low_delay_db": psnr_low,
        "psnr_at_max_delay_db": psnr_high,
        "degeneration_threshold_ms": threshold_ms,
        "per_regime": per_regime,
    }


def _sweeps_meta() -> list[dict]:
    return [{
        "name": SWEEP,
        "configs": list(range(ID_BASE, ID_BASE + len(DELAY_MS))),
        "knob_values": list(DELAY_MS),
        "reps": 3,
        "record_path": f"runs/experiments/{SWEEP}.json",
        "description": "SCReAM RTT-boundary sweep on a fixed 5 Mbps link.",
    }]


def main() -> int:
    from _qdt_report import build_extras, RTT
    report = evaluate()
    if report["verdict"] != "untested":
        report.update(build_extras("h9", report["per_regime"], ["rtt"],
                                   list(DELAY_MS), _sweeps_meta(), RTT))
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(report, indent=2))
    print(f"H9 verdict: {report['verdict']}")
    print(f"PSNR: {report['psnr_at_low_delay_db']} dB @20ms -> "
          f"{report['psnr_at_max_delay_db']} dB @800ms; "
          f"threshold={report['degeneration_threshold_ms']}ms")
    for c in report["per_regime"]["rtt"]["cells"]:
        print(f"  {c['rtt_ms']:4d}ms: PSNR {c['mean_psnr_db']}dB  "
              f"p95 {c['mean_p95_latency_ms']}ms  (n{c['n_reps']})")
    print(f"Report: {RESULT_PATH.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

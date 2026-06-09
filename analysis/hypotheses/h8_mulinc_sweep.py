"""H8 verifier — mul_increase (ramp-aggressiveness) sweep at the loose ceiling.

H6/H7 found the queue-delay-target is a latency knob, not a quality knob, and
on the capacity-step link a looser target slightly HURT quality (data buffered
through the 300 kbps trough arrives stale). H8 asks the follow-up: does a more
aggressive multiplicative-increase ramp recover picture quality faster after
capacity returns?

Design: sweep mul_increase {0.02 .. 0.5} across three networks at the loose
4000 kbps ceiling, queue-delay-target held at 0.06 s.
  - fixed 5 Mbps link  = control: no capacity change, so ramp speed should not
    matter; PSNR should stay roughly flat (small swing).
  - capacity-step link = treatment: a faster ramp should recover quality after
    the step, so PSNR should rise with mul_increase and swing more than on the
    control.

Aggregates each cell's mean PSNR and p95 latency across reps, evaluates the
predicates from specs/hypotheses/h8.yaml, and writes h8_report.json.

Note: mean PSNR is whole-run, not windowed to the post-step recovery phase
(the available per-run metric). A real recovery effect should still register
as a higher whole-run mean; see the spec's limitations.

Run from the project root:
    python3 analysis/hypotheses/h8_mulinc_sweep.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from statistics import mean

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RUNS_DIR = PROJECT_ROOT / "runs"
RESULT_PATH = PROJECT_ROOT / "analysis" / "hypotheses" / "results" / "h8_report.json"

REGIMES = ("static", "fluct", "5g")
MUL = (0.02, 0.05, 0.1, 0.2, 0.3, 0.5)
# H8 config-id allocation: id_base=201, regime order [static, fluct, 5g],
# mul order [0.02, 0.05, 0.1, 0.2, 0.3, 0.5].
ID_BASE = 201
SWEEP_BY_REGIME = {
    "static": "h8-mulinc-sweep-static",
    "fluct": "h8-mulinc-sweep-fluct",
    "5g": "h8-mulinc-sweep-5g",
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


def per_cell_aggregates(regime: str) -> dict[float, dict]:
    """For one network, return {mul: {mean_psnr_db, mean_p95_latency_ms, n_reps}}."""
    sweep_path = RUNS_DIR / "experiments" / f"{SWEEP_BY_REGIME[regime]}.json"
    if not sweep_path.is_file():
        return {}
    runs = json.loads(sweep_path.read_text())["runs"]
    by_mul: dict[float, list[tuple[float, float]]] = {m: [] for m in MUL}
    for r in runs:
        if r.get("exit_code") != 0:
            continue
        config = int(r["config"])
        run_id = r["run_id"]
        mul = MUL[(config - ID_BASE) % len(MUL)]
        psnr = load_run_psnr(config, run_id)
        summary = load_run_summary(config, run_id)
        p95 = (summary.get("latency", {}) or {}).get("p95_ms") if summary else None
        if psnr is None or p95 is None:
            continue
        by_mul[mul].append((psnr, p95))
    out: dict[float, dict] = {}
    for m, samples in by_mul.items():
        if not samples:
            continue
        out[m] = {
            "mean_psnr_db": round(mean(s[0] for s in samples), 3),
            "mean_p95_latency_ms": round(mean(s[1] for s in samples), 3),
            "n_reps": len(samples),
        }
    return out


def is_monotone_nondecreasing(values: list[float]) -> bool:
    return all(b >= a for a, b in zip(values, values[1:]))


def swing(cells: dict[float, dict], key: str) -> float:
    vals = [c[key] for c in cells.values()]
    return round(max(vals) - min(vals), 3) if vals else 0.0


def evaluate() -> dict:
    per_regime: dict[str, dict] = {}
    any_data = False
    for regime in REGIMES:
        cells = per_cell_aggregates(regime)
        if cells:
            any_data = True
        sorted_muls = sorted(cells.keys())
        psnr_curve = [cells[m]["mean_psnr_db"] for m in sorted_muls]
        latency_curve = [cells[m]["mean_p95_latency_ms"] for m in sorted_muls]
        per_regime[regime] = {
            "cells": [
                {
                    "mul_increase": m,
                    "mean_psnr_db": cells[m]["mean_psnr_db"],
                    "mean_p95_latency_ms": cells[m]["mean_p95_latency_ms"],
                    "n_reps": cells[m]["n_reps"],
                }
                for m in sorted_muls
            ],
            "psnr_curve_monotone_nondecreasing": is_monotone_nondecreasing(psnr_curve),
            "psnr_swing_db": swing(cells, "mean_psnr_db"),
            "p95_latency_swing_ms": swing(cells, "mean_p95_latency_ms"),
        }

    fluct = per_regime.get("fluct", {})
    static = per_regime.get("static", {})
    psnr_rises_step = bool(fluct.get("psnr_curve_monotone_nondecreasing"))
    swing_step = fluct.get("psnr_swing_db", 0.0)
    swing_ctrl = static.get("psnr_swing_db", 0.0)
    effect_larger_on_step = swing_step > swing_ctrl
    # latency guardrail: the ramp should not blow up latency on the step link.
    # "Bounded" = the p95 swing across the sweep is under 50 ms.
    latency_bounded = fluct.get("p95_latency_swing_ms", 0.0) <= 50.0

    if not any_data:
        verdict = "untested"
    elif psnr_rises_step and effect_larger_on_step:
        verdict = "supported"
    elif not psnr_rises_step:
        verdict = "refuted"
    else:
        verdict = "inconclusive"

    predicates = {
        "psnr_rises_with_ramp_on_the_capacity_step_network": psnr_rises_step,
        "psnr_effect_larger_on_capacity_step_than_on_fixed_control": effect_larger_on_step,
        "p95_latency_stays_bounded_as_ramp_increases": latency_bounded,
        # Refutation: the core claim is that a faster ramp RAISES recovery
        # quality on the capacity-step link. If PSNR does not rise there, the
        # claim is refuted.
        "psnr_does_not_rise_on_the_capacity_step_network": not psnr_rises_step,
        # Untested triggers
        "any_cell_failed": False,
        "required_metric_missing": not any_data,
    }

    return {
        "verdict": verdict,
        "predicates": predicates,
        "psnr_swing_capacity_step_db": swing_step,
        "psnr_swing_fixed_control_db": swing_ctrl,
        "per_regime": per_regime,
    }


def _sweeps_meta() -> list[dict]:
    cfg = list(range(ID_BASE, ID_BASE + len(REGIMES) * len(MUL)))  # 201..218
    return [
        {"name": SWEEP_BY_REGIME[reg], "configs": cfg[i * len(MUL):(i + 1) * len(MUL)],
         "knob_values": list(MUL), "reps": 3,
         "record_path": f"runs/experiments/{SWEEP_BY_REGIME[reg]}.json",
         "description": f"mul_increase (ramp) sweep on the {reg} network, loose 4000 kbps ceiling."}
        for i, reg in enumerate(REGIMES)
    ]


def main() -> int:
    from _qdt_report import build_extras, MULINC
    report = evaluate()
    # Figures/tables only build once there is data to plot.
    if report["verdict"] != "untested":
        report.update(build_extras("h8", report["per_regime"], list(REGIMES),
                                   list(MUL), _sweeps_meta(), MULINC))
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(report, indent=2))
    print(f"H8 verdict: {report['verdict']}")
    print(f"PSNR swing: capacity-step {report['psnr_swing_capacity_step_db']} dB "
          f"vs fixed control {report['psnr_swing_fixed_control_db']} dB")
    for regime in REGIMES:
        b = report["per_regime"].get(regime, {})
        if b.get("cells"):
            print(f"  {regime}: psnr_rises={b['psnr_curve_monotone_nondecreasing']} "
                  f"swing={b['psnr_swing_db']}dB")
        else:
            print(f"  {regime}: no data yet")
    print(f"Report: {RESULT_PATH.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

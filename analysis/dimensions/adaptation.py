"""Adaptation dimension.

Question: How well did the controller match output to network capacity?

Inputs:
  - encoder_target_kbps samples (per-second trajectory)
  - the network spec's camera_steps (resolved via the run's config_id)

For each network phase, we report:
  - mean target during the phase (steady-state behavior)
  - utilization (mean target / phase rate cap)
  - settling time at phase entry (seconds to within 10% of new mean)
  - overshoot at phase entry (peak deviation above the eventual mean)

Settling time captures *how fast* the controller reacts; utilization
captures *how well it uses* the available bandwidth; overshoot captures
*how much it pushes back* on the bottleneck before settling.

These three numbers per phase are how SCReAM and GCC are honestly
compared on the question they're both designed to answer.
"""

from __future__ import annotations

import statistics
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from analysis import _common as C   # noqa: E402


name = "adaptation"

# Settling threshold: phase-mean within X% of the eventual phase-mean
# counts as "settled". 10% is generous enough that ordinary CC noise
# doesn't reset the timer, tight enough to flag a slow ramp.
_SETTLING_REL_TOL = 0.10


def analyze_run(run: C.RunFiles) -> dict:
    camera = C.load_json(run.camera_path)
    metrics = camera.get("metrics") or {}
    samples = (metrics.get("encoder_target_kbps") or {}).get("samples") or []
    # samples = [(elapsed_s, kbps), ...]

    steps = (C.load_camera_steps_for_config(run.config_id)
             if run.config_id else None)

    phases = []
    if steps and samples:
        boundaries = C.phase_boundaries_ns(steps)
        # Convert samples to the same time base (seconds since run start).
        # encoder_target_kbps already records elapsed_s.
        for i, (start_ns, end_ns, label) in enumerate(boundaries):
            phase_samples = [(t, kbps) for t, kbps in samples
                             if start_ns / 1e9 <= t < end_ns / 1e9]
            if not phase_samples:
                continue
            kbps_xs = [kbps for _, kbps in phase_samples]
            cap_kbps = int(steps[i].get("rate_kbps") or 0)
            mean_kbps = statistics.mean(kbps_xs)

            # Settling time: first sample within tol of the eventual phase
            # mean, reported relative to the phase start.
            settling_t_s = None
            tol = _SETTLING_REL_TOL * mean_kbps
            phase_start_s = start_ns / 1e9
            for t, kbps in phase_samples:
                if abs(kbps - mean_kbps) <= tol:
                    settling_t_s = t - phase_start_s
                    break

            # Overshoot: max deviation above mean, normalized by mean.
            overshoot = None
            if kbps_xs:
                peak = max(kbps_xs)
                overshoot = (peak - mean_kbps) / mean_kbps if mean_kbps > 0 else 0

            utilization = (mean_kbps / cap_kbps) if cap_kbps > 0 else None

            phases.append({
                "label":          label,
                "cap_kbps":       cap_kbps,
                "mean_kbps":      mean_kbps,
                "first_kbps":     phase_samples[0][1],
                "last_kbps":      phase_samples[-1][1],
                "utilization":    utilization,
                "settling_t_s":   settling_t_s,
                "overshoot":      overshoot,
                "n_samples":      len(phase_samples),
            })

    return {
        "dimension": name,
        "phases":    phases,
        "samples_total": len(samples),
        "has_steps":  steps is not None,
    }


def analyze_experiment(record: dict) -> dict:
    arms = {c["id"]: c["label"] for c in record["configurations"]}
    per_arm: dict[str, dict] = {}
    for cid, label in arms.items():
        runs = [r for r in record["runs"]
                if r["config"] == cid and r["exit_code"] == 0]
        per_run = []
        for r in runs:
            run_dir = PROJECT_ROOT / "runs" / cid / r["run_id"]
            rep = analyze_run(C.RunFiles(
                run_dir, run_dir / "summary.json",
                run_dir / "camera.json", run_dir / "viewer.json", cid,
            ))
            per_run.append(rep)
        # Aggregate per-phase across reps. We assume phase count is
        # constant within an arm (every run uses the same config →
        # same network spec). Drop reps where adaptation samples are
        # absent (encoder_target_kbps disabled).
        phases_by_idx: dict[int, dict[str, list]] = {}
        for rep in per_run:
            for i, phase in enumerate(rep["phases"]):
                d = phases_by_idx.setdefault(i, {
                    "label": phase["label"],
                    "cap_kbps": phase["cap_kbps"],
                    "mean_xs": [], "util_xs": [],
                    "settling_xs": [], "overshoot_xs": [],
                })
                d["mean_xs"].append(phase["mean_kbps"])
                if phase["utilization"] is not None:
                    d["util_xs"].append(phase["utilization"])
                if phase["settling_t_s"] is not None:
                    d["settling_xs"].append(phase["settling_t_s"])
                if phase["overshoot"] is not None:
                    d["overshoot_xs"].append(phase["overshoot"])
        per_arm[cid] = {
            "label":  label,
            "n":      len(runs),
            "phases": [phases_by_idx[i] for i in sorted(phases_by_idx)],
        }
    return {"dimension": name, "per_arm": per_arm}


def render(report: dict) -> str:
    if "per_arm" in report:
        return _render_experiment(report)
    return _render_run(report)


def _render_run(report: dict) -> str:
    lines = ["[adaptation] How well did the controller match output to capacity?"]
    if not report["phases"]:
        if report["samples_total"] == 0:
            lines.append("  encoder_target_kbps was not enabled for this run.")
        elif not report["has_steps"]:
            lines.append("  config has no network spec (loopback?); "
                         "phase analysis skipped.")
        else:
            lines.append("  no samples landed in any phase window.")
        return "\n".join(lines)
    lines.append(f"  {'phase':<32} {'cap':>8} {'mean':>10} "
                 f"{'util':>7} {'settle':>9} {'over':>7}")
    for p in report["phases"]:
        cap = f"{p['cap_kbps']}" if p["cap_kbps"] else "—"
        util = f"{p['utilization']*100:.0f}%" if p["utilization"] is not None else "—"
        settle = f"{p['settling_t_s']:.1f}s" if p["settling_t_s"] is not None else "—"
        over = f"{p['overshoot']*100:+.0f}%" if p["overshoot"] is not None else "—"
        lines.append(f"  {p['label'][:32]:<32} {cap:>8} "
                     f"{p['mean_kbps']:>10.1f} {util:>7} "
                     f"{settle:>9} {over:>7}")
    return "\n".join(lines)


def _render_experiment(report: dict) -> str:
    lines = ["[adaptation] How well did the controller match output to capacity?"]
    if not any(arm["phases"] for arm in report["per_arm"].values()):
        lines.append("  encoder_target_kbps was not enabled for any arm "
                     "(or no phase data resolved).")
        return "\n".join(lines)
    # Per-arm × per-phase grid. We list one row per (arm, phase).
    lines.append(f"  {'arm':<14} {'phase':<32} {'cap':>7} "
                 f"{'mean µ±σ':>14} {'util µ':>7} {'settle µ':>10}")
    for cid, arm in report["per_arm"].items():
        for p in arm["phases"]:
            cap = f"{p['cap_kbps']}" if p["cap_kbps"] else "—"
            util = f"{statistics.mean(p['util_xs'])*100:.0f}%" if p["util_xs"] else "—"
            settle = f"{statistics.mean(p['settling_xs']):.1f}s" if p["settling_xs"] else "—"
            mean_s = C.msd(p["mean_xs"], "{:.0f}")
            lines.append(f"  {arm['label']:<14} {p['label'][:32]:<32} "
                         f"{cap:>7} {mean_s:>14} {util:>7} {settle:>10}")
    return "\n".join(lines)

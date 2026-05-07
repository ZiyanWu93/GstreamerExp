"""Stability dimension.

Question: How predictably did the pipeline behave over time?

Stability is what the other dimensions look like *over time*: how much
variance in encoder rate within a phase (after settling), how much
PSNR jitter within a phase, how the run-to-run reps within an
experiment cluster vs spread.

Distinct from quality (mean PSNR) and adaptation (mean utilization)
because two pipelines could have identical means but very different
variability — a teleop pipeline that occasionally drops to 10 dB PSNR
is operationally worse than one that holds steady at 35 dB even if
their averages match. Stability captures that.
"""

from __future__ import annotations

import statistics
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from analysis import _common as C   # noqa: E402


name = "stability"


def analyze_run(run: C.RunFiles) -> dict:
    camera = C.load_json(run.camera_path)
    viewer = C.load_json(run.viewer_path)

    # Encoder-rate variability — stddev across all encoder_target_kbps
    # samples. A steadier controller has lower stddev relative to the
    # mean (coefficient of variation).
    cam_metrics = camera.get("metrics") or {}
    target_samples = (cam_metrics.get("encoder_target_kbps") or {}).get("samples") or []
    target_kbps_xs = [kbps for _, kbps in target_samples]
    enc_cv = None
    if len(target_kbps_xs) > 1:
        m = statistics.mean(target_kbps_xs)
        s = statistics.stdev(target_kbps_xs)
        enc_cv = (s / m) if m > 0 else None

    # PSNR jitter — stddev of decoded PSNR values across the run. A
    # high-mean-but-high-jitter run is operationally less acceptable
    # than a steady one.
    viewer_metrics = viewer.get("metrics") or {}
    psnr_samples = (viewer_metrics.get("decoded_psnr") or {}).get("samples") or []
    psnr_xs = [db for _, _, db in psnr_samples]
    psnr_stddev = statistics.stdev(psnr_xs) if len(psnr_xs) > 1 else None
    psnr_mean = statistics.mean(psnr_xs) if psnr_xs else None

    return {
        "dimension":    name,
        "encoder_cv":   enc_cv,         # coefficient of variation
        "encoder_mean": statistics.mean(target_kbps_xs) if target_kbps_xs else None,
        "encoder_stddev": statistics.stdev(target_kbps_xs) if len(target_kbps_xs) > 1 else None,
        "psnr_stddev":  psnr_stddev,
        "psnr_mean":    psnr_mean,
        "psnr_enabled": bool(psnr_xs),
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

        # Per-arm reproducibility: how clustered are the per-rep means?
        # Ideal pipeline: same spec, same network, same recovery →
        # nearly identical results. High stddev across reps signals
        # the pipeline's behavior is hard to predict run-to-run.
        rep_psnr_means = [r["psnr_mean"] for r in per_run
                          if r["psnr_mean"] is not None]
        rep_encoder_means = [r["encoder_mean"] for r in per_run
                             if r["encoder_mean"] is not None]
        per_arm[cid] = {
            "label":     label,
            "n":         len(runs),
            "encoder_cv_xs":   [r["encoder_cv"] for r in per_run
                                 if r["encoder_cv"] is not None],
            "psnr_stddev_xs":  [r["psnr_stddev"] for r in per_run
                                 if r["psnr_stddev"] is not None],
            "rep_psnr_means":  rep_psnr_means,
            "rep_encoder_means": rep_encoder_means,
        }
    return {"dimension": name, "per_arm": per_arm}


def render(report: dict) -> str:
    if "per_arm" in report:
        return _render_experiment(report)
    return _render_run(report)


def _render_run(report: dict) -> str:
    lines = ["[stability] How predictably did the pipeline behave over time?"]
    if report["encoder_mean"] is None:
        lines.append("  encoder_target_kbps was not enabled; encoder "
                     "stability cannot be computed.")
    else:
        cv = report["encoder_cv"]
        cv_s = f"{cv*100:.1f}%" if cv is not None else "—"
        lines.append(f"  encoder rate  mean={report['encoder_mean']:.1f}kbps  "
                     f"stddev={report['encoder_stddev']:.1f}kbps  cv={cv_s}")
    if report["psnr_enabled"]:
        lines.append(f"  psnr jitter   stddev={C.fmt(report['psnr_stddev'], precision=2)}dB "
                     f"(mean={C.fmt(report['psnr_mean'], precision=1)}dB)")
    else:
        lines.append("  decoded_psnr was not enabled; PSNR jitter "
                     "cannot be computed.")
    return "\n".join(lines)


def _render_experiment(report: dict) -> str:
    lines = ["[stability] How predictably did the pipeline behave over time?"]
    lines.append(f"  {'arm':<14} {'n':>3} "
                 f"{'enc_cv µ':>10} "
                 f"{'psnr_jitter µ':>15} "
                 f"{'rep_mean σ (psnr)':>20} "
                 f"{'rep_mean σ (enc)':>18}")
    for cid, arm in report["per_arm"].items():
        cv_means = arm["encoder_cv_xs"]
        cv_avg = f"{statistics.mean(cv_means)*100:.1f}%" if cv_means else "—"
        jitter_means = arm["psnr_stddev_xs"]
        jitter_avg = f"{statistics.mean(jitter_means):.2f}dB" if jitter_means else "—"
        psnr_rep_sd = (f"{statistics.stdev(arm['rep_psnr_means']):.2f}dB"
                       if len(arm["rep_psnr_means"]) > 1 else "—")
        enc_rep_sd = (f"{statistics.stdev(arm['rep_encoder_means']):.0f}kbps"
                      if len(arm["rep_encoder_means"]) > 1 else "—")
        lines.append(f"  {arm['label']:<14} {arm['n']:>3} "
                     f"{cv_avg:>10} {jitter_avg:>15} "
                     f"{psnr_rep_sd:>20} {enc_rep_sd:>18}")
    return "\n".join(lines)

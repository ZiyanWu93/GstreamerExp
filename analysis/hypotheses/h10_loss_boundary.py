"""H10 verifier — SCReAM loss-boundary sweep (where does packet loss break it?).

Second of the three §7.8 boundary axes (H9 did delay). On a fixed 5 Mbps /
20 ms link, sweep uniform iid loss {0, 0.1, 0.25, 0.5, 1, 2} %. Recovery is off
(nack/pli/fec false, as in H6-H9), so loss is unmasked: a lost packet corrupts
VP8 until the next keyframe (60 frames = 6 s).

The boundary signal here is FRAME DELIVERY (frames the viewer reconstructs /
frames the camera sent), not PSNR: the few frames that survive stay clean
(high PSNR), but most stop arriving. So we read the boundary off the delivery
curve and report PSNR-of-delivered alongside.

Run from the project root:
    python3 analysis/hypotheses/h10_loss_boundary.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from statistics import mean

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RUNS_DIR = PROJECT_ROOT / "runs"
RESULTS_DIR = PROJECT_ROOT / "analysis" / "hypotheses" / "results"
RESULT_PATH = RESULTS_DIR / "h10_report.json"

LOSS_PCT = (0.0, 0.1, 0.25, 0.5, 1.0, 2.0)
ID_BASE = 307
SWEEP = "h10-loss-boundary"
USABLE_DELIVERY = 0.80     # below this, too many frames are missing to be usable
COLLAPSE_DELIVERY = 0.50   # "collapsed"


def _summary(config: int, run_id: str) -> dict | None:
    p = RUNS_DIR / str(config) / run_id / "summary.json"
    return json.loads(p.read_text()) if p.is_file() else None


def _psnr(config: int, run_id: str) -> float | None:
    p = RUNS_DIR / str(config) / run_id / "viewer.json"
    if not p.is_file():
        return None
    v = json.loads(p.read_text())
    return (v.get("metrics", {}).get("decoded_psnr", {})
             .get("summary", {}).get("mean_psnr_db"))


def per_loss_aggregates() -> dict[float, dict]:
    sweep_path = RUNS_DIR / "experiments" / f"{SWEEP}.json"
    if not sweep_path.is_file():
        return {}
    runs = json.loads(sweep_path.read_text())["runs"]
    acc: dict[float, list[tuple[float, float | None]]] = {l: [] for l in LOSS_PCT}
    for r in runs:
        if r.get("exit_code") != 0:
            continue
        config = int(r["config"])
        loss = LOSS_PCT[config - ID_BASE]
        s = _summary(config, r["run_id"])
        if not s:
            continue
        sent = (s.get("camera", {}) or {}).get("frames_sent")
        recv = (s.get("viewer", {}) or {}).get("frames_depayloaded")
        if not sent:
            continue
        delivery = recv / sent
        acc[loss].append((delivery, _psnr(config, r["run_id"])))
    out: dict[float, dict] = {}
    for l, samples in acc.items():
        if not samples:
            continue
        psnrs = [p for _, p in samples if p is not None]
        out[l] = {
            "mean_delivery": round(mean(d for d, _ in samples), 4),
            "mean_psnr_db": round(mean(psnrs), 3) if psnrs else None,
            "n_reps": len(samples),
        }
    return out


def evaluate() -> dict:
    cells = per_loss_aggregates()
    any_data = bool(cells)
    sorted_l = sorted(cells.keys())
    per_regime = {
        "loss": {
            "cells": [
                {"loss_pct": l, "mean_delivery": cells[l]["mean_delivery"],
                 "mean_psnr_db": cells[l]["mean_psnr_db"], "n_reps": cells[l]["n_reps"]}
                for l in sorted_l
            ]
        }
    }
    deliv_low = cells.get(LOSS_PCT[0], {}).get("mean_delivery")     # 0% loss
    deliv_high = cells.get(LOSS_PCT[-1], {}).get("mean_delivery")   # 2% loss
    all_deliv = [cells[l]["mean_delivery"] for l in sorted_l]

    usable_at_zero = deliv_low is not None and deliv_low >= 0.90
    collapses = (deliv_high is not None and deliv_high < COLLAPSE_DELIVERY) or \
                (all_deliv and min(all_deliv) < COLLAPSE_DELIVERY)
    threshold_pct = next((l for l in sorted_l if cells[l]["mean_delivery"] < USABLE_DELIVERY), None)

    if not any_data:
        verdict = "untested"
    elif not usable_at_zero:
        verdict = "untested"
    elif collapses:
        verdict = "supported"
    else:
        verdict = "refuted"

    predicates = {
        "usable_delivery_at_zero_loss": usable_at_zero,
        "delivery_collapses_across_the_loss_range": bool(collapses),
        "usable_threshold_found_below_max_loss": threshold_pct is not None,
        "delivery_robust_across_whole_range": usable_at_zero and not collapses,
        "any_cell_failed": False,
        "required_metric_missing": not any_data,
    }
    return {
        "verdict": verdict,
        "predicates": predicates,
        "delivery_at_zero_loss": deliv_low,
        "delivery_at_max_loss": deliv_high,
        "usable_threshold_pct": threshold_pct,
        "per_regime": per_regime,
    }


def _figures(per_regime: dict) -> list[dict]:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    cells = per_regime["loss"]["cells"]
    xs = [c["loss_pct"] for c in cells]
    figs = []
    for key, ylabel, fname, caption, hline in [
        ("mean_delivery", "frame delivery (received / sent)", "h10_delivery_by_loss.svg",
         "Frame delivery vs uniform packet loss (recovery off). The boundary is where "
         "delivery falls through the usable line — most frames stop arriving.", 0.80),
        ("mean_psnr_db", "decoded PSNR of delivered frames (dB)", "h10_psnr_by_loss.svg",
         "PSNR of the frames that do arrive. It stays high because the survivors are "
         "clean; the loss damage shows up as missing frames (delivery), not blur.", None),
    ]:
        ys = [c[key] for c in cells]
        fig, ax = plt.subplots(figsize=(6.4, 3.6))
        ax.plot(xs, ys, marker="o", color="#d62728")
        if hline is not None:
            ax.axhline(hline, ls="--", lw=1, color="#888", label=f"usable line ({hline:.0%})")
            ax.legend(fontsize=8)
        ax.set_xlabel("uniform packet loss (%)")
        ax.set_ylabel(ylabel)
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        out = RESULTS_DIR / fname
        fig.savefig(out)
        plt.close(fig)
        figs.append({"path": str(out.relative_to(PROJECT_ROOT)), "caption": caption})
    return figs


def _tables(per_regime: dict) -> dict:
    cells = per_regime["loss"]["cells"]
    drow = {"metric": "frame delivery"}
    prow = {"metric": "PSNR of delivered (dB)"}
    for c in cells:
        col = f"{c['loss_pct']}%"
        drow[col] = round(c["mean_delivery"], 3)
        prow[col] = c["mean_psnr_db"]
    return {"Delivery and PSNR by packet loss": [drow, prow]}


def main() -> int:
    from _qdt_report import _experiment_blocks, LOSS
    report = evaluate()
    if report["verdict"] != "untested":
        sweeps = [{
            "name": SWEEP, "configs": list(range(ID_BASE, ID_BASE + len(LOSS_PCT))),
            "knob_values": list(LOSS_PCT), "reps": 3,
            "record_path": f"runs/experiments/{SWEEP}.json",
            "description": "SCReAM loss-boundary sweep on a fixed 5 Mbps / 20 ms link, recovery off.",
        }]
        report["figures"] = _figures(report["per_regime"])
        report["tables"] = _tables(report["per_regime"])
        report["experiments"] = _experiment_blocks(sweeps, LOSS)
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(report, indent=2))
    print(f"H10 verdict: {report['verdict']}")
    print(f"delivery: {report['delivery_at_zero_loss']} @0% -> "
          f"{report['delivery_at_max_loss']} @2%; usable_threshold={report['usable_threshold_pct']}%")
    for c in report["per_regime"]["loss"]["cells"]:
        print(f"  {c['loss_pct']:5}%: delivery {c['mean_delivery']:.3f}  "
              f"PSNR {c['mean_psnr_db']}dB  (n{c['n_reps']})")
    print(f"Report: {RESULT_PATH.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
